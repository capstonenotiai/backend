"""Deterministic facts only. No GPT calls, enrichment or recommendation ranking."""
from datetime import date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import Event, Notice, UserEvent, UserNotice
from app.services.display_rules import APPLICATION_TYPES, deadline
from app.services.google import build_calendar_body
from app.services.user_schedule import effective_event
from app.services.review import requires_confirmation
from app.services.event_types import to_planner_event_type

SEOUL = ZoneInfo('Asia/Seoul')
URGENCY_BANDS = ((2, 'urgent'), (7, 'soon'), (14, 'upcoming'))
ACTION_TYPES = {'application': 'apply', 'submission': 'submit', 'event': 'attend', 'interview': 'attend'}


def action_type(event):
    return ACTION_TYPES.get(event.event_type, 'unknown')


def action_date(event):
    return (event.start_date or event.end_date) if action_type(event) == 'attend' else event.end_date


def urgency(day, now):
    if not day:
        return 'none'
    days = (date.fromisoformat(day) - now.astimezone(SEOUL).date()).days
    if days < 0:
        return 'none'
    return next((label for maximum, label in URGENCY_BANDS if days <= maximum), 'later')


def derived_event_facts(event, state=None, now=None):
    now = now or datetime.now(SEOUL)
    if now.tzinfo is None:
        raise ValueError('Facts require a timezone-aware now')
    now = now.astimezone(SEOUL)
    effective = effective_event(event, state)
    kind = action_type(effective)
    day = action_date(effective)
    end = deadline(effective)
    expired = end < now if end else None
    window = 'unknown'
    if kind in ('apply', 'submit') and end and not expired:
        start = datetime.combine(date.fromisoformat(effective.start_date),
            time.fromisoformat(effective.start_time) if effective.start_time else time.min,
            tzinfo=SEOUL) if effective.start_date else None
        window = 'not_open' if start and now < start else 'open'
    elif kind == 'attend' and day and expired is not True:
        window = 'open'
    return {'action_type': kind, 'action_date': day or None, 'expired': expired,
        'action_window': window, 'urgency': urgency(day, now),
        'review_status': 'needs_review' if requires_confirmation(event) else 'ok'}


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
        'review_required': requires_confirmation(event),
        **derived_event_facts(event, state, now),
    }


def _overlaps(left, right):
    return left[0] < right[1] and right[0] < left[1]


def opportunity_context(db, user):
    """활동 사실 계산에 필요한 사용자 데이터를 한 번에 읽는다 (활동마다 전체 일정을 다시 읽지 않도록)"""
    from app.services.events import _visible_rows
    from app.services.preferences import get_or_create_preference

    pref = get_or_create_preference(db, user)
    rows_by_notice = {}
    for event, state in _visible_rows(db, user, pref.enabled_sources):
        if event.notice_id is not None:
            rows_by_notice.setdefault(event.notice_id, []).append((event, state))
    registered = db.execute(select(Event, UserEvent).join(UserEvent, UserEvent.event_id == Event.id).where(
        UserEvent.user_id == user.id, UserEvent.registered.is_(True),
        Event.event_type.in_(('event', 'interview')),
        Event.schedule_status != 'cancelled', Event.review_status != 'rejected')).all()
    managed = set(db.scalars(select(Event.notice_id).join(UserEvent, UserEvent.event_id == Event.id).where(
        UserEvent.user_id == user.id, UserEvent.bookmarked.is_(True) | UserEvent.registered.is_(True))))
    dismissed = set(db.scalars(select(UserNotice.notice_id).where(
        UserNotice.user_id == user.id, UserNotice.dismissed.is_(True))))
    return {'rows_by_notice': rows_by_notice, 'managed': managed, 'dismissed': dismissed,
            'registered': [(event.notice_id, schedule_interval(effective_event(event, state)))
                           for event, state in registered]}


def opportunity_facts(db, user, notice, now=None, context=None):
    now = now or datetime.now(SEOUL)
    context = context or opportunity_context(db, user)
    rows = context['rows_by_notice'].get(notice.id, [])
    events = []
    intervals = []
    for event, state in rows:
        effective = effective_event(event, state)
        derived = derived_event_facts(event, state, now)
        events.append({'event_id': f'e{event.id}',
            'event_type': to_planner_event_type(event.event_type),
            **{key: getattr(effective, key) for key in (
                'title', 'detail', 'source_url', 'start_date', 'end_date', 'start_time', 'end_time', 'timezone', 'location')},
            **derived, 'action_status': state.action_status if state else 'pending'})
        if derived['action_type'] == 'attend':
            intervals.append(schedule_interval(effective))

    other_intervals = [interval for notice_id, interval in context['registered'] if notice_id != notice.id]
    conflict = any(_overlaps(left, right) for left in intervals for right in other_intervals if left and right)
    verified = bool(intervals) and all(intervals) and all(other_intervals)
    applications = [event for event in events if event['action_type'] in ('apply', 'submit')]
    return {'opportunity_id': f'o{notice.id}', 'title': notice.title_raw, 'source_url': notice.source_url,
        'category': next((event.category for event, _ in rows if event.category), None),
        'dismissed': notice.id in context['dismissed'], 'user_managed': notice.id in context['managed'],
        'events': events,
        'application_expired_not_done': bool(applications) and all(event['expired'] is True for event in applications)
            and not any(event['action_status'] == 'done' for event in applications),
        'calendar_conflict': True if conflict else False if verified else None}


def all_opportunity_facts(db, user, now=None):
    """사용자가 볼 수 있는 모든 활동의 사실 (공지 id 순)"""
    context = opportunity_context(db, user)
    notices = db.scalars(select(Notice).where(Notice.id.in_(context['rows_by_notice'])).order_by(Notice.id)).all()
    return [opportunity_facts(db, user, notice, now, context) for notice in notices]


def pairwise_conflicts(opportunities):
    intervals = [[schedule_interval(SimpleNamespace(**event)) for event in item['events']
                  if event['action_type'] == 'attend'] for item in opportunities]
    pairs = []
    for index, left in enumerate(opportunities):
        for other_index in range(index + 1, len(opportunities)):
            if any(_overlaps(a, b) for a in intervals[index] for b in intervals[other_index] if a and b):
                pairs.append([left['opportunity_id'], opportunities[other_index]['opportunity_id']])
    return pairs
