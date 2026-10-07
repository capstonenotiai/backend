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
from app.schemas import RegisterIn, RegisterOut, ConfirmationIn
from app.services import google
from app.services.events import get_event_or_404, get_user_event, notice_events, calendar_events
from app.services.review import registration_error
from app.services.user_schedule import effective_event, validate_overrides
from app.services.display_rules import HIDDEN_TYPES, service_excluded

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
    if event.event_type == 'interview':
        raise HTTPException(400, '면접은 상세 화면에서 개별 추가해 주세요.')
    if event.review_status not in ('auto', 'approved') or service_excluded(event) or event.schedule_status == 'cancelled':
        raise HTTPException(400, '공개된 일정만 등록할 수 있습니다.')
    targets = [e for e in notice_events(db, event) if e.event_type != 'interview' and not service_excluded(e)
               and e.review_status in ('auto', 'approved') and e.schedule_status != 'cancelled']
    _register_targets(db, user, targets, body)
    return RegisterOut(id=str(event.id), registered=True)


def _register_targets(db, user, targets, confirmation=None):
    if not targets:
        raise HTTPException(400, '등록할 공개 일정이 없습니다.')
    patches = confirmation.overrides if confirmation else {}
    if any(key not in {str(e.id) for e in targets} for key in patches):
        raise HTTPException(400, '등록 대상이 아닌 일정의 수정 값입니다.')
    if any(e.ai_extracted for e in targets) and not (confirmation and confirmation.confirmed):
        raise HTTPException(400, '날짜·시각·장소를 확인한 뒤 등록해 주세요.')
    prepared = []
    # Validate the complete bundle before any external writes.
    for event in targets:
        state = get_user_event(db, user, event)
        overrides = validate_overrides(event, state, patches[str(event.id)].model_dump(exclude_unset=True)) if str(event.id) in patches else (state.overrides or {})
        effective = effective_event(event, overrides=overrides)
        error = registration_error(effective)
        if error:
            raise HTTPException(400, f'함께 등록할 일정 확인이 필요합니다: {error}')
        prepared.append((event, state, effective, overrides))
    skip = _can_skip_google(user)
    for event, state, effective, overrides in prepared:
        changed = (state.overrides or {}) != overrides
        if state.registered and not changed and state.sync_status == 'synced':
            continue
        try:
            if not skip:
                if state.registered and state.google_event_id:
                    google.update_event(user.google_refresh_token, state.google_event_id, effective)
                else:
                    state.google_event_id = google.insert_event(user.google_refresh_token, effective)
        except (google.GoogleError, httpx.HTTPError):
            db.rollback()
            # Successful siblings are committed; a retry skips them instead of duplicating them.
            raise HTTPException(502, '일부 일정 등록에 실패했습니다. 이미 등록한 일정은 유지됩니다. 다시 시도해 주세요.') from None
        state.registered = True
        state.overrides = overrides or None
        state.synced_revision = event.revision
        state.sync_status = 'synced'
        state.sync_error = None
        db.commit()


@router.get('/events')
def registered_events(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return calendar_events(db, user)


@router.post('/interviews/{event_id}', response_model=RegisterOut)
def register_interview(event_id: str, body: ConfirmationIn | None = None,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event = get_event_or_404(db, event_id)
    if event.event_type != 'interview':
        raise HTTPException(400, '면접 일정만 개별 추가할 수 있습니다.')
    _register_targets(db, user, [event], body)
    return RegisterOut(id=str(event.id), registered=True)


@router.delete("/register/{event_id}", response_model=RegisterOut)
def unregister(event_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event = get_event_or_404(db, event_id)
    targets = [event] if event.event_type == 'interview' else [
        e for e in notice_events(db, event) if e.event_type != 'interview' and e.event_type not in HIDDEN_TYPES]
    for target in targets:
        state = get_user_event(db, user, target)
        if state.google_event_id and user.google_refresh_token:
            try:
                google.delete_event(user.google_refresh_token, state.google_event_id)
            except (google.GoogleError, httpx.HTTPError):
                db.rollback()
                raise HTTPException(502, '일부 일정 해제에 실패했습니다. 다시 시도해 주세요.') from None
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
    effective = effective_event(event, state)
    error=registration_error(effective)
    if error and not remove: raise HTTPException(400,error)
    try:
        skip=_can_skip_google(user)
        if not skip:
            if not state.google_event_id: raise google.GoogleError('기존 Google 일정 ID 없음: 해제 후 재등록 필요')
            if remove: google.delete_event(user.google_refresh_token,state.google_event_id)
            else: google.update_event(user.google_refresh_token,state.google_event_id,effective)
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
