"""대시보드 수집 현황 — 프론트 data/mockDashboard.js 형태"""
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CrawlRun, Notice
from app.schemas import SOURCE_IDS, DashboardSummary, SourceSummary
from app.timeutil import now_local, to_local


def _count_notices(db: Session, start: datetime, end: datetime) -> dict[str, int]:
    # DB 에는 UTC 로 저장되므로 비교 값도 UTC 로 (SQLite 는 tz 변환을 하지 않음)
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    rows = db.execute(
        select(Notice.site, func.count())
        .where(Notice.crawled_at >= start, Notice.crawled_at < end)
        .group_by(Notice.site)
    ).all()
    return {site: count for site, count in rows}


def _hhmm(value: datetime | None) -> str:
    return to_local(value).strftime("%H:%M") if value else "-"


def _greeting(hour: int) -> str:
    if 5 <= hour < 12:
        return "좋은 아침이에요"
    if 12 <= hour < 18:
        return "좋은 오후예요"
    return "좋은 저녁이에요"


def get_summary(db: Session) -> DashboardSummary:
    now = now_local()
    today_start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    today = _count_notices(db, today_start, today_start + timedelta(days=1))
    yesterday = _count_notices(db, today_start - timedelta(days=1), today_start)

    total_today = sum(today.values())
    diff = total_today - sum(yesterday.values())

    sources = []
    for site in SOURCE_IDS:
        latest = db.scalar(select(CrawlRun).where(CrawlRun.site == site).order_by(CrawlRun.started_at.desc()))
        count = today.get(site, 0)
        sources.append(
            SourceSummary(
                source=site,
                count=count,
                progress=round(count / total_today * 100) if total_today else 0,
                # 한 번도 수집하지 않은 사이트는 none (프론트: '기록 없음')
                status=latest.status if latest else "none",
            )
        )

    last_run = db.scalar(select(CrawlRun).order_by(CrawlRun.started_at.desc()))
    last_finished = db.scalar(
        select(CrawlRun).where(CrawlRun.finished_at.is_not(None)).order_by(CrawlRun.finished_at.desc())
    )
    ampm = "오전" if now.hour < 12 else "오후"

    return DashboardSummary(
        collectedToday=total_today,
        collectedChangeLabel=f"{diff:+d} vs 어제",
        lastCollectedAt=_hhmm(last_run.started_at if last_run else None),
        collectionFinishedAt=_hhmm(last_finished.finished_at if last_finished else None),
        referenceTimeLabel=f"현재 {ampm} {now.hour % 12 or 12:02d}:{now.minute:02d} 기준",
        greeting=_greeting(now.hour),
        sources=sources,
    )
