from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ServiceFeedback, User
from app.schemas import FeedbackIn, FeedbackOut
from app.timeutil import as_aware


def submit(db: Session, user: User, body: FeedbackIn) -> FeedbackOut:
    item = ServiceFeedback(user_id=user.id, **body.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return FeedbackOut(id=item.id, created_at=as_aware(item.created_at))


def list_feedback(db: Session, limit: int, offset: int, kind: str | None = None) -> dict:
    counts = dict.fromkeys(("all", "inconvenience", "suggestion", "praise", "other"), 0)
    if kind is not None and kind not in counts.keys() - {"all"}:
        raise HTTPException(400, '알 수 없는 의견 종류입니다.')
    for category, count in db.execute(select(ServiceFeedback.type, func.count()).group_by(ServiceFeedback.type)):
        counts[category] = count
        counts["all"] += count
    filters = [ServiceFeedback.type == kind] if kind is not None else []
    rows = db.scalars(select(ServiceFeedback).where(*filters).order_by(ServiceFeedback.created_at.desc(), ServiceFeedback.id.desc())
                      .limit(limit).offset(offset)).all()
    return {'total': counts[kind] if kind is not None else counts['all'], 'counts': counts,
            'items': [{'id': item.id, 'user_id': item.user_id, 'type': item.type, 'message': item.message,
                       'reply_email': item.reply_email, 'created_at': as_aware(item.created_at).isoformat()}
                      for item in rows]}
