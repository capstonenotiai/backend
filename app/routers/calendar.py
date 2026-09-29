"""
Google Calendar 등록 / 해제 — 프론트 services/calendarService.js 계약.

Google 권한(refresh token)이 없는 사용자는:
  - DEV_LOGIN=true : DB 의 registered 값만 바꾼다 (OAuth 설정 전 화면 테스트용)
  - 그 외         : 400
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import RegisterIn, RegisterOut
from app.services import google
from app.services.events import get_event_or_404, get_user_event

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
    if not event.end_date:
        raise HTTPException(status_code=400, detail="날짜가 확인되지 않은 일정은 캘린더에 등록할 수 없습니다.")

    if not _can_skip_google(user):
        try:
            state.google_event_id = google.insert_event(user.google_refresh_token, event)
        except google.GoogleError as error:
            log.error("calendar register failed user=%s event=%s: %s", user.id, event.id, error)
            raise HTTPException(status_code=502, detail="Google 캘린더 등록에 실패했습니다.") from None

    state.registered = True
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
    db.commit()
    return RegisterOut(id=str(event.id), registered=False)
