import base64
import json

import pytest
from itsdangerous import TimestampSigner
from sqlalchemy import select

from app.config import get_settings
from app.models import Event, Notice, ReviewLog, User, UserEvent
from app.services.extractor import ExtractionResult, parse_model_response
from app.services.pipeline import extract_pending
from app.services.google import build_calendar_body

ORIGIN = {'Origin': 'http://localhost:5173'}


def login(client, db, monkeypatch, admin=True):
    monkeypatch.setattr(get_settings(), 'admin_emails', 'owner@example.com')
    user = User(google_sub='owner' if admin else 'ordinary', email='owner@example.com' if admin else 'user@example.com', name='tester')
    db.add(user)
    db.commit()
    data = base64.b64encode(json.dumps({'user_id': user.id}).encode())
    client.cookies.set('notiai_session', TimestampSigner(get_settings().session_secret).sign(data).decode())
    return user


def notice(db):
    item = Notice(site='cbnu', source_url='https://example.com/notice', title_raw='원문', raw_text='접수 후 본행사', extraction_state='needs_review', extraction_result={'original': True})
    db.add(item)
    db.commit()
    return item


def payload(revision=0, **overrides):
    return {'revision': revision, 'action': 'approve', 'reason': '원문 확인', 'events': [
        {'title': '접수', 'event_type': 'application', 'end_date': '2030-10-01'},
        {'title': '본행사', 'event_type': 'event', 'start_date': '2030-10-03', 'end_date': '2030-10-03', 'location': '대강당'},
    ], **overrides}


def test_admin_default_deny_and_regular_user(client, db, monkeypatch):
    assert client.get('/api/admin/notices').status_code == 401
    login(client, db, monkeypatch, admin=False)
    assert client.get('/api/user').json()['is_admin'] is False
    assert client.get('/api/admin/notices').status_code == 403
    assert client.post('/api/admin/notices/1/review', json=payload(), headers=ORIGIN).status_code == 403


