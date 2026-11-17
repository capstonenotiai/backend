from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User, EventReport
from app.schemas import BookmarkIn, BookmarkOut, EventOut
from app.services import preferences
from app.services.events import get_event_or_404, get_user_event, list_events, event_detail, notice_events
from app.services.user_schedule import ActionIn, ReportIn, DismissIn, ScheduleOverrides, validate_overrides, get_user_notice
from fastapi import HTTPException

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def get_events(include_dismissed: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pref = preferences.get_or_create_preference(db, user)
    return list_events(db, user, pref.enabled_sources, include_dismissed)


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


@router.patch('/{event_id}/overrides', response_model=EventOut)
def edit_schedule(event_id: str, body: ScheduleOverrides,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event_detail(db, user, event_id)
    event = get_event_or_404(db, event_id)
    state = get_user_event(db, user, event)
    state.overrides = validate_overrides(event, state, body.model_dump(exclude_unset=True))
    if state.registered:
        state.sync_status = 'needs_sync'
    db.commit()
    from app.services.events import to_event_out
    return to_event_out(event, state)


@router.put('/{event_id}/action-status')
def set_action_status(event_id: str, body: ActionIn,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event_detail(db, user, event_id)
    event = get_event_or_404(db, event_id)
    get_user_event(db, user, event).action_status = body.action_status
    db.commit()
    return {'id': str(event.id), 'action_status': body.action_status}


@router.post('/{event_id}/reports', status_code=201)
def report(event_id: str, body: ReportIn,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event_detail(db, user, event_id)
    event = get_event_or_404(db, event_id)
    record = EventReport(user_id=user.id, event_id=event.id, reason=body.reason, memo=body.memo)
    db.add(record); db.commit()
    return {'id': record.id, 'saved': True}


@router.put('/{event_id}/dismissed')
def dismiss(event_id: str, body: DismissIn,
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event_detail(db, user, event_id)
    event = get_event_or_404(db, event_id)
    if event.notice_id is None:
        raise HTTPException(400, '원문 공지가 연결되지 않은 일정입니다.')
    get_user_notice(db, user, event.notice_id).dismissed = body.dismissed
    db.commit()
    return {'notice_id': event.notice_id, 'dismissed': body.dismissed}
