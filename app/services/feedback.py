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


def list_feedback(db: Session, limit: int, offset: int) -> dict:
    rows = db.scalars(select(ServiceFeedback).order_by(ServiceFeedback.created_at.desc(), ServiceFeedback.id.desc())
                      .limit(limit).offset(offset)).all()
    return {'total': db.scalar(select(func.count()).select_from(ServiceFeedback)),
            'items': [{'id': item.id, 'user_id': item.user_id, 'type': item.type, 'message': item.message,
                       'reply_email': item.reply_email, 'created_at': as_aware(item.created_at).isoformat()}
                      for item in rows]}
