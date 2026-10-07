import copy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, text, create_engine, inspect

from app.config import get_settings
from app.deps import get_current_user, get_or_create_dev_user
from app.main import app
from app.models import Event, EventReport, Notice, User, UserEvent, UserNotice
from app.services.extractor import ModelApiExtractor, parse_model_response, RetryableExtraction
from app.services.pipeline import extract_pending, import_records, mark_interrupted_runs
from app.services.planner_facts import event_facts
from app.services.event_types import to_planner_event_type
from app.services.notice_metadata import iso_publication, application_deadline
from app.services.google import build_calendar_body


def response():
    return {'schema_version': 'notiai-model-candidate-v1', 'status': 'candidate',
        'validation_errors': [], 'review_required': True, 'metadata': {'prompt_mode': 'slim-v9'},
        'candidate': {'events': [
            {'title': '접수', 'event_type': 'application', 'start_date': '2030-10-01', 'end_date': '2030-10-23',
             'start_time': None, 'end_time': '18:00', 'location': None, 'attendance_mode': 'not_applicable', 'schedule_status': 'unknown'},
            {'title': '면접이라는 제목의 행사', 'event_type': 'event', 'start_date': '2030-11-04', 'end_date': '2030-11-04',
             'start_time': '14:00', 'end_time': '15:00', 'location': '강당', 'attendance_mode': 'offline', 'schedule_status': 'confirmed'},
            {'title': '최종 선발', 'event_type': 'interview', 'start_date': '2030-11-03', 'end_date': '2030-11-03',
             'start_time': '14:00', 'end_time': '15:00', 'location': '회의실', 'attendance_mode': 'offline', 'schedule_status': 'confirmed'}]}}


class CachedModel:
    name = 'model_api'
    def __init__(self, raw=None): self.raw = raw or response(); self.references = []
    def extract(self, title, body, reference_time=None):
        self.references.append(reference_time)
        return parse_model_response(copy.deepcopy(self.raw), title)


def extracted(db, raw=None):
    import_records(db, [{'site': 'contestkorea', 'source_url': 'https://example.com/v9', 'title_raw': '공지',
        'raw_text': '원문', 'published_at': '2030-09-30', 'meta': {'접수기간': '2030.10.01 ~ 2030.10.22'}}])
    model = CachedModel(raw)
    assert extract_pending(db, model) == 1
    notice = db.scalar(select(Notice).where(Notice.source_url == 'https://example.com/v9'))
    return notice, list(db.scalars(select(Event).where(Event.notice_id == notice.id).order_by(Event.id))), model


@pytest.mark.parametrize('source,expected', [('2030-10-01','2030-10-01'),
    ('2030-10-01T12:30:00+09:00','2030-10-01'), ('2030.10.1','2030-10-01'), ('2030-02-30',None), ('',None), (None,None)])
def test_publication_normalization(source, expected):
    assert iso_publication(source) == expected


def test_crawler_metadata_import_preserves_source_and_null(db):
    import_records(db, [
        {'site':'cbnu','source_url':'https://example.com/1','title_raw':'a','raw_text':'b','list_date_raw':'2030.9.30'},
        {'site':'wevity','source_url':'https://example.com/2','title_raw':'a','list_date_raw':'2030.10.01 ~ 2030.10.23'}])
    rows = list(db.scalars(select(Notice).order_by(Notice.id)))
    assert rows[0].published_at == '2030-09-30' and rows[0].raw_text == 'b'
    assert rows[1].published_at is None
    assert application_deadline({'접수기간':'2030.10.01 ~ 10.23'}) == '2030-10-23'
    assert application_deadline({'행사기간':'2030.10.01 ~ 2030.10.23'}) is None


