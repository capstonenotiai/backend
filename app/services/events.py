from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Event, User, UserEvent
from app.schemas import EventOut
from app.timeutil import as_aware, now_local, to_local


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


def to_event_out(event: Event, state: UserEvent | None) -> EventOut:
    new_since = now_local() - timedelta(days=get_settings().new_event_days)
    return EventOut(
        id=str(event.id),
        title=event.title,
        start_date=event.start_date or "",
        end_date=event.end_date or "",
        location=event.location or "",
        detail=event.detail or "",
        source=event.source,
        source_url=event.source_url or "",
        category=event.category,
        is_new=as_aware(event.collected_at) >= new_since,
        registered=bool(state and state.registered),
        bookmarked=bool(state and state.bookmarked),
        collected_at=to_local(event.collected_at).isoformat(timespec="seconds"),
        review_status=event.review_status,
    )


def list_events(db: Session, user: User, enabled_sources: dict[str, bool] | None = None) -> list[EventOut]:
    """사용자 설정에서 끈 출처(enabled_sources[x] == False)는 제외"""
    query = select(Event).order_by(Event.end_date, Event.id)
    disabled = [source for source, on in (enabled_sources or {}).items() if on is False]
    if disabled:
        query = query.where(Event.source.not_in(disabled))
    events = db.scalars(query).all()

    states = {
        state.event_id: state
        for state in db.scalars(select(UserEvent).where(UserEvent.user_id == user.id))
    }
    return [to_event_out(event, states.get(event.id)) for event in events]
