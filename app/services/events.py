from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from app.config import get_settings
from app.models import Event, User, UserEvent, UserNotice
from app.schemas import EventOut
from app.timeutil import as_aware, now_local, to_local
from app.services.review import registration_error, requires_confirmation, confirmation_reason
from app.services.display_rules import HIDDEN_TYPES, representative, service_excluded
from app.services.user_schedule import effective_event


def parse_event_id(value: str | int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.") from None


def get_event_or_404(db: Session, event_id: str | int) -> Event:
    event = db.get(Event, parse_event_id(event_id))
    if not event:
        raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
    return event


def get_user_event(db: Session, user: User, event: Event) -> UserEvent:
    state = db.scalar(select(UserEvent).where(UserEvent.user_id == user.id, UserEvent.event_id == event.id))
    if not state:
        state = UserEvent(user_id=user.id, event_id=event.id)
        db.add(state)
    return state


def to_event_out(event: Event, state: UserEvent | None, dismissed=False) -> EventOut:
    new_since = now_local() - timedelta(days=get_settings().new_event_days)
    shared = event
    event = effective_event(shared, state)
    return EventOut(
        id=str(event.id),
        title=event.title,
        start_date=event.start_date or "",
        end_date=event.end_date or "",
        location=event.location or "",
        detail=event.detail or "",
        source=shared.source,
        source_url=event.source_url or "",
        category=shared.category,
        is_new=as_aware(shared.collected_at) >= new_since,
        registered=bool(state and state.registered),
        bookmarked=bool(state and state.bookmarked),
        collected_at=to_local(shared.collected_at).isoformat(timespec="seconds"),
        review_status=event.review_status,
        review_reason=confirmation_reason(shared) if shared.ai_extracted else event.review_reason,
        notice_id=event.notice_id,event_type=event.event_type,
        start_time=event.start_time,end_time=event.end_time,timezone=event.timezone,
        attendance_mode=event.attendance_mode,schedule_status=event.schedule_status,revision=event.revision,
        can_register=registration_error(event) is None,registration_reason=registration_error(event),
        sync_status=state.sync_status if state else 'none',
        ai_extracted=shared.ai_extracted, review_required=requires_confirmation(shared),
        action_status=state.action_status if state else 'pending', dismissed=dismissed,
        user_modified=bool(state and state.overrides),
    )


def _visible_rows(db, user, enabled_sources=None):
    state = aliased(UserEvent)
    query = (select(Event, state)
             .outerjoin(state, and_(state.event_id == Event.id, state.user_id == user.id))
             .where(Event.event_type.not_in(HIDDEN_TYPES))
             .where(or_(Event.review_status.in_(['auto', 'approved']), state.registered.is_(True)))
             .order_by(Event.end_date, Event.id))
    disabled = [source for source, on in (enabled_sources or {}).items() if on is False]
    if disabled:
        query = query.where(Event.source.not_in(disabled))
    return [(event, state) for event, state in db.execute(query).all() if not service_excluded(event)]


def notice_events(db, event):
    if event.notice_id is None:
        return [event]
    return list(db.scalars(select(Event).where(Event.notice_id == event.notice_id).order_by(Event.id)))


def event_detail(db, user, event_id):
    anchor = get_event_or_404(db, event_id)
    rows = _visible_rows(db, user)
    group = [(event, state) for event, state in rows
             if event.id == anchor.id or (anchor.notice_id is not None and event.notice_id == anchor.notice_id)]
    if not group or not any(event.id == anchor.id for event, _ in group):
        raise HTTPException(404, '공개된 일정을 찾을 수 없습니다.')
    notice_state = db.get(UserNotice, (user.id, anchor.notice_id)) if anchor.notice_id else None
    dismissed = bool(notice_state and notice_state.dismissed)
    return {'notice_id': anchor.notice_id, 'dismissed': dismissed,
            'events': [to_event_out(event, state, dismissed) for event, state in group]}


def calendar_events(db, user):
    # A closed application must never hide an already registered main event.
    return [to_event_out(event, state) for event, state in _visible_rows(db, user) if state and state.registered]


def list_events(db: Session, user: User, enabled_sources: dict[str, bool] | None = None,
                include_dismissed: bool = False) -> list[EventOut]:
    """
    - 사용자 설정에서 끈 출처(enabled_sources[x] == False)는 제외
    - 공지별 가장 가까운 미마감 접수 1건, 접수가 없으면 본행사 1건
    - 접수가 모두 마감되면 보존 기간·관심·등록과 무관하게 목록에서 제외
    - 기존 보존 기간은 선택된 대표 일정에 적용; 캘린더는 별도 조회
    """
    retention = timedelta(days=get_settings().event_retention_days)
    cutoff_date = (now_local().date() - retention).isoformat()
    cutoff_time = (now_local() - retention).astimezone(timezone.utc)

    groups = {}
    dismissed_notices = set(db.scalars(select(UserNotice.notice_id).where(
        UserNotice.user_id == user.id, UserNotice.dismissed.is_(True))))
    for event, state in _visible_rows(db, user, enabled_sources):
        if not include_dismissed and event.notice_id in dismissed_notices:
            continue
        key = ('notice', event.notice_id) if event.notice_id is not None else ('event', event.id)
        groups.setdefault(key, []).append((event, state))
    output = []
    for rows in groups.values():
        # Choose before retention filtering: a retained main event cannot revive closed applications.
        chosen = representative([effective_event(event, state) for event, state in rows], now_local())
        if chosen is None:
            continue
        shared, state = next((event, state) for event, state in rows if event.id == chosen.id)
        bookmarked = any(state and state.bookmarked for _, state in rows)
        registered = bool(state and state.registered)
        if not (chosen.end_date >= cutoff_date or
                (not chosen.end_date and as_aware(shared.collected_at) >= cutoff_time) or
                registered or bookmarked):
            continue
        item = to_event_out(shared, state, chosen.notice_id in dismissed_notices)
        item.bookmarked = bookmarked
        output.append(item)
    return sorted(output, key=lambda item: (item.end_date or '9999', int(item.id)))
