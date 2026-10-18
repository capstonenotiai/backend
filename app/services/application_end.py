"""Fill missing application deadlines and correct the known one-day model offset."""
from datetime import date, timedelta

from sqlalchemy import select

from app.models import Event, Notice, ReviewLog, UserEvent

OFFSET_REASON = 'AI 마감일이 사이트 접수 마감일보다 하루 늦어 사이트 기준으로 고쳤어요. 원문에서 확인해 주세요.'


def correct_event_end(event, deadline, source='site'):
    if event.event_type != 'application' or not deadline or not event.end_date:
        return False
    try:
        end = date.fromisoformat(deadline)
        if date.fromisoformat(event.end_date) != end + timedelta(days=1):
            return False
        if event.start_date and date.fromisoformat(event.start_date) > end:
            return False
    except ValueError:
        return False
    event.end_date = deadline
    event.extraction_metadata = {**(event.extraction_metadata or {}), 'end_date_source': f'{source}_deadline_corrected'}
    event.review_reason = ' / '.join(filter(None, [event.review_reason, OFFSET_REASON if source != 'title' else TITLE_OFFSET_REASON]))
    return True


def fix_application_end_offsets(db, dry_run=False):
    from types import SimpleNamespace
    rows = db.execute(select(Event, Notice.application_end_date, Notice.source_metadata).join(Notice).where(
        Event.event_type == 'application', Event.review_status != 'approved',
        Notice.application_end_date.is_not(None),
        ~select(ReviewLog.id).where(ReviewLog.notice_id == Event.notice_id).exists(),
        ~select(UserEvent.id).where(UserEvent.event_id == Event.id,
                                   UserEvent.overrides.is_not(None)).exists()).order_by(Event.id)).all()
    changes = []
    for event, deadline, metadata in rows:
        target = SimpleNamespace(**{key: getattr(event, key) for key in (
            'event_type', 'start_date', 'end_date', 'extraction_metadata', 'review_reason')}) if dry_run else event
        before = event.end_date
        if correct_event_end(target, deadline, source=(metadata or {}).get('application_end_source', 'site')):
            changes.append({'event_id': event.id, 'before': before, 'after': target.end_date})
    if not dry_run:
        db.commit()
    return changes

SITE_DEADLINE_REASON = '접수 마감일을 사이트 정보로 채웠어요. 원문에서 확인해 주세요.'
MISSING_END_REASONS = {
    '종료일 또는 마감일을 원문에서 확인해 주세요.',
    '접수·제출 마감일이 없어요. 원문에서 마감일을 확인해 주세요.',
    'end_date 없음',
}


def fill_event_end(event, deadline, source='site'):
    if event.event_type != 'application' or event.end_date or not deadline:
        return False
    try:
        end = date.fromisoformat(deadline)
        if event.start_date and date.fromisoformat(event.start_date) > end:
            return False
    except ValueError:
        return False
    event.end_date = deadline
    event.extraction_metadata = {**(event.extraction_metadata or {}), 'end_date_source': f'{source}_deadline'}
    reasons = [part for part in (event.review_reason or '').split(' / ')
               if part and part not in MISSING_END_REASONS and part != SITE_DEADLINE_REASON]
    reason = SITE_DEADLINE_REASON if source != 'title' else TITLE_DEADLINE_REASON
    event.review_reason = ' / '.join(reasons + [reason])
    return True


def fill_application_ends(db, dry_run=False):
    rows = db.execute(select(Event, Notice.application_end_date, Notice.source_metadata).join(Notice)
                      .where(Event.event_type == 'application', Event.end_date == '',
                             Notice.application_end_date.is_not(None)).order_by(Event.id)).all()
    changed = 0
    # Dry-run operates on detached copies, with no ORM mutations or commits.
    from types import SimpleNamespace
    for event, deadline, metadata in rows:
        target = (SimpleNamespace(**{key: getattr(event, key) for key in (
            'event_type', 'start_date', 'end_date', 'extraction_metadata', 'review_reason')})
                  if dry_run else event)
        if fill_event_end(target, deadline, source=(metadata or {}).get('application_end_source', 'site')):
            changed += 1
    if not dry_run:
        db.commit()
    return changed


TITLE_OFFSET_REASON = 'AI 마감일이 제목의 마감일보다 하루 늦어 제목 기준으로 고쳤어요. 원문에서 확인해 주세요.'
TITLE_DEADLINE_REASON = '접수 마감일을 제목 정보로 채웠어요. 원문에서 확인해 주세요.'


def deadline_mismatch_reason(source):
    return ('제목의 마감일과 AI 마감일이 달라요. 원문 확인이 필요해요.' if source == 'title'
            else '사이트 접수 마감일과 AI 마감일이 다릅니다. 원문 확인이 필요합니다.')


def fill_title_deadlines(db, dry_run=False):
    from app.services.notice_metadata import title_deadline

    notices = db.scalars(select(Notice).where(Notice.application_end_date.is_(None)).order_by(Notice.id)).all()
    changes = []
    for notice in notices:
        deadline = title_deadline(notice.title_raw, notice.published_at)
        if not deadline:
            continue
        changes.append({'notice_id': notice.id, 'title': notice.title_raw, 'deadline': deadline})
        if dry_run:
            continue
        notice.application_end_date = deadline
        notice.source_metadata = {**(notice.source_metadata or {}), 'application_end_source': 'title'}
        reviewed = db.scalar(select(ReviewLog.id).where(ReviewLog.notice_id == notice.id).limit(1))
        for event in notice.events:
            if event.event_type not in ('application', 'submission'):
                continue
            edited = db.scalar(select(UserEvent.id).where(UserEvent.event_id == event.id,
                               UserEvent.overrides.is_not(None)).limit(1))
            if not reviewed and not edited and event.review_status not in ('approved', 'rejected'):
                correct_event_end(event, deadline, source='title')
            if event.end_date != deadline:
                reason = deadline_mismatch_reason('title')
                reasons = [part for part in (event.review_reason or '').split(' / ') if part]
                if reason not in reasons:
                    event.review_reason = ' / '.join(reasons + [reason])
    if not dry_run:
        db.commit()
    return changes
