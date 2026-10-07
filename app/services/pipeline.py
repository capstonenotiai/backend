"""
수집 파이프라인: 크롤러 실행 → notices 저장 → 추출 → events 저장.

크롤러는 이 repo 의 crawler/ (capstonenotiai/model repo 에서 옮겨 옴) 를 그대로 사용한다.
runner 는 data/crawled_all.jsonl 에 누적 저장하므로, 여기서는 source_url 로 중복을 걸러 새 것만 넣는다.
"""
import json
import logging
import subprocess
import sys
import inspect
import httpx
from dataclasses import asdict
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CrawlRun, Event, Notice, utcnow
from app.schemas import SOURCE_IDS
from app.services.category import categorize
from app.services.extractor import ExtractionResult, Extractor, ModelUnavailable, RetryableExtraction, get_extractor
from app.services.notice_metadata import iso_publication, publication_from_metadata, application_deadline
from app.timeutil import to_local

log = logging.getLogger(__name__)


def import_records(db: Session, records: Iterable[dict]) -> Counter:
    """크롤러 레코드({source_url, site, title_raw, raw_text, board?}) → notices. 사이트별 새 건수 반환"""
    added: Counter = Counter()
    seen = set(db.scalars(select(Notice.source_url)))
    for record in records:
        url = record.get("source_url")
        site = record.get("site")
        if not url or not site or url in seen:
            continue
        if len(url) > 700:  # models.Notice.source_url 길이 제한
            log.warning("URL 이 너무 길어 건너뜀: %s...", url[:80])
            continue
        seen.add(url)
        meta = record.get('meta') if isinstance(record.get('meta'), dict) else {}
        published_at = iso_publication(record.get('published_at')) or publication_from_metadata(meta)
        if not published_at and site == 'cbnu':
            published_at = iso_publication(record.get('list_date_raw'))
        db.add(
            Notice(
                site=site,
                board=record.get("board"),
                source_url=url,
                title_raw=(record.get("title_raw") or "").strip(),
                raw_text=record.get("raw_text") or "",
                published_at=published_at, source_metadata=meta,
                application_end_date=application_deadline(meta),
            )
        )
        added[site] += 1
    db.commit()
    return added


