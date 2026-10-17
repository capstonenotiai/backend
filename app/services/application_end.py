"""Use the site's deadline only when an application has no extracted end date."""
from datetime import date

from sqlalchemy import select

from app.models import Event, Notice

SITE_DEADLINE_REASON = '접수 마감일을 사이트 정보로 채웠어요. 원문에서 확인해 주세요.'
MISSING_END_REASONS = {
    '종료일 또는 마감일을 원문에서 확인해 주세요.',
    '접수·제출 마감일이 없어요. 원문에서 마감일을 확인해 주세요.',
    'end_date 없음',
}


def fill_event_end(event, deadline):
    if event.event_type != 'application' or event.end_date or not deadline:
        return False
    try:
        end = date.fromisoformat(deadline)
        if event.start_date and date.fromisoformat(event.start_date) > end:
            return False
    except ValueError:
        return False
    event.end_date = deadline
    event.extraction_metadata = {**(event.extraction_metadata or {}), 'end_date_source': 'site_deadline'}
    reasons = [part for part in (event.review_reason or '').split(' / ')
               if part and part not in MISSING_END_REASONS and part != SITE_DEADLINE_REASON]
    event.review_reason = ' / '.join(reasons + [SITE_DEADLINE_REASON])
    return True


def fill_application_ends(db, dry_run=False):
    rows = db.execute(select(Event, Notice.application_end_date).join(Notice)
                      .where(Event.event_type == 'application', Event.end_date == '',
                             Notice.application_end_date.is_not(None)).order_by(Event.id)).all()
    changed = 0
    # Dry-run operates on detached copies, with no ORM mutations or commits.
    from types import SimpleNamespace
    for event, deadline in rows:
        target = (SimpleNamespace(**{key: getattr(event, key) for key in (
            'event_type', 'start_date', 'end_date', 'extraction_metadata', 'review_reason')})
                  if dry_run else event)
        if fill_event_end(target, deadline):
            changed += 1
    if not dry_run:
        db.commit()
    return changed