def test_reference_time_sent_and_warning_publication(client, db, monkeypatch):
    calls = []
    monkeypatch.setattr(get_settings(), 'model_api_url', 'http://localhost/extract')
    monkeypatch.setattr(httpx, 'post', lambda *a, **kw: (calls.append(kw) or httpx.Response(200,json=response())))
    assert ModelApiExtractor().extract('a','b','2030-09-30').events
    assert calls[0]['json']['reference_time'] == '2030-09-30'
    ModelApiExtractor().extract('a','b','bad')
    assert calls[1]['json']['reference_time'] is None
    notice, events, model = extracted(db)
    assert model.references == ['2030-09-30']
    assert notice.extraction_state == 'extracted'
    assert all(e.ai_extracted and e.review_status == 'auto' for e in events)
    assert events[0].end_date == '2030-10-23'  # Never overwrite a model deadline.
    assert '사이트 접수 마감일' in events[0].review_reason
    item = next(e for e in client.get('/api/events').json() if e['notice_id']==notice.id)
    assert item['review_required'] and item['source_url']
    assert len(client.get(f"/api/events/{events[0].id}").json()['events']) == 3


@pytest.mark.parametrize('failure', ['timeout','connect','503','429'])
def test_transient_model_errors(failure, monkeypatch):
    monkeypatch.setattr(get_settings(), 'model_api_url', 'http://localhost/extract')
    def post(*a, **kw):
        if failure == 'timeout': raise httpx.ReadTimeout('test')
        if failure == 'connect': raise httpx.ConnectError('test')
        return httpx.Response(int(failure), text='not JSON')
    monkeypatch.setattr(httpx, 'post', post)
    with pytest.raises(RetryableExtraction): ModelApiExtractor().extract('a','b')


def test_retry_then_success_and_exhaustion(db, monkeypatch):
    monkeypatch.setattr(get_settings(), 'extraction_max_attempts', 2)
    notice = Notice(site='cbnu', source_url='https://example.com/retry', title_raw='공지')
    db.add(notice); db.commit()
    class Failing:
        name='model_api'
        def extract(self,title,body): raise RetryableExtraction('secret should not be logged', {})
    assert extract_pending(db, Failing()) == 0
    assert notice.extraction_state == 'retry_pending' and notice.extraction_attempts == 1
    assert extract_pending(db, CachedModel()) == 1
    assert notice.extraction_state == 'extracted' and notice.extraction_attempts == 2
    another = Notice(site='cbnu', source_url='https://example.com/exhaust', title_raw='공지')
    db.add(another); db.commit()
    assert extract_pending(db, Failing()) == 0
    assert extract_pending(db, Failing()) == 0
    assert another.extraction_state == 'failed' and another.extraction_attempts == 2
    assert extract_pending(db, CachedModel()) == 0
    assert 'secret' not in another.extraction_error


def test_interrupted_extraction_is_retryable(db):
    notice=Notice(site='cbnu',source_url='https://example.com/interrupted',title_raw='공지',extraction_state='processing',extraction_attempts=1)
    db.add(notice); db.commit(); mark_interrupted_runs(db)
    assert notice.extraction_state == 'retry_pending'


@pytest.mark.parametrize('change', ['bad_date', 'wrong_type', 'invalid_envelope'])
def test_invalid_model_response_permanently_hidden(client, db, change):
    raw=response()
    if change=='bad_date': raw['candidate']['events'][0]['end_date']='2030-02-30'
    elif change=='wrong_type': raw['candidate']['events'][0]['event_type']='main_event'
    else: raw['status']='invalid'
    item=Notice(site='cbnu',source_url='https://example.com/invalid',title_raw='공지')
    db.add(item); db.commit()
    assert extract_pending(db,CachedModel(raw)) == 0
    assert item.extraction_state=='failed'
    assert not list(db.scalars(select(Event).where(Event.notice_id==item.id)))
    assert not any(e['notice_id']==item.id for e in client.get('/api/events').json())


