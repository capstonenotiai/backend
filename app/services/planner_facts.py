"""Deterministic facts only. No GPT calls, enrichment or recommendation ranking."""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import Event, UserEvent, UserNotice
from app.services.display_rules import APPLICATION_TYPES, deadline
from app.services.google import build_calendar_body
from app.services.user_schedule import effective_event

SEOUL = ZoneInfo('Asia/Seoul')


def schedule_interval(event):
    """Use the same half-open interval that Google Calendar receives."""
    try:
        body = build_calendar_body(event)
        values = []
        for key in ('start', 'end'):
            item = body[key]
            if 'dateTime' in item:
                values.append(datetime.fromisoformat(item['dateTime']).astimezone(SEOUL))
            else:
                values.append(datetime.combine(date.fromisoformat(item['date']), time.min, tzinfo=SEOUL))
        return tuple(values)
    except (ValueError, TypeError):
        return None


def event_facts(db, user, event, state=None, now=None):
    now = now or datetime.now(SEOUL)
    if now.tzinfo is None:
        raise ValueError('Facts require a timezone-aware now')
    now = now.astimezone(SEOUL)
    if state is None:
        state = db.scalar(select(UserEvent).where(UserEvent.user_id == user.id, UserEvent.event_id == event.id))
    effective = effective_event(event, state)
    end = deadline(effective)
    start = None
    if effective.start_date:
        start = datetime.combine(date.fromisoformat(effective.start_date),
            time.fromisoformat(effective.start_time) if effective.start_time else time.min, tzinfo=SEOUL)
    expired = end < now if end else None
    can_apply = None
    if event.event_type in APPLICATION_TYPES and start and end:
        can_apply = start <= now <= end and effective.schedule_status != 'cancelled'
    notice_state = db.get(UserNotice, (user.id, event.notice_id)) if event.notice_id else None
    bookmarked = bool(state and state.bookmarked)
    if event.notice_id:
        bookmarked = bookmarked or bool(db.scalar(select(UserEvent.id).join(Event).where(
            UserEvent.user_id == user.id, Event.notice_id == event.notice_id, UserEvent.bookmarked.is_(True)).limit(1)))
    interval = schedule_interval(effective)
    conflict_ids = []
    verified = interval is not None
    registered = db.execute(select(Event, UserEvent).join(UserEvent, UserEvent.event_id == Event.id).where(
        UserEvent.user_id == user.id, UserEvent.registered.is_(True), Event.id != event.id,
        Event.schedule_status != 'cancelled', Event.review_status != 'rejected')).all()
    for other, other_state in registered:
        other_interval = schedule_interval(effective_event(other, other_state))
        if not other_interval:
            verified = False
        elif interval and interval[0] < other_interval[1] and other_interval[0] < interval[1]:
            conflict_ids.append(other.id)
    return {
        'event_id': event.id, 'notice_id': event.notice_id, 'event_type': event.event_type,
        **{key: getattr(effective, key) for key in ('start_date', 'end_date', 'start_time', 'end_time', 'location')},
        'timezone': 'Asia/Seoul',
        'days_until_deadline': (end.date() - now.date()).days if end else None,
        'can_apply_now': can_apply, 'expired': expired,
        'bookmarked': bookmarked, 'registered': bool(state and state.registered),
        'action_status': state.action_status if state else 'pending',
        'dismissed': bool(notice_state and notice_state.dismissed),
        'calendar_conflict': True if conflict_ids else False if verified else None,
        'conflicting_event_ids': sorted(conflict_ids),
        'review_required': bool(event.review_reason),
    }
