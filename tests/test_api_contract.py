r"""현재 API 계약. 의도적 변경 시 PowerShell: $env:UPDATE_API_SNAPSHOTS='1'; .\.venv\Scripts\python.exe -m pytest tests/test_api_contract.py; Remove-Item Env:UPDATE_API_SNAPSHOTS (diff 검토 필수)."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.deps import get_or_create_dev_user
from app.main import app
from app.models import EventReport, NoticeEnrichment, Notification
from app.services import planner_recommendations as service
from app.services.enrichment import empty_enrichment
from app.services.preferences import get_or_create_preference
from test_planner_opportunities import NOW, make_event, make_notice
from test_planner_recommendations import output_for

SNAPSHOTS = Path(__file__).parent / 'snapshots'


def frozen(name, actual):
    path = SNAPSHOTS / name
    if os.environ.get('UPDATE_API_SNAPSHOTS') == '1':
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    assert actual == json.loads(path.read_text(encoding='utf-8'))


def test_openapi_routes():
    routes = sorted([method.upper(), path] for path, operations in app.openapi()['paths'].items()
                    for method in operations if method in {'get', 'post', 'put', 'patch', 'delete', 'head', 'options'})
    frozen('api_routes.json', routes)


def key_tree(value):
    if isinstance(value, dict):
        return {key: key_tree(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        assert value, '계약 검사 배열은 테스트 데이터로 채워야 합니다.'
        shape = key_tree(value[0])
        assert all(key_tree(item) == shape for item in value)
        return [shape]
    return None


def test_frontend_response_keys(client, db, monkeypatch):
    monkeypatch.setattr('app.routers.planner.now_local', lambda: NOW)
    service._cache.clear()
    monkeypatch.setattr(service, 'create_judgment', output_for)
    user = get_or_create_dev_user(db)
    pref = get_or_create_preference(db, user)
    pref.interests = ['academic']
    notice = make_notice(db)
    event = make_event(db, notice, category='academic')
    db.add(NoticeEnrichment(notice_id=notice.id, state='done', prompt_version='enrich-v1', facts=empty_enrichment('ok')))
    db.add(Notification(user_id=user.id, event_id=event.id, kind='event_d1', target_date='2030-10-08',
                        title='내일 일정', message='일정을 확인해 주세요.'))
    db.commit()
    shapes = {}

    def call(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        assert response.status_code == 200, response.text
        return response.json()

    def keys(label, value):
        shapes[label] = sorted(value)

    events = call('GET', '/api/events')
    assert events
    keys('event', events[0])
    for item in events:
        assert sorted(item) == shapes['event']
    detail = call('GET', f'/api/events/{event.id}')
    keys('detail', detail)
    assert detail['events'] and all(sorted(item) == shapes['event'] for item in detail['events'])
    for method, path, kwargs in [
        ('GET', '/api/user', {}), ('GET', '/api/user/preferences', {}),
        ('PUT', '/api/user/preferences', {'json': {'ai_mode': 'focus', 'interests': ['academic']}}),
        ('PUT', '/api/user/profile', {'json': {'major': '소프트웨어학부', 'grade': 3, 'enrollment_status': 'enrolled'}}),
        ('PUT', '/api/user/onboarding', {'json': {'done': True}}),
        ('GET', '/api/notifications', {}), ('GET', '/api/notifications/unread-count', {}),
        ('GET', '/api/dashboard', {}),
    ]:
        body = call(method, path, **kwargs)
        keys(method + ' ' + path, body)
        if path.endswith('preferences'):
            shapes[method + ' preferences nested'] = key_tree(body)
        elif path == '/api/notifications':
            assert body['items']
            shapes['notification item'] = sorted(body['items'][0])
        elif path == '/api/dashboard':
            assert body['sources']
            shapes['dashboard source'] = sorted(body['sources'][0])
    call('POST', '/api/calendar/register', json={'event_id': str(event.id)})
    calendar = call('GET', '/api/calendar/events')
    assert calendar and all(sorted(item) == shapes['event'] for item in calendar)
    for mode in ('priority', 'discover', 'focus'):
        body = call('POST', '/api/planner/recommendations', json={'mode': mode})
        keys('recommendations ' + mode, body)
        assert body['items']
        shapes['recommendation item ' + mode] = sorted(body['items'][0])
        assert all(sorted(item) == shapes['recommendation item ' + mode] for item in body['items'])
        shapes['priority_context ' + mode] = sorted(body['items'][0]['priority_context'])
    feedback = client.post('/api/feedback', json={'type': 'other', 'message': '서비스 의견'})
    assert feedback.status_code == 201
    keys('POST /api/feedback', feedback.json())
    db.add(EventReport(user_id=user.id, event_id=event.id, reason='date', memo='날짜 확인'))
    db.commit()
    reports = call('GET', '/api/reports/mine')
    assert reports
    shapes['report item'] = sorted(reports[0])
    from test_admin_review import login
    login(client, db, monkeypatch)
    admin_feedback = call('GET', '/api/admin/feedback')
    keys('GET /api/admin/feedback', admin_feedback)
    shapes['admin feedback item'] = sorted(admin_feedback['items'][0])
    frozen('api_response_keys.json', shapes)
    service._cache.clear()


@pytest.mark.parametrize('case', ['missing', 'planner', 'validation', 'auth'])
def test_error_response_keys(client, monkeypatch, case):
    if case == 'missing':
        response = client.get('/api/events/999999')
        status, keys = 404, ['message']
    elif case == 'planner':
        response = client.post('/api/planner/recommendations', json={'mode': 'bad'})
        status, keys = 400, ['message']
    elif case == 'validation':
        response = client.put('/api/user/profile', json={'grade': 0})
        status, keys = 400, ['errors', 'message']
    else:
        monkeypatch.setattr('app.deps.get_settings', lambda: SimpleNamespace(dev_login=False))
        response = client.get('/api/user')
        status, keys = 401, ['message']
    assert response.status_code == status
    assert sorted(response.json()) == keys
    assert isinstance(response.json()['message'], str)