def test_interview_exclusion_user_override_google_and_isolation(client, db, monkeypatch):
    notice, events, _=extracted(db)
    user=get_or_create_dev_user(db)
    user.google_refresh_token='test-only'; db.commit()
    bodies=[]
    monkeypatch.setattr('app.services.google.insert_event',lambda token,event: (bodies.append(build_calendar_body(event)) or f'google-{event.id}'))
    assert client.post('/api/calendar/register',json={'event_id':events[0].id}).status_code==400
    override={str(events[0].id):{'end_date':'2030-10-21','end_time':'17:00','location':'내 장소'}}
    r=client.post('/api/calendar/register',json={'event_id':events[0].id,'confirmed':True,'overrides':override})
    assert r.status_code==200, r.text
    states=list(db.scalars(select(UserEvent).where(UserEvent.user_id==user.id,UserEvent.event_id.in_([e.id for e in events]))))
    assert {s.event_id for s in states if s.registered} == {events[0].id,events[1].id}
    assert bodies[0]['start']['dateTime'].startswith('2030-10-21T17:00')
    assert bodies[0]['location']=='내 장소'
    assert db.get(Event,events[0].id).end_date=='2030-10-23'
    assert client.post('/api/calendar/register',json={'event_id':events[2].id,'confirmed':True}).status_code==400
    assert client.post(f'/api/calendar/interviews/{events[2].id}',json={'confirmed':True}).status_code==200
    other=User(email='other@test',name='other');db.add(other);db.commit()
    app.dependency_overrides[get_current_user]=lambda:other
    try:
        detail=client.get(f'/api/events/{events[0].id}').json()['events'][0]
        assert detail['end_date']=='2030-10-23' and not detail['registered'] and not detail['user_modified']
    finally: app.dependency_overrides.pop(get_current_user,None)


def test_invalid_overrides_validate_whole_bundle_before_google(client,db,monkeypatch):
    _,events,_=extracted(db);calls=[]
    user=get_or_create_dev_user(db);user.google_refresh_token='test-only';db.commit()
    monkeypatch.setattr('app.services.google.insert_event',lambda *a: calls.append(a))
    invalid={str(events[1].id):{'end_date':'2030-01-01'}}
    r=client.post('/api/calendar/register',json={'event_id':events[0].id,'confirmed':True,'overrides':invalid})
    assert r.status_code==400 and not calls
    foreign={str(events[2].id):{'location':'x'}}
    assert client.post('/api/calendar/register',json={'event_id':events[0].id,'confirmed':True,'overrides':foreign}).status_code==400


def test_report_action_dismissed_and_restore(client,db):
    notice,events,_=extracted(db);item=events[0];user=get_or_create_dev_user(db)
    assert client.post(f'/api/events/{item.id}/reports',json={'reason':'date','memo':'날짜 확인'}).status_code==201
    report=db.scalar(select(EventReport));assert report.event_id==item.id and report.user_id==user.id and report.memo=='날짜 확인'
    assert client.post(f'/api/events/{item.id}/reports',json={'reason':'invalid'}).status_code==400
    for value in ('done','pending'):
        assert client.put(f'/api/events/{item.id}/action-status',json={'action_status':value}).json()['action_status']==value
    assert client.put(f'/api/events/{item.id}/dismissed',json={'dismissed':True}).status_code==200
    assert not any(e['notice_id']==notice.id for e in client.get('/api/events').json())
    included=client.get('/api/events?include_dismissed=true').json()
    assert next(e for e in included if e['notice_id']==notice.id)['dismissed']
    other=User(email='other@test',name='other');db.add(other);db.commit()
    from app.services.events import list_events
    assert any(e.notice_id==notice.id for e in list_events(db,other))
    assert client.put(f'/api/events/{item.id}/dismissed',json={'dismissed':False}).status_code==200
    assert any(e['notice_id']==notice.id for e in client.get('/api/events').json())


def test_override_patch_reset_and_sync_effective_values(client,db,monkeypatch):
    _,events,_=extracted(db);item=events[1]
    assert client.post('/api/calendar/register',json={'event_id':events[0].id,'confirmed':True}).status_code==200
    assert client.patch(f'/api/events/{item.id}/overrides',json={'location':'내 강당'}).json()['location']=='내 강당'
    user=get_or_create_dev_user(db);user.google_refresh_token='test-only';db.commit()
    state=db.scalar(select(UserEvent).where(UserEvent.user_id==user.id,UserEvent.event_id==item.id))
    state.google_event_id='test-calendar';db.commit()
    sent=[]
    monkeypatch.setattr('app.services.google.update_event',lambda token,key,event:sent.append(event.location))
    assert client.post(f'/api/calendar/sync/{item.id}').status_code==200
    assert sent==['내 강당'] and item.location=='강당'
    assert client.patch(f'/api/events/{item.id}/overrides',json={'location':None}).json()['location']=='강당'


