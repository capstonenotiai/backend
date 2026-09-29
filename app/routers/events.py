from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import BookmarkIn, BookmarkOut, EventOut
from app.services import preferences
from app.services.events import get_event_or_404, get_user_event, list_events

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def get_events(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pref = preferences.get_or_create_preference(db, user)
    return list_events(db, user, pref.enabled_sources)


# ⚠️ 프론트에 아직 북마크 API 가 없음 (eventService.setBookmark TODO) — 이 형태로 제안
@router.put("/{event_id}/bookmark", response_model=BookmarkOut)
def set_bookmark(
    event_id: str, body: BookmarkIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    event = get_event_or_404(db, event_id)
    state = get_user_event(db, user, event)
    state.bookmarked = body.bookmarked
    db.commit()
    return BookmarkOut(id=str(event.id), bookmarked=state.bookmarked)
