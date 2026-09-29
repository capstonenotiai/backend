"""
Google 로그인.

  GET  /api/auth/google/login     → Google 동의 화면으로 이동 (프론트 '로그인' 버튼이 이 주소로 이동)
  GET  /api/auth/google/callback  → 사용자 저장 + 세션 쿠키 발급 → FRONTEND_URL/dashboard 로 이동
  POST /api/auth/logout           → 세션 삭제
"""
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.services import google

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/google/login")
def google_login(request: Request):
    if not google.is_configured():
        raise HTTPException(status_code=500, detail="Google 로그인 설정이 완료되지 않았습니다.")
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    return RedirectResponse(google.build_auth_url(state))


@router.get("/google/callback")
def google_callback(
    request: Request, code: str | None = None, state: str | None = None, db: Session = Depends(get_db)
):
    frontend = get_settings().frontend_url.rstrip("/")
    expected = request.session.pop("oauth_state", None)
    if not code or not state or state != expected:
        return RedirectResponse(f"{frontend}/?login=failed")

    try:
        tokens = google.exchange_code(code)
        info = google.fetch_userinfo(tokens["access_token"])
    except google.GoogleError as error:
        log.error("google login failed: %s", error)
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
