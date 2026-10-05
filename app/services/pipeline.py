"""
수집 파이프라인: 크롤러 실행 → notices 저장 → 추출 → events 저장.

크롤러는 이 repo 의 crawler/ (capstonenotiai/model repo 에서 옮겨 옴) 를 그대로 사용한다.
runner 는 data/crawled_all.jsonl 에 누적 저장하므로, 여기서는 source_url 로 중복을 걸러 새 것만 넣는다.
"""
import json
import logging
import subprocess
import sys
from dataclasses import asdict
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CrawlRun, Event, Notice, utcnow
from app.schemas import SOURCE_IDS
from app.services.category import categorize
from app.services.extractor import ExtractionResult, Extractor, get_extractor

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
        db.add(
            Notice(
                site=site,
                board=record.get("board"),
                source_url=url,
                title_raw=(record.get("title_raw") or "").strip(),
                raw_text=record.get("raw_text") or "",
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


def extract_pending(db: Session, extractor: Extractor | None = None, limit: int | None = None) -> int:
    """아직 event 가 없는 notice 를 추출해 events 로 저장. 처리 건수 반환"""
    extractor = extractor or get_extractor()
    query = select(Notice).where(Notice.extraction_state=='pending',~Notice.events.any()).order_by(Notice.id)
    if limit:
        query = query.limit(limit)

    count = 0
    for notice in db.scalars(query).all():
        claimed=db.execute(update(Notice).where(Notice.id==notice.id,Notice.extraction_state=='pending')
            .values(extraction_state='processing'))
        db.commit()
        if claimed.rowcount!=1: continue
        try:
            result = extractor.extract(notice.title_raw, notice.raw_text)
            if not isinstance(result,ExtractionResult):
                result=ExtractionResult([result],{'legacy_prediction':asdict(result)})
            notice.extraction_result=result.raw
            for extracted in result.events:
                db.add(Event(notice_id=notice.id,source=notice.site,source_url=notice.source_url,
                    **asdict(extracted),category=categorize(notice.site,notice.board),
                    extractor=extractor.name,collected_at=notice.crawled_at))
            # Store empty success separately; it is not an unprocessed notice.
            notice.extraction_state='needs_review' if not result.events or any(
                e.review_status=='needs_review' for e in result.events) else 'extracted'
            notice.extraction_error=None
            notice.revision+=1
            db.commit()
        except Exception as error:  # noqa: BLE001 — 한 건 실패로 전체를 멈추지 않음
            db.rollback()
            notice.extraction_state='failed'
            notice.extraction_error=f'{type(error).__name__}: {str(error)[:500]}'
            if hasattr(error,'raw'): notice.extraction_result=error.raw
            db.commit()
            log.warning("추출 실패 notice=%s: %s", notice.id, error)
            continue
        count += 1
    return count


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
    db.execute(update(Notice).where(Notice.extraction_state=='processing').values(
        extraction_state='failed',extraction_error='서버 중단: 원문과 추출 상태 확인 필요'))
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