def test_admin_origin_and_history_conflict(client, db, monkeypatch):
    owner = login(client, db, monkeypatch)
    item = notice(db)
    path = f'/api/admin/notices/{item.id}/review'
    assert client.get('/api/user').json()['is_admin'] is True
    assert client.post(path, json=payload()).status_code == 403
    assert client.post(path, json=payload(), headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post(path, json=payload(), headers=ORIGIN).status_code == 200
    assert client.post(path, json=payload(), headers=ORIGIN).status_code == 409
    details = client.get(f'/api/admin/notices/{item.id}').json()
    assert len(details['events']) == 2
    assert details['model_output'] == {'original': True}
    assert details['history'][0]['actor_id'] == owner.id
    assert details['history'][0]['before'] == []
    assert len(details['history'][0]['after']) == 2
    assert len(db.scalars(select(ReviewLog)).all()) == 1


def test_visibility_and_personal_sync(client, db, monkeypatch):
    owner = login(client, db, monkeypatch)
    item = notice(db)
    path = f'/api/admin/notices/{item.id}/review'
    assert client.post(path, json=payload(action='save'), headers=ORIGIN).status_code == 200
    details = client.get(f'/api/admin/notices/{item.id}').json()
    event_id = details['events'][0]['id']
    assert str(event_id) not in [e['id'] for e in client.get('/api/events').json()]
    assert client.post('/api/calendar/register', json={'event_id': str(event_id)}).status_code == 400
    edits = [{k: v for k, v in e.items() if k not in ('revision', 'review_status', 'review_reason')} for e in details['events']]
    assert client.post(path, json=payload(1, events=edits), headers=ORIGIN).status_code == 200
    assert client.post('/api/calendar/register', json={'event_id': str(event_id)}).status_code == 200
    other = User(google_sub='other', email='other@example.com', name='other')
    db.add(other)
    db.flush()
    other_state = UserEvent(user_id=other.id, event_id=event_id, registered=True, sync_status='synced')
    db.add(other_state)
    db.commit()
    edits[0]['end_date'] = '2030-10-02'
    assert client.post(path, json=payload(2, events=edits), headers=ORIGIN).status_code == 200
    assert client.post(f'/api/calendar/sync/{event_id}').status_code == 200
    db.expire_all()
    own = db.scalar(select(UserEvent).where(UserEvent.user_id == owner.id, UserEvent.event_id == event_id))
    assert own.sync_status == 'synced'
    assert db.get(UserEvent, other_state.id).sync_status == 'needs_sync'
    assert client.post(path, json=payload(3, action='reject', events=[]), headers=ORIGIN).status_code == 200
    assert client.post(f'/api/calendar/sync/{event_id}').json()['registered'] is False
    assert str(event_id) not in [e['id'] for e in client.get('/api/events').json()]


@pytest.mark.parametrize('edit', [
    {'end_date': '2030-02-30'}, {'start_date': '2030-10-05'},
    {'start_time': '25:00'}, {'attendance_mode': 'online', 'location': '대강당'},
    {'id': 987654}, {'actor_id': 1},
])
def test_invalid_admin_edits(client, db, monkeypatch, edit):
    login(client, db, monkeypatch)
    item = notice(db)
    response = client.post(f'/api/admin/notices/{item.id}/review', headers=ORIGIN,
        json=payload(events=[{'title': '행사', 'end_date': '2030-10-03', **edit}]))
    assert response.status_code in (400, 422)
    db.refresh(item)
    assert item.revision == 0


def test_empty_extraction_not_repeated(db):
    item = notice(db)
    item.extraction_state = 'pending'
    db.commit()
    class Empty:
        name = 'test'
        def extract(self, title, body):
            return ExtractionResult([], {'events': []})
    assert extract_pending(db, Empty()) == 1
    assert extract_pending(db, Empty()) == 0
    assert item.extraction_state == 'needs_review'


def test_v2_multiple_events_and_time_body(db):
    raw = {'schema_version': 'student-calendar-v2.0', 'coverage': 'complete', 'relations': [], 'issues': [], 'events': [
        {'event_id': 'a', 'title': '접수', 'event_type': 'application', 'end_date': '2030-10-01', 'end_time': '18:00'},
        {'event_id': 'b', 'title': '본행사', 'event_type': 'event', 'start_date': '2030-10-03', 'end_date': '2030-10-03', 'locations': [{'name': '대강당'}]},
    ]}
    result = parse_model_response(raw, '공지')
    assert len(result.events) == 2
    assert all(e.review_status == 'needs_review' for e in result.events)
    assert result.raw == raw
    event = Event(title='접수', end_date='2030-10-01', end_time='18:00')
    body = build_calendar_body(event)
    assert body['start']['dateTime'] == '2030-10-01T18:00:00+09:00'
    assert body['end']['dateTime'] == '2030-10-01T18:01:00+09:00'


def test_google_patch_clears_old_location_and_time(monkeypatch):
    from app.services import google
    monkeypatch.setattr(google, 'refresh_access_token', lambda token: 'test-access')
    calls = []
    def patch(url, **kwargs):
        calls.append(kwargs['json'])
        return type('Response', (), {'status_code': 200})()
    monkeypatch.setattr(google.httpx, 'patch', patch)
    google.update_event('test-refresh', 'existing-id', Event(title='수정', start_date='', end_date='2030-10-01', location=''))
    assert calls[0]['location'] == ''
    assert calls[0]['start']['dateTime'] is None
    assert calls[0]['end']['date'] == '2030-10-02'


def test_dev_user_cannot_be_admin(client, db, monkeypatch):
    from app.deps import get_or_create_dev_user, is_admin
    user = get_or_create_dev_user(db)
    monkeypatch.setattr(get_settings(), 'admin_emails', user.email)
    assert is_admin(user) is False
    data = base64.b64encode(json.dumps({'user_id': user.id}).encode())
    client.cookies.set('notiai_session', TimestampSigner(get_settings().session_secret).sign(data).decode())
    assert client.get('/api/admin/notices').status_code == 403