def read_jsonl(path: str | Path) -> list[dict]:
    records = []
    # utf-8-sig: Windows 에서 BOM 이 붙은 파일도 읽히도록
    with open(path, encoding="utf-8-sig") as file:
        for line_no, line in enumerate(file, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("%s:%d JSON 파싱 실패 — 건너뜀", path, line_no)
    return records


# 공지 탓일 수 있는 실패(시간 초과, 5xx)의 재시도 간격(분). 시도 횟수 n 번째 실패 후 RETRY_DELAYS[n-1]
RETRY_DELAYS = (10, 30, 120)
# 마지막 모델 서버 확인 결과 (/api/health 표시용, 프로세스 메모리)
MODEL_STATUS: dict = {"available": None, "checked_at": None}


def _mark_model(available: bool) -> None:
    MODEL_STATUS.update(available=available, checked_at=utcnow())


def extract_pending(db: Session, extractor: Extractor | None = None, limit: int | None = None,
                    now: datetime | None = None) -> int:
    """
    추출 대기열(pending / retry_pending) 처리. 처리 건수 반환.

    모델 서버가 꺼져 있거나 사용 중이면 시도 횟수를 쓰지 않고 다음 실행으로 미룬다.
    """
    extractor = extractor or get_extractor()
    now = now or utcnow()
    states = ('pending', 'retry_pending')
    maximum = max(1, get_settings().extraction_max_attempts)
    query = select(Notice).where(Notice.extraction_state.in_(states),
        Notice.extraction_attempts < maximum, ~Notice.events.any(),
        or_(Notice.next_attempt_at.is_(None), Notice.next_attempt_at <= now)).order_by(Notice.id)
    if limit:
        query = query.limit(limit)
    notices = db.scalars(query).all()
    if not notices:
        return 0
    if hasattr(extractor, 'is_available'):
        available = extractor.is_available()
        _mark_model(available)
        if not available:
            log.info("모델 서버 응답 없음: 추출 %d건 대기", len(notices))
            return 0

    count = 0
    for notice in notices:
        previous = notice.extraction_state
        claimed=db.execute(update(Notice).where(Notice.id==notice.id,Notice.extraction_state.in_(states),
                Notice.extraction_attempts < maximum)
            .values(extraction_state='processing', extraction_attempts=Notice.extraction_attempts+1))
        db.commit()
        if claimed.rowcount!=1: continue
        try:
            # Legacy injectable extractors are still usable without altering their two-argument API.
            if 'reference_time' in inspect.signature(extractor.extract).parameters:
                result = extractor.extract(notice.title_raw, notice.raw_text, reference_time=notice.published_at)
            else:
                result = extractor.extract(notice.title_raw, notice.raw_text)
            if not isinstance(result,ExtractionResult):
                result=ExtractionResult([result],{'legacy_prediction':asdict(result)})
            notice.extraction_result=result.raw
            for extracted in result.events:
                if extractor.name == 'model_api':
                    extracted.review_status = 'auto'
                    if (extracted.event_type in ('application', 'submission') and notice.application_end_date
                            and extracted.end_date != notice.application_end_date):
                        reason = '사이트 접수 마감일과 AI 마감일이 다릅니다. 원문 확인이 필요합니다.'
                        extracted.review_reason = ' / '.join(filter(None, [extracted.review_reason, reason]))
                db.add(Event(notice_id=notice.id,source=notice.site,source_url=notice.source_url,
                    **asdict(extracted),category=categorize(notice.site,notice.board),
                    extractor=extractor.name,collected_at=notice.crawled_at,
                    ai_extracted=extractor.name in ('model_api', 'gpt')))
            # Store empty success separately; it is not an unprocessed notice.
            notice.extraction_state = ('no_events' if not result.events else
                'needs_review' if any(e.review_status=='needs_review' for e in result.events) else 'extracted')
            notice.extraction_error=None
            notice.next_attempt_at=None
            notice.revision+=1
            db.commit()
            if hasattr(extractor, 'is_available'):
                _mark_model(True)
        except Exception as error:  # noqa: BLE001 — 한 건 실패로 전체를 멈추지 않음
            db.rollback()
            if isinstance(error, (ModelUnavailable, httpx.ConnectError)):
                # 공지 탓이 아니므로 시도 횟수를 되돌리고, 남은 공지도 다음 실행으로 미룬다
                notice.extraction_attempts -= 1
                notice.extraction_state = previous
                notice.extraction_error = '모델 서버 연결 안 됨: 추출 대기'
                db.commit()
                _mark_model(False)
                log.info("모델 서버 응답 없음: 이번 추출 중단 notice=%s", notice.id)
                break
            retryable = isinstance(error, (RetryableExtraction, httpx.TransportError))
            notice.extraction_state = 'retry_pending' if retryable and notice.extraction_attempts < maximum else 'failed'
            if notice.extraction_state == 'retry_pending':
                delay = RETRY_DELAYS[min(notice.extraction_attempts, len(RETRY_DELAYS)) - 1]
                notice.next_attempt_at = now + timedelta(minutes=delay)
            notice.extraction_error = ('모델 연결 실패: 재시도 대기' if notice.extraction_state == 'retry_pending'
                else '추출 실패: ' + type(error).__name__)
            if hasattr(error,'raw'): notice.extraction_result=error.raw
            db.commit()
            log.warning("추출 실패 notice=%s: %s", notice.id, type(error).__name__)
            continue
        count += 1
    return count


def requeue_failed(db: Session) -> int:
    """failed 공지(일정 없음)를 시도 횟수 0 으로 대기열에 다시 넣는다 (CLI 수동 실행용)"""
    result = db.execute(update(Notice).where(Notice.extraction_state == 'failed', ~Notice.events.any())
        .values(extraction_state='retry_pending', extraction_attempts=0, next_attempt_at=None),
        execution_options={'synchronize_session': False})
    db.commit()
    return result.rowcount


def queue_status(db: Session) -> dict:
    """추출 대기열 현황 (/api/health)"""
    counts = dict(db.execute(select(Notice.extraction_state, func.count())
        .where(Notice.extraction_state.in_(('pending', 'retry_pending', 'processing', 'failed')))
        .group_by(Notice.extraction_state)).all())
    checked = MODEL_STATUS["checked_at"]
    return {
        "pending": counts.get('pending', 0), "retryPending": counts.get('retry_pending', 0),
        "processing": counts.get('processing', 0), "failed": counts.get('failed', 0),
        "modelAvailable": MODEL_STATUS["available"],
        "modelCheckedAt": to_local(checked).isoformat(timespec="seconds") if checked else None,
    }


BACKEND_ROOT = Path(__file__).resolve().parents[2]
CRAWLED_FILE = BACKEND_ROOT / "data" / "crawled_all.jsonl"
# crawler/runner.py 가 남기는 사이트별 결과 {site: {status, new, failed_requests, error}}
SUMMARY_FILE = BACKEND_ROOT / "data" / "last_run.json"


def run_crawler(site: str | None = None) -> tuple[int, dict]:
    """crawler/runner.py 실행. (exit code, 사이트별 결과) 반환 — 실패해도 예외를 던지지 않음"""
    SUMMARY_FILE.unlink(missing_ok=True)  # 이전 실행 결과를 이번 것으로 착각하지 않도록
    command = [sys.executable, "crawler/runner.py", "--max", str(get_settings().crawl_max_pages)]
    if site:
        command += ["--site", site]
    returncode = subprocess.run(command, cwd=BACKEND_ROOT, timeout=60 * 60).returncode
    try:
        summary = json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary = {}
    return returncode, summary


def _run_message(result: dict) -> str | None:
    """사이트 결과 → 수집 기록 message (실패 사유 / 경고)"""
    parts = []
    if result.get("error"):
        parts.append(result["error"])
    failed = result.get("failed_requests") or []
    if failed:
        parts.append(f"요청 실패 {len(failed)}건: " + " / ".join(failed[:3]))
    return "\n".join(parts) or None


def mark_interrupted_runs(db: Session) -> int:
    """
    서버 시작 시 호출. 수집 도중 서버가 꺼져 'running' 으로 남은 기록을 실패로 정리
    (그대로 두면 대시보드에 계속 '수집 중'으로 보임)
    """
    interrupted = db.scalars(select(Notice).where(Notice.extraction_state=='processing')).all()
    for notice in interrupted:
        notice.extraction_state = 'retry_pending' if notice.extraction_attempts < max(1, get_settings().extraction_max_attempts) else 'failed'
        notice.extraction_error = '서버 중단: 다음 추출 실행에서 재시도'
    runs = db.scalars(select(CrawlRun).where(CrawlRun.status == "running")).all()
    for run in runs:
        run.status, run.finished_at, run.message = "failed", utcnow(), "서버 재시작으로 수집이 중단됨"
    db.commit()
    return len(runs)


def collect(db: Session, site: str | None = None, extractor: Extractor | None = None) -> dict:
    """크롤 → import → 추출 전체 실행 (스케줄러 / CLI 에서 호출)"""
    sites = [site] if site else list(SOURCE_IDS)
    runs = {name: CrawlRun(site=name, status="running") for name in sites}
    db.add_all(runs.values())
    db.commit()

    try:
        returncode, summary = run_crawler(site)
        # 일부 사이트가 실패해도 성공한 사이트 결과는 저장
        added = import_records(db, read_jsonl(CRAWLED_FILE)) if CRAWLED_FILE.exists() else Counter()
    except Exception as error:
        for run in runs.values():
            run.status, run.finished_at, run.message = "failed", utcnow(), f"{error.__class__.__name__}: {error}"[:500]
        db.commit()
        raise

    for name, run in runs.items():
        result = summary.get(name)
        run.finished_at, run.new_count = utcnow(), added.get(name, 0)
        if result is None:
            run.status, run.message = "failed", f"크롤러 결과 없음 (exit code {returncode})"
        else:
            run.status, run.message = result["status"], _run_message(result)
        if run.status == "failed":
            log.warning("수집 실패 %s: %s", name, run.message)
    db.commit()

    extracted = extract_pending(db, extractor)
    return {
        "imported": dict(added),
        "extracted": extracted,
        "failed": [name for name, run in runs.items() if run.status == "failed"],
    }
