from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import BookmarkIn, BookmarkOut, EventOut
from app.services import preferences
from app.services.events import get_event_or_404, get_user_event, list_events, event_detail, notice_events

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def get_events(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pref = preferences.get_or_create_preference(db, user)
    return list_events(db, user, pref.enabled_sources)


@router.get('/{event_id}')
def detail(event_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return event_detail(db, user, event_id)


@router.put("/{event_id}/bookmark", response_model=BookmarkOut)
def set_bookmark(
    event_id: str, body: BookmarkIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    event = get_event_or_404(db, event_id)
    # Interest belongs to the notice, so it survives representative changes.
    event_detail(db, user, event_id)
    for sibling in notice_events(db, event):
        if sibling.event_type != 'result' and sibling.review_status in ('auto', 'approved'):
            get_user_event(db, user, sibling).bookmarked = body.bookmarked
    db.commit()
    return BookmarkOut(id=str(event.id), bookmarked=body.bookmarked)
