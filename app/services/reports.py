from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, EventReport, User
from app.timeutil import as_aware


def list_mine(db: Session, user: User) -> list[dict]:
    rows = db.execute(select(EventReport, Event.title).outerjoin(Event, Event.id == EventReport.event_id)
                      .where(EventReport.user_id == user.id)
                      .order_by(EventReport.created_at.desc(), EventReport.id.desc())).all()
    return [{'id': report.id, 'event_id': report.event_id, 'title': title, 'reason': report.reason,
             'memo': report.memo, 'status': 'received', 'created_at': as_aware(report.created_at).isoformat()}
            for report, title in rows]
