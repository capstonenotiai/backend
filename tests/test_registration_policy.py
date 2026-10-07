from sqlalchemy import select
from app.deps import get_or_create_dev_user
from app.models import Event, Notice, UserEvent
from app.services.extractor import parse_model_response
from app.services.review import confirmation_reason


def bundle(db, reason=None):
    notice = Notice(site='cbnu', source_url='https://example.com/b7b', title_raw='B7b 확인')
    db.add(notice); db.flush()
    events = []
    for kind in ('application', 'event', 'interview'):
        event = Event(notice_id=notice.id, source='cbnu', source_url=notice.source_url, title=kind,
            event_type=kind, start_date='2030-10-01', end_date='2030-10-01',
            review_status='auto', ai_extracted=True, review_reason=reason if kind=='event' else None)
        db.add(event); events.append(event)
    db.commit()
    return events


def test_normal_ai_bundle_registers_without_confirmation(client,db):
    events = bundle(db)
    r = client.post('/api/calendar/register',json={'event_id':events[0].id})
    assert r.status_code == 200
    user = get_or_create_dev_user(db)
    states = list(db.scalars(select(UserEvent).where(UserEvent.user_id==user.id, UserEvent.registered.is_(True))))
    assert {e.id for e in events[:2]} <= {s.event_id for s in states}
    assert events[2].id not in {s.event_id for s in states}
    assert all(e.ai_extracted for e in events)


def test_sibling_review_requires_confirmation_before_any_writes(client,db):
    events = bundle(db,'모델 날짜 원문 확인 요청')
    r = client.post('/api/calendar/register',json={'event_id':events[0].id})
    assert r.status_code == 400
    assert r.json()['message'] == '공지에서 날짜를 한 번 확인해 주세요.'
    user = get_or_create_dev_user(db)
    assert not list(db.scalars(select(UserEvent).where(UserEvent.user_id==user.id,
        UserEvent.event_id.in_([e.id for e in events]), UserEvent.registered.is_(True))))
    assert client.post('/api/calendar/register',json={'event_id':events[0].id,'confirmed':True}).status_code == 200


def test_interview_separate_registration_has_its_own_confirmation_policy(client,db):
    events=bundle(db);interview=events[2]
    assert client.post(f'/api/calendar/interviews/{interview.id}').status_code==200
    assert client.delete(f'/api/calendar/register/{interview.id}').status_code==200
    interview.review_reason='모델 면접 날짜 확인 요청';db.commit()
    assert client.post(f'/api/calendar/interviews/{interview.id}').status_code==400
    assert client.post(f'/api/calendar/interviews/{interview.id}',json={'confirmed':True}).status_code==200


def test_old_generic_status_warning_does_not_trigger_confirmation(client,db):
    events=bundle(db,'일정 상태를 원문에서 확인해 주세요.')
    events[1].schedule_status='unknown';db.commit()
    assert confirmation_reason(events[1]) is None
    assert client.get(f'/api/events/{events[0].id}').json()['events'][1]['review_required'] is False
    assert client.post('/api/calendar/register',json={'event_id':events[0].id}).status_code==200
    events[1].review_reason += ' / 사이트 접수 마감일과 AI 마감일이 다릅니다.';db.commit()
    assert confirmation_reason(events[1]) == '사이트 접수 마감일과 AI 마감일이 다릅니다.'
    assert client.post('/api/calendar/register',json={'event_id':events[0].id}).status_code==400


def test_schedule_status_alone_is_not_a_model_review_request():
    item = {'title':'접수','event_type':'application','start_date':'2030-10-01','end_date':'2030-10-23',
        'start_time':None,'end_time':None,'location':None,'attendance_mode':'not_applicable','schedule_status':'unknown'}
    raw={'schema_version':'notiai-model-candidate-v1','status':'candidate','review_required':True,
        'validation_errors':[], 'candidate':{'events':[item]}, 'metadata':{'review_required_events':[]}}
    assert parse_model_response(raw,'공지').events[0].review_reason is None
    raw['candidate']['events'][0]['review_reason']='모델이 날짜 확인을 요청했습니다.'
    assert parse_model_response(raw,'공지').events[0].review_reason == '모델이 날짜 확인을 요청했습니다.'
