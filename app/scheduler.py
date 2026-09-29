"""CRAWL_ENABLED=true 이면 서버 실행 중 CRAWL_CRON 마다 수집 파이프라인 실행"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.services.pipeline import collect

log = logging.getLogger(__name__)


def run_collect_job() -> None:
    db = SessionLocal()
    try:
        log.info("수집 시작")
        log.info("수집 완료: %s", collect(db))
    except Exception:
        log.exception("수집 실패")
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler | None:
    settings = get_settings()
    if not settings.crawl_enabled:
        return None
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        run_collect_job,
        CronTrigger.from_crontab(settings.crawl_cron, timezone=settings.timezone),
        id="collect",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("수집 스케줄러 시작 (%s)", settings.crawl_cron)
    return scheduler
