"""CRAWL_ENABLED=true 이면 서버 실행 중 CRAWL_CRON 마다 수집, EXTRACT_CRON 마다 추출 대기열 처리"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.services.pipeline import collect, extract_pending

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


def run_extract_job() -> None:
    """모델 서버(A)가 꺼져 있던 동안 쌓인 공지를 다시 켜졌을 때 처리"""
    db = SessionLocal()
    try:
        count = extract_pending(db)
        if count:
            log.info("대기열 추출 %d건", count)
    except Exception:
        log.exception("대기열 추출 실패")
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
    scheduler.add_job(
        run_extract_job,
        CronTrigger.from_crontab(settings.extract_cron, timezone=settings.timezone),
        id="extract",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("수집 스케줄러 시작 (수집 %s, 추출 %s)", settings.crawl_cron, settings.extract_cron)
    return scheduler
