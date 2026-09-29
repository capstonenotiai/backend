"""
수집 파이프라인: 크롤러 실행 → notices 저장 → 추출 → events 저장.

크롤러는 capstonenotiai/model repo 의 crawler/runner.py 를 그대로 사용한다.
runner 는 data/crawled_all.jsonl 에 누적 저장하므로, 여기서는 source_url 로 중복을 걸러 새 것만 넣는다.
"""
import json
import logging
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CrawlRun, Event, Notice, utcnow
from app.schemas import SOURCE_IDS
from app.services.category import categorize
from app.services.extractor import Extractor, get_extractor

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
    query = select(Notice).where(~Notice.event.has()).order_by(Notice.id)
    if limit:
        query = query.limit(limit)

    count = 0
    for notice in db.scalars(query).all():
        try:
            result = extractor.extract(notice.title_raw, notice.raw_text)
        except Exception as error:  # noqa: BLE001 — 한 건 실패로 전체를 멈추지 않음
            log.warning("추출 실패 notice=%s: %s", notice.id, error)
            continue
        db.add(
            Event(
                notice_id=notice.id,
                source=notice.site,
                source_url=notice.source_url,
                title=result.title or notice.title_raw,
                start_date=result.start_date,
                end_date=result.end_date,
                location=result.location,
                detail=result.detail,
                category=categorize(notice.site, notice.board, notice.title_raw),
                review_status=result.review_status,
                review_reason=result.review_reason,
                extractor=extractor.name,
                collected_at=notice.crawled_at,
            )
        )
        db.commit()
        count += 1
    return count


def run_crawler(site: str | None = None) -> Path:
    """model repo 크롤러 실행 후 결과 파일 경로 반환"""
    settings = get_settings()
    if not settings.model_repo_path:
        raise RuntimeError("MODEL_REPO_PATH 가 설정되지 않았습니다.")
    repo = Path(settings.model_repo_path)
    command = [sys.executable, "crawler/runner.py", "--max", str(settings.crawl_max_pages)]
    if site:
        command += ["--site", site]
    subprocess.run(command, cwd=repo, check=True, timeout=60 * 60)
    return repo / "data" / "crawled_all.jsonl"


def collect(db: Session, site: str | None = None, extractor: Extractor | None = None) -> dict:
    """크롤 → import → 추출 전체 실행 (스케줄러 / CLI 에서 호출)"""
    sites = [site] if site else list(SOURCE_IDS)
    runs = {name: CrawlRun(site=name, status="running") for name in sites}
    db.add_all(runs.values())
    db.commit()

    try:
        output = run_crawler(site)
        added = import_records(db, read_jsonl(output))
    except Exception:
        for run in runs.values():
            run.status, run.finished_at = "failed", utcnow()
        db.commit()
        raise

    for name, run in runs.items():
        run.status, run.finished_at, run.new_count = "done", utcnow(), added.get(name, 0)
    db.commit()

    extracted = extract_pending(db, extractor)
    return {"imported": dict(added), "extracted": extracted}
