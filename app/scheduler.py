"""
CRAWL_ENABLED=true: CRAWL_CRON 마다 수집, EXTRACT_CRON 마다 추출 대기열 처리
NOTIFY_ENABLED=true: NOTIFY_CRON 마다 알림 생성 + 이메일 발송
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.services.notifications import generate_notifications, send_pending_emails
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


def run_notify_job() -> None:
    """오늘 보낼 알림을 알림함에 만들고 이메일 발송"""
    db = SessionLocal()
    try:
        created = generate_notifications(db)
        sent = send_pending_emails(db)
        if created or sent:
            log.info("알림 %d건 생성, 메일 %d통 발송", created, sent)
    except Exception:
        log.exception("알림 작업 실패")
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler | None:
    settings = get_settings()
    if not (settings.crawl_enabled or settings.notify_enabled):
        return None
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    jobs = []
    if settings.crawl_enabled:
        jobs += [("collect", run_collect_job, settings.crawl_cron), ("extract", run_extract_job, settings.extract_cron)]
    if settings.notify_enabled:
        jobs.append(("notify", run_notify_job, settings.notify_cron))
    for job_id, func, cron in jobs:
        scheduler.add_job(func, CronTrigger.from_crontab(cron, timezone=settings.timezone),
                          id=job_id, max_instances=1, coalesce=True)
    scheduler.start()
    log.info("스케줄러 시작: %s", ", ".join(f"{job_id} {cron}" for job_id, _, cron in jobs))
    return scheduler