def test_planner_facts_seoul_overrides_deadline_and_conflict(client,db):
    notice,events,_=extracted(db);user=get_or_create_dev_user(db)
    app_state=UserEvent(user_id=user.id,event_id=events[0].id,bookmarked=True,action_status='done',
        overrides={'start_date':'2030-10-01','end_date':'2030-10-21','end_time':'17:00','location':'내 장소'})
    other=Event(source='cbnu',title='겹치는 일정',start_date='2030-10-21',end_date='2030-10-21',
        start_time='16:30',end_time='17:30',location='',detail='',review_status='auto')
    db.add_all([app_state,other,UserNotice(user_id=user.id,notice_id=notice.id,dismissed=True)]);db.flush()
    db.add(UserEvent(user_id=user.id,event_id=other.id,registered=True));db.commit()
    now=datetime(2030,10,21,7,59,tzinfo=timezone.utc) # 16:59 Seoul
    facts=event_facts(db,user,events[0],now=now)
    assert facts['end_date']=='2030-10-21' and facts['location']=='내 장소'
    assert facts['days_until_deadline']==0 and facts['can_apply_now'] and not facts['expired']
    assert facts['calendar_conflict'] and facts['conflicting_event_ids']==[other.id]
    assert facts['action_status']=='done' and facts['dismissed'] and facts['bookmarked'] and facts['review_required']
    after=event_facts(db,user,events[0],now=datetime(2030,10,21,8,1,tzinfo=timezone.utc))
    assert after['expired'] and not after['can_apply_now']
    assert client.get(f'/api/internal/planner/facts/{events[0].id}').status_code==200


def test_facts_future_all_day_and_adjacent_intervals(db):
    _,events,_=extracted(db);user=get_or_create_dev_user(db)
    f=event_facts(db,user,events[0],now=datetime(2030,9,30,23,59,tzinfo=ZoneInfo('Asia/Seoul')))
    assert not f['can_apply_now'] and not f['expired'] and f['days_until_deadline']==23
    item=events[1]
    another=Event(source='cbnu',title='인접',start_date=item.end_date,end_date=item.end_date,
        start_time='15:00',end_time='16:00',location='',detail='',review_status='auto')
    db.add(another);db.flush();db.add(UserEvent(user_id=user.id,event_id=another.id,registered=True));db.commit()
    assert event_facts(db,user,item)['calendar_conflict'] is False
    state=UserEvent(user_id=user.id,event_id=item.id,overrides={'start_time':'','end_time':''})
    db.add(state);db.commit()
    assert event_facts(db,user,item)['calendar_conflict'] is True


@pytest.mark.parametrize('kind,expected',[('application','application'),('submission','application'),
    ('event','main_event'),('interview','other'),(None,'unknown'),('','unknown'),('bogus','unknown')])
def test_reserved_planner_adapter(kind,expected):
    assert to_planner_event_type(kind)==expected


def test_service_migration_downgrade_preserves_original_rows(tmp_path):
    from app.db import run_migrations, BACKEND_ROOT
    from alembic.config import Config
    from alembic import command
    engine=create_engine(f'sqlite:///{tmp_path / "reversible.db"}')
    with engine.begin() as conn:
        run_migrations(conn,'0003')
        conn.execute(text("INSERT INTO users(id,email,name,created_at) VALUES (1,'test@local','keep','2030-01-01')"))
        conn.execute(text("INSERT INTO notices(id,site,source_url,title_raw,raw_text,crawled_at) VALUES (1,'cbnu','https://example.com/keep','keep','original','2030-01-01')"))
        run_migrations(conn)
        conn.execute(text("UPDATE notices SET extraction_state='retry_pending'"))
        config=Config();config.set_main_option('script_location',str(BACKEND_ROOT/'migrations'));config.attributes['connection']=conn
        command.downgrade(config,'0003')
        assert conn.execute(text('SELECT raw_text FROM notices')).scalar()=='original'
        assert conn.execute(text('SELECT name FROM users')).scalar()=='keep'
        assert conn.execute(text('SELECT extraction_state FROM notices')).scalar()=='pending'
        assert 'published_at' not in {c['name'] for c in inspect(conn).get_columns('notices')}
        run_migrations(conn)
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0004'


