from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.models import Event, EventReport, Notice
from app.services.review import confirmation_reason
from app.timeutil import as_aware

STATES = ('needs_review', 'failed', 'pending', 'retry_pending', 'processing',
          'approved', 'rejected', 'extracted', 'no_events')


def open_reports():
    return select(Event.notice_id).join(EventReport, EventReport.event_id == Event.id).where(
        EventReport.resolved_at.is_(None))


def counts(db):
    result = dict.fromkeys((*STATES, 'reported', 'all'), 0)
    for state, count in db.execute(select(Notice.extraction_state, func.count()).group_by(Notice.extraction_state)):
        if state in STATES:
            result[state] = count
        result['all'] += count
    result['reported'] = db.scalar(select(func.count()).select_from(Notice).where(Notice.id.in_(open_reports())))
    return result


def list_notices(db, state, limit, offset):
    if state not in (*STATES, 'reported', 'all'):
        raise HTTPException(400, '알 수 없는 검토 상태입니다.')
    filters = []
    if state == 'reported':
        filters = [Notice.id.in_(open_reports())]
    elif state != 'all':
        filters = [Notice.extraction_state == state]
    rows = db.scalars(select(Notice).where(*filters).order_by(Notice.crawled_at.desc(), Notice.id.desc())
                      .offset(offset).limit(limit)).all()
    total = db.scalar(select(func.count()).select_from(Notice).where(*filters))
    ids = [notice.id for notice in rows]
    reasons, report_counts = {}, {}
    if ids:
        events = db.scalars(select(Event).options(joinedload(Event.notice)).where(Event.notice_id.in_(ids)).order_by(Event.id))
        for event in events:
            if event.notice_id not in reasons:
                reason = confirmation_reason(event)
                if reason:
                    reasons[event.notice_id] = ' '.join(reason.split())
        report_counts = dict(db.execute(select(Event.notice_id, func.count(EventReport.id))
                            .join(EventReport, EventReport.event_id == Event.id)
                            .where(Event.notice_id.in_(ids), EventReport.resolved_at.is_(None))
                            .group_by(Event.notice_id)).all())
    items = []
    for n in rows:
        reason = reasons.get(n.id)
        if n.extraction_state == 'failed':
            reason = ' '.join((n.extraction_error or '').split())[:200] or None
        items.append({'id': n.id, 'title': n.title_raw, 'state': n.extraction_state, 'revision': n.revision,
                      'source': n.site, 'error': n.extraction_error, 'published_at': n.published_at,
                      'crawled_at': as_aware(n.crawled_at).isoformat(), 'reason': reason,
                      'open_report_count': report_counts.get(n.id, 0)})
    return {'total': total, 'items': items}
