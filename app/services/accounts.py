import logging

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.models import EventReport, Notification, Preference, ReviewLog, ServiceFeedback, User, UserEvent, UserNotice
from app.services import google

log = logging.getLogger(__name__)


def delete_account(db: Session, user: User) -> None:
    if user.google_refresh_token:
        try:
            google.revoke_token(user.google_refresh_token)
        except Exception:
            # Provider/transport errors must never expose tokens or block deletion.
            log.warning('Google token revocation failed during account deletion')
    db.execute(update(ReviewLog).where(ReviewLog.actor_id == user.id).values(actor_id=None))
    for model in (Preference, UserEvent, UserNotice, Notification, EventReport, ServiceFeedback):
        db.execute(delete(model).where(model.user_id == user.id))
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
