from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Event, Notice
from app.services import google
from app.timeutil import now_local

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health(db: Session = Depends(get_db)):
    """서버/DB/설정 상태 확인 (비밀값은 설정 여부만)"""
    settings = get_settings()
    body = {"status": "ok", "service": "notiai-backend", "time": now_local().isoformat(timespec="seconds")}
    try:
        db.execute(text("SELECT 1"))
        body["db"] = {
            "ok": True,
            "notices": db.scalar(select(func.count()).select_from(Notice)),
            "events": db.scalar(select(func.count()).select_from(Event)),
        }
    except Exception:  # noqa: BLE001
        body["status"], body["db"] = "degraded", {"ok": False}
    body["config"] = {
        "devLogin": settings.dev_login,
        "extractor": settings.extractor,
        "openaiApiKeyConfigured": bool(settings.openai_api_key),
        "googleOAuthConfigured": google.is_configured(),
        "crawlEnabled": settings.crawl_enabled,
    }
    return body
