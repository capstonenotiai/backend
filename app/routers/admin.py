from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db import get_db
from app.deps import require_admin
from app.models import Notice, Event, ReviewLog, User, EventReport
from app.services.review import ReviewIn, apply_review, snapshot

router = APIRouter(prefix='/api/admin',tags=['admin'],dependencies=[Depends(require_admin)])

@router.get('/notices')
def notices(state: str = 'needs_review',limit: int = Query(30,ge=1,le=100),
            offset: int = Query(0,ge=0),db: Session = Depends(get_db)):
    filters=[]
    if state!='all':
        if state not in ('pending','retry_pending','processing','needs_review','approved','rejected','failed','extracted','no_events','reported'):
            raise HTTPException(400,'알 수 없는 검토 상태입니다.')
        filters = [Notice.id.in_(select(Event.notice_id).join(EventReport, EventReport.event_id == Event.id))] if state == 'reported' else [Notice.extraction_state==state]
    rows=db.scalars(select(Notice).where(*filters).order_by(Notice.crawled_at.desc(),Notice.id.desc())
                    .offset(offset).limit(limit)).all()
    return {'total':db.scalar(select(func.count()).select_from(Notice).where(*filters)),
            'items':[{'id':n.id,'title':n.title_raw,'state':n.extraction_state,'revision':n.revision,
                      'source':n.site,'error':n.extraction_error} for n in rows]}

@router.get('/notices/{notice_id}')
def detail(notice_id: int,db: Session = Depends(get_db)):
    n=db.get(Notice,notice_id)
    if not n: raise HTTPException(404,'공지를 찾을 수 없습니다.')
    events=db.scalars(select(Event).where(Event.notice_id==n.id).order_by(Event.id)).all()
    logs=db.scalars(select(ReviewLog).where(ReviewLog.notice_id==n.id).order_by(ReviewLog.id.desc())).all()
    return {'id':n.id,'title':n.title_raw,'body':n.raw_text,'source_url':n.source_url,
            'state':n.extraction_state,'revision':n.revision,'model_output':n.extraction_result,
            'error':n.extraction_error,'events':[snapshot(e) for e in events],
            'reports':[{'id':r.id,'event_id':r.event_id,'reason':r.reason,'memo':r.memo} for r in
                db.scalars(select(EventReport).join(Event).where(Event.notice_id==n.id).order_by(EventReport.id))],
            'history':[{'id':r.id,'actor_id':r.actor_id,'action':r.action,'reason':r.reason,
                        'before':r.before,'after':r.after,'at':r.created_at.isoformat()} for r in logs]}

@router.post('/notices/{notice_id}/review')
def review(notice_id: int,payload: ReviewIn,actor: User = Depends(require_admin),db: Session = Depends(get_db)):
    return apply_review(db,notice_id,payload,actor)
