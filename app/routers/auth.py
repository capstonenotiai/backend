"""
Google 로그인.

  GET  /api/auth/google/login     → Google 동의 화면으로 이동 (프론트 '로그인' 버튼이 이 주소로 이동)
  GET  /api/auth/google/calendar  → (로그인 상태) 캘린더 권한 동의 화면으로 이동, ?next=/돌아올/경로
  GET  /api/auth/google/callback  → 로그인: 사용자 저장 + 세션 쿠키 발급 → FRONTEND_URL/dashboard 로 이동
                                    캘린더 연결: refresh token 저장 → FRONTEND_URL{next}?calendar=connected|denied|failed|wrong_account
  POST /api/auth/logout           → 세션 삭제
"""
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.services import google

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/google/login")
def google_login(request: Request):
    settings = get_settings()
    if not google.is_configured():
        # 개발 모드: Google 설정 전에도 로그인 버튼 → 대시보드 흐름을 확인할 수 있게 (개발용 사용자로 동작)
        if settings.dev_login:
            return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/dashboard")
        raise HTTPException(status_code=500, detail="Google 로그인 설정이 완료되지 않았습니다.")
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    return RedirectResponse(google.build_auth_url(state))


def _safe_next(value: str | None) -> str:
    """앱 안의 경로만 허용한다 (외부 주소로 보내는 open redirect 방지)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/dashboard"
    return value


def _with_param(path: str, key: str, value: str) -> str:
    parts = urlsplit(path)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key] + [(key, value)]
    return urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))


@router.get("/google/calendar")
def google_calendar_connect(request: Request, next: str | None = None, user: User = Depends(get_current_user)):
    settings = get_settings()
    target = _safe_next(next)
    if not google.is_configured():
        # 개발 모드는 Google 없이 등록되므로 바로 돌려보낸다.
        return RedirectResponse(f"{settings.frontend_url.rstrip('/')}{_with_param(target, 'calendar', 'connected')}")
    state = secrets.token_urlsafe(24)
    request.session.update(oauth_state=state, oauth_purpose="calendar", oauth_next=target)
    return RedirectResponse(google.build_auth_url(state, calendar=True, login_hint=user.email))


def _calendar_callback(request: Request, db: Session, code: str | None, frontend: str) -> RedirectResponse:
    target = _safe_next(request.session.pop("oauth_next", None))

    def back(result: str) -> RedirectResponse:
        return RedirectResponse(f"{frontend}{_with_param(target, 'calendar', result)}")

    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id is not None else None
    if not user:
        return back("failed")
    if not code:
        # 동의를 거절하면 Google 이 code 없이 error=access_denied 로 돌려보낸다.
        return back("denied")
    try:
        tokens = google.exchange_code(code)
        info = google.fetch_userinfo(tokens["access_token"])
    except google.GoogleError as error:
        log.error("google calendar connect failed: %s", error)
        return back("failed")
    if info.get("sub") != user.google_sub:
        # 다른 Google 계정으로 동의한 경우: 로그인한 계정의 캘린더가 아니므로 저장하지 않는다.
        return back("wrong_account")
    if not google.has_calendar_scope(tokens):
        return back("denied")
    if not tokens.get("refresh_token"):
        return back("failed")
    user.google_refresh_token = tokens["refresh_token"]
    db.commit()
    return back("connected")


@router.get("/google/callback")
def google_callback(
    request: Request, code: str | None = None, state: str | None = None, db: Session = Depends(get_db)
):
    frontend = get_settings().frontend_url.rstrip("/")
    expected = request.session.pop("oauth_state", None)
    purpose = request.session.pop("oauth_purpose", "login")
    if purpose == "calendar":
        if not state or state != expected:
            request.session.pop("oauth_next", None)
            return RedirectResponse(f"{frontend}/dashboard?calendar=failed")
        return _calendar_callback(request, db, code, frontend)
    if not code or not state or state != expected:
        return RedirectResponse(f"{frontend}/?login=failed")

    try:
        tokens = google.exchange_code(code)
        info = google.fetch_userinfo(tokens["access_token"])
    except google.GoogleError as error:
        log.error("google login failed: %s", error)
        return RedirectResponse(f"{frontend}/?login=failed")

    if info.get('email_verified') is not True or not info.get('sub') or not info.get('email'):
        return RedirectResponse(f"{frontend}/?login=failed")
    user = db.scalar(select(User).where(User.google_sub == info["sub"]))
    if not user:
        user = User(google_sub=info["sub"], email=info.get("email", ""), name=info.get("name", ""))
        db.add(user)
    user.email = info.get("email", user.email)
    user.name = info.get("name", user.name) or user.email
    if tokens.get("refresh_token"):
        user.google_refresh_token = tokens["refresh_token"]
    db.commit()

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(f"{frontend}/dashboard")


@router.post("/logout", status_code=204)
def logout(request: Request):
    request.session.clear()
    return Response(status_code=204)
