"""Admin edits, immutable history and conservative calendar eligibility."""
from datetime import date, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update

from app.models import Event, Notice, ReviewLog, UserEvent
from app.services.category import categorize
from app.services.display_rules import service_excluded

EDIT_FIELDS = ('title','start_date','end_date','start_time','end_time','timezone',
               'location','detail','event_type','attendance_mode','schedule_status')


class EventEdit(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: int | None = None
    title: str = Field(min_length=1, max_length=1000)
    start_date: str = ''
    end_date: str = ''
    start_time: str = ''
    end_time: str = ''
    timezone: str = 'Asia/Seoul'
    location: str = Field(default='', max_length=255)
    detail: str = Field(default='', max_length=2000)
    event_type: Literal['application','submission','event','interview','orientation','result','service_change'] = 'event'
    attendance_mode: Literal['offline','online','hybrid','unknown','not_applicable'] = 'unknown'
    schedule_status: Literal['confirmed','tentative','cancelled','unknown'] = 'confirmed'

    @model_validator(mode='after')
    def valid_values(self):
        for key in ('start_date','end_date'):
            value = getattr(self,key)
            if value and (len(value)!=10 or date.fromisoformat(value).isoformat()!=value):
                raise ValueError('날짜는 유효한 YYYY-MM-DD여야 합니다.')
        for key in ('start_time','end_time'):
            value = getattr(self,key)
            if value and (len(value)!=5 or time.fromisoformat(value).strftime('%H:%M')!=value):
                raise ValueError('시간은 유효한 HH:MM이어야 합니다.')
            if value and not getattr(self,key.replace('time','date')):
                raise ValueError('시각에 해당하는 날짜가 필요합니다.')
        if self.start_date and self.end_date and self.start_date>self.end_date:
            raise ValueError('시작일이 종료일보다 늦습니다.')
        if self.start_date==self.end_date and self.start_time and self.end_time and self.start_time>self.end_time:
            raise ValueError('시작 시각이 종료 시각보다 늦습니다.')
        try: ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError): raise ValueError('지원하지 않는 시간대입니다.')
        if self.attendance_mode=='online' and self.location:
            raise ValueError('온라인 플랫폼은 물리 장소에 입력하지 않습니다.')
        return self


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    revision: int = Field(ge=0)
    action: Literal['save','approve','reject']
    reason: str = Field(min_length=1, max_length=2000)
    events: list[EventEdit] = Field(default_factory=list, max_length=50)


def snapshot(event):
    return {'id':event.id, **{k:getattr(event,k) for k in EDIT_FIELDS},
            'revision':event.revision, 'review_status':event.review_status,
            'review_reason':event.review_reason}


def confirmation_reason(event):
    if not getattr(event, 'ai_extracted', False):
        return None
    # Older B7 rows included this generic warning for every slim-v9 unknown status.
    # A status alone is not a model review request or a source deadline mismatch.
    generic = '일정 상태를 원문에서 확인해 주세요.'
    explicit = (getattr(event, 'extraction_metadata', None) or {}).get('review_reason')
    reasons = [part for part in (event.review_reason or '').split(' / ') if part and (part != generic or explicit == generic)]
    return ' / '.join(reasons) or None


def requires_confirmation(event):
    return bool(confirmation_reason(event))


def registration_error(event):
    if service_excluded(event):
        return '서비스에서 제공하지 않는 일정입니다.'
    if event.review_status not in ('auto','approved'):
        return '관리자 검토가 완료되지 않았거나 제외된 일정입니다.'
    if event.schedule_status=='cancelled': return '취소된 일정입니다.'
    if event.schedule_status=='unknown' and not getattr(event, 'ai_extracted', False):
        return '일정 상태 확인이 필요합니다.'
    try: EventEdit(**{k:getattr(event,k) for k in EDIT_FIELDS})
    except ValueError: return '날짜·시각·장소 정보를 확인해야 합니다.'
    if not event.end_date: return '종료일 또는 마감일이 확인되지 않았습니다.'
    if event.start_time and not event.end_time and event.start_date != event.end_date:
        return '여러 날 일정의 종료 시각 확인이 필요합니다.'
    if (event.end_time and not event.start_time and event.start_date and event.start_date!=event.end_date
            and event.event_type not in ('application', 'submission')):
        return '여러 날 일정의 시작 시각 확인이 필요합니다.'
    return None


def apply_review(db, notice_id, payload, actor):
    notice = db.get(Notice,notice_id)
    if not notice: raise HTTPException(404,'공지를 찾을 수 없습니다.')
    existing = {e.id:e for e in db.scalars(select(Event).where(Event.notice_id==notice_id))}
    ids = [e.id for e in payload.events if e.id is not None]
    if len(ids)!=len(set(ids)) or any(i not in existing for i in ids):
        raise HTTPException(400,'중복되거나 다른 공지의 일정 ID입니다.')
    # Compare-and-swap protects SQLite as well as MySQL from stale admin forms.
    locked = db.execute(update(Notice).where(Notice.id==notice_id,Notice.revision==payload.revision,
            Notice.extraction_state!='processing').values(revision=Notice.revision+1))
    if locked.rowcount!=1:
        db.rollback()
        raise HTTPException(409,'다른 작업에서 변경되었습니다. 새로 불러온 뒤 검토해 주세요.')
    before = [snapshot(e) for e in existing.values()]
    kept = set()
    if payload.action!='reject':
        for edit in payload.events:
            event = existing.get(edit.id)
            if event is None:
                event = Event(notice_id=notice.id,source=notice.site,source_url=notice.source_url,
                    category=categorize(notice.site,notice.board),extractor='admin',collected_at=notice.crawled_at,
                    revision=0)
                db.add(event)
            for key in EDIT_FIELDS: setattr(event,key,getattr(edit,key))
            event.review_status = 'approved' if payload.action=='approve' else 'needs_review'
            event.review_reason = payload.reason
            event.revision += 1
            db.flush()
            kept.add(event.id)
    # Removal is an auditable rejection, never a destructive delete.
    for event in existing.values():
        if event.id not in kept:
            event.review_status='rejected'
            event.review_reason=payload.reason
            event.revision+=1
    affected = set(existing) | kept
    if affected:
        db.execute(update(UserEvent).where(UserEvent.event_id.in_(affected),UserEvent.registered.is_(True))
                   .values(sync_status='needs_sync',sync_error=None))
    notice.extraction_state = {'save':'needs_review','approve':'approved','reject':'rejected'}[payload.action]
    db.flush()
    after = [snapshot(e) for e in db.scalars(select(Event).where(Event.notice_id==notice_id).order_by(Event.id))]
    db.add(ReviewLog(notice_id=notice_id,actor_id=actor.id,action=payload.action,reason=payload.reason,
                     before=before,after=after))
    db.commit()
    return {'id':notice.id,'revision':notice.revision,'state':notice.extraction_state}