@pytest.mark.parametrize('title', ['결과 발표', '시상식', '설문 (선택)', '선택 설문', '시스템 중단 안내'])
def test_non_service_schedules_hidden_and_not_registered(client,db,title):
    event=Event(source='cbnu',title=title,start_date='2030-10-01',end_date='2030-10-01',
        event_type='event',review_status='auto',ai_extracted=True)
    db.add(event);db.commit()
    assert not any(e['id']==str(event.id) for e in client.get('/api/events').json())
    assert client.get(f'/api/events/{event.id}').status_code==404
    assert client.post('/api/calendar/register',json={'event_id':event.id,'confirmed':True}).status_code==400


def test_no_warning_when_site_deadline_agrees(client,db):
    import_records(db,[{'site':'contestkorea','source_url':'https://example.com/match','title_raw':'공지',
        'meta':{'접수기간':'2030.10.01 ~ 2030.10.23'}}])
    raw=response();raw['candidate']['events'][0]['schedule_status']='confirmed'
    assert extract_pending(db,CachedModel(raw))==1
    event=db.scalar(select(Event).where(Event.event_type=='application'))
    assert event.review_reason is None


def test_missing_application_start_and_dates_stay_unknown(db):
    _,events,_=extracted(db);user=get_or_create_dev_user(db)
    state=UserEvent(user_id=user.id,event_id=events[0].id,overrides={'start_date':''})
    db.add(state);db.commit()
    assert event_facts(db,user,events[0])['can_apply_now'] is None
    state.overrides={'start_date':'','end_date':'','end_time':''};db.commit()
    facts=event_facts(db,user,events[0])
    assert facts['expired'] is None and facts['calendar_conflict'] is None and facts['days_until_deadline'] is None


def test_conflicts_only_use_this_users_registered_events(db):
    _,events,_=extracted(db);user=get_or_create_dev_user(db)
    other_user=User(email='other@local',name='other');db.add(other_user);db.flush()
    overlapping=Event(source='cbnu',title='다른 사용자 일정',start_date='2030-11-04',end_date='2030-11-04',
        start_time='14:00',end_time='15:00',review_status='auto')
    db.add(overlapping);db.flush();db.add(UserEvent(user_id=other_user.id,event_id=overlapping.id,registered=True));db.commit()
    assert event_facts(db,user,events[1])['conflicting_event_ids']==[]


@pytest.mark.parametrize('module_name', ['contestkorea','wevity'])
@pytest.mark.parametrize('publication', [True,False])
def test_crawler_uses_publication_tags_not_event_dates(monkeypatch,module_name,publication):
    import importlib
    from types import SimpleNamespace
    module=importlib.import_module('crawler.'+module_name)
    tagged='<meta property="article:published_time" content="2030-09-30T12:00:00+09:00">' if publication else ''
    html=tagged+'<h1>테스트 공지 제목입니다</h1><h4 class="tit">테스트 공지 제목입니다</h4><time datetime="2030-10-23">행사 날짜</time><div class="view_detail_area">본문</div>'
    if module_name=='wevity':
        monkeypatch.setattr(module,'_get',lambda url:html)
        result=module.parse_detail_page(1)
    else:
        monkeypatch.setattr(module,'get',lambda url:SimpleNamespace(text=html,apparent_encoding='utf-8'))
        result=module.parse_detail_page('https://example.com/notice')
    assert result['published_at']==('2030-09-30' if publication else None)
