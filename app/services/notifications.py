"""Notification candidate queries only; this service never sends messages."""
from app.services.events import _visible_rows
from app.services.display_rules import APPLICATION_TYPES, is_open
from app.timeutil import now_local


def notification_candidates(db, user, now=None):
    now = now or now_local()
    rows = _visible_rows(db, user)
    interested_notices = {event.notice_id for event, state in rows
                          if event.notice_id is not None and state and state.bookmarked}
    result = []
    for event, state in rows:
        if event.review_status not in ('auto', 'approved') or event.schedule_status == 'cancelled':
            continue
        if event.event_type in APPLICATION_TYPES:
            interested = bool(state and (state.bookmarked or state.registered)) or event.notice_id in interested_notices
            if interested and is_open(event, now):
                result.append({'event': event, 'kind': 'application_deadline'})
        elif state and state.registered and is_open(event, now):
            result.append({'event': event, 'kind': 'registered_event'})
    # TODO: Apply user notification preferences, due windows, delivery and deduplication in a future sender.
    return result
