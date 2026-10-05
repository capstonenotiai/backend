"""
Google Calendar 등록 / 해제 — 프론트 services/calendarService.js 계약.

Google 권한(refresh token)이 없는 사용자는:
  - DEV_LOGIN=true : DB 의 registered 값만 바꾼다 (OAuth 설정 전 화면 테스트용)
  - 그 외         : 400
"""
import logging
import httpx

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import RegisterIn, RegisterOut
from app.services import google
from app.services.events import get_event_or_404, get_user_event
from app.services.review import registration_error

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _can_skip_google(user: User) -> bool:
    if user.google_refresh_token:
        return False
    if get_settings().dev_login:
        return True
    raise HTTPException(status_code=400, detail="Google 캘린더 권한이 없습니다. 다시 로그인해 주세요.")


@router.post("/register", response_model=RegisterOut)
def register(body: RegisterIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event = get_event_or_404(db, body.event_id)
    state = get_user_event(db, user, event)
    if state.registered:
        return RegisterOut(id=str(event.id), registered=True)
    error=registration_error(event)
    if error: raise HTTPException(status_code=400,detail=error)

    if not _can_skip_google(user):
        try:
            state.google_event_id = google.insert_event(user.google_refresh_token, event)
        except google.GoogleError as error:
            log.error("calendar register failed user=%s event=%s: %s", user.id, event.id, error)
            raise HTTPException(status_code=502, detail="Google 캘린더 등록에 실패했습니다.") from None

    state.registered = True
    state.synced_revision=event.revision
    state.sync_status='synced'
    state.sync_error=None
    db.commit()
    return RegisterOut(id=str(event.id), registered=True)


@router.delete("/register/{event_id}", response_model=RegisterOut)
def unregister(event_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event = get_event_or_404(db, event_id)
    state = get_user_event(db, user, event)

    if state.google_event_id and user.google_refresh_token:
        try:
            google.delete_event(user.google_refresh_token, state.google_event_id)
        except google.GoogleError as error:
            log.error("calendar unregister failed user=%s event=%s: %s", user.id, event.id, error)
            raise HTTPException(status_code=502, detail="Google 캘린더 삭제에 실패했습니다.") from None

    state.registered = False
    state.google_event_id = None
    state.sync_status='none'
    state.sync_error=None
    db.commit()
    return RegisterOut(id=str(event.id), registered=False)


@router.post('/sync/{event_id}',response_model=RegisterOut)
def sync(event_id: str,user: User = Depends(get_current_user),db: Session = Depends(get_db)):
    event=get_event_or_404(db,event_id)
    state=get_user_event(db,user,event)
    if not state.registered: raise HTTPException(400,'등록된 일정만 갱신할 수 있습니다.')
    revision=event.revision
    remove=event.review_status=='rejected' or event.schedule_status=='cancelled'
    error=registration_error(event)
    if error and not remove: raise HTTPException(400,error)
    try:
        skip=_can_skip_google(user)
        if not skip:
            if not state.google_event_id: raise google.GoogleError('기존 Google 일정 ID 없음: 해제 후 재등록 필요')
            if remove: google.delete_event(user.google_refresh_token,state.google_event_id)
            else: google.update_event(user.google_refresh_token,state.google_event_id,event)
    except (google.GoogleError,httpx.HTTPError) as exc:
        state.sync_status='failed'
        state.sync_error='Google 캘린더 반영 실패. 다시 시도해 주세요.'
        db.commit()
        raise HTTPException(502,state.sync_error) from exc
    # Re-read revision after external I/O; never mark a newer edit as synced.
    db.refresh(event)
    state.synced_revision=revision
    state.sync_status='needs_sync' if event.revision!=revision else ('none' if remove else 'synced')
    state.sync_error=None
    if remove:
        state.registered=False
        state.google_event_id=None
    db.commit()
    return RegisterOut(id=str(event.id),registered=state.registered)
