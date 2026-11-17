from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.deps import require_admin
from app.models import Notice, Event, ReviewLog, User, EventReport
from app.services.review import ReviewIn, apply_review, snapshot
from app.services import feedback as feedback_service, admin_notices
from app.timeutil import as_aware

router = APIRouter(prefix='/api/admin',tags=['admin'],dependencies=[Depends(require_admin)])

@router.get('/feedback')
def feedback(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), type: str | None = None, db: Session = Depends(get_db)):
    return feedback_service.list_feedback(db, limit, offset, type)

@router.get('/notices')
def notices(state: str = 'needs_review',limit: int = Query(30,ge=1,le=100),
            offset: int = Query(0,ge=0),db: Session = Depends(get_db)):
    return admin_notices.list_notices(db, state, limit, offset)

@router.get('/notices/counts')
def notice_counts(db: Session = Depends(get_db)):
    return admin_notices.counts(db)

@router.get('/notices/{notice_id}')
def detail(notice_id: int,db: Session = Depends(get_db)):
    n=db.get(Notice,notice_id)
    if not n: raise HTTPException(404,'공지를 찾을 수 없습니다.')
    events=db.scalars(select(Event).where(Event.notice_id==n.id).order_by(Event.id)).all()
    logs=db.scalars(select(ReviewLog).where(ReviewLog.notice_id==n.id).order_by(ReviewLog.id.desc())).all()
    return {'id':n.id,'title':n.title_raw,'body':n.raw_text,'source_url':n.source_url,
            'state':n.extraction_state,'revision':n.revision,'model_output':n.extraction_result,
            'error':n.extraction_error,'events':[snapshot(e) for e in events],
            'reports':[{'id':r.id,'event_id':r.event_id,'reason':r.reason,'memo':r.memo,
                        'created_at':as_aware(r.created_at).isoformat(),
                        'resolved_at':as_aware(r.resolved_at).isoformat() if r.resolved_at else None} for r in
                db.scalars(select(EventReport).join(Event).where(Event.notice_id==n.id).order_by(EventReport.id))],
            'history':[{'id':r.id,'actor_id':r.actor_id,'action':r.action,'reason':r.reason,
                        'before':r.before,'after':r.after,'at':r.created_at.isoformat()} for r in logs]}

@router.post('/notices/{notice_id}/review')
def review(notice_id: int,payload: ReviewIn,actor: User = Depends(require_admin),db: Session = Depends(get_db)):
    return apply_review(db,notice_id,payload,actor)
