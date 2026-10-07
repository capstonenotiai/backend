"""
서비스 안 알림함 (화면은 프론트 개편 때 결정)

GET  /api/notifications?limit=20&before=<id>  최신순 목록 → { items, next_before }
GET  /api/notifications/unread-count          → { count }
POST /api/notifications/{id}/read             → { id, read }
POST /api/notifications/read-all              → { updated }
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.services import notifications

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(limit: int = Query(20, ge=1, le=50), before: int | None = Query(None, ge=1),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return notifications.list_notifications(db, user, limit, before)


@router.get("/unread-count")
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"count": notifications.unread_count(db, user)}


@router.post("/read-all")
def read_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"updated": notifications.mark_all_read(db, user)}


@router.post("/{notification_id}/read")
def read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not notifications.mark_read(db, user, notification_id):
        raise HTTPException(404, "알림을 찾을 수 없습니다.")
    return {"id": str(notification_id), "read": True}
