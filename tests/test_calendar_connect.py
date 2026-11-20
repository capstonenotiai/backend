"""로그인은 이메일·프로필만, 캘린더 권한은 처음 일정 등록 때 따로 받는다."""
from urllib.parse import parse_qs, urlsplit

import pytest

from app.config import get_settings
from app.models import User
from app.services import google

CAL = google.CALENDAR_SCOPE


def _query(response):
    return {key: values[0] for key, values in parse_qs(urlsplit(response.headers['location']).query).items()}


@pytest.fixture()
def google_on(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, 'google_client_id', 'cid')
    monkeypatch.setattr(settings, 'google_client_secret', 'secret')
    monkeypatch.setattr(settings, 'dev_login', False)
    monkeypatch.setattr(google, 'fetch_userinfo', lambda token: {
        'sub': 'sub-1', 'email': 'student@example.com', 'name': '학생', 'email_verified': True})
    return client


def _login(client, monkeypatch, tokens=None):
    monkeypatch.setattr(google, 'exchange_code', lambda code: tokens or {'access_token': 'a', 'scope': 'openid email profile'})
    state = _query(client.get('/api/auth/google/login', follow_redirects=False))['state']
    response = client.get(f'/api/auth/google/callback?code=c&state={state}', follow_redirects=False)
    assert response.headers['location'].endswith('/dashboard')


def _connect(client, monkeypatch, tokens, next_path='/schedules?tab=all', code='c'):
    monkeypatch.setattr(google, 'exchange_code', lambda code: tokens)
    start = client.get('/api/auth/google/calendar', params={'next': next_path}, follow_redirects=False)
    state = _query(start)['state']
    suffix = f'code={code}&' if code else 'error=access_denied&'
    return start, client.get(f'/api/auth/google/callback?{suffix}state={state}', follow_redirects=False)


def test_login_asks_only_basic_scopes(google_on):
    params = _query(google_on.get('/api/auth/google/login', follow_redirects=False))
    assert params['scope'] == 'openid email profile'
    assert 'access_type' not in params and CAL not in params['scope']


def test_login_without_calendar_then_connect_and_register(google_on, monkeypatch, db):
    _login(google_on, monkeypatch)
    assert google_on.get('/api/user').json()['calendar_connected'] is False
    event = next(e for e in google_on.get('/api/events').json() if not e['registered'] and not e.get('review_required'))
    blocked = google_on.post('/api/calendar/register', json={'event_id': event['id']})
    assert blocked.status_code == 409 and blocked.headers['x-notiai-reason'] == 'calendar_not_connected'

    start, done = _connect(google_on, monkeypatch, {'access_token': 'a', 'refresh_token': 'r1', 'scope': f'openid email {CAL}'})
    params = _query(start)
    assert CAL in params['scope'] and params['access_type'] == 'offline' and params['login_hint'] == 'student@example.com'
    assert done.headers['location'] == get_settings().frontend_url.rstrip('/') + '/schedules?tab=all&calendar=connected'
    assert db.query(User).filter_by(google_sub='sub-1').one().google_refresh_token == 'r1'
    assert google_on.get('/api/user').json()['calendar_connected'] is True


def test_relogin_keeps_calendar_token(google_on, monkeypatch, db):
    _login(google_on, monkeypatch)
    _connect(google_on, monkeypatch, {'access_token': 'a', 'refresh_token': 'r1', 'scope': f'openid {CAL}'})
    _login(google_on, monkeypatch)
    assert db.query(User).filter_by(google_sub='sub-1').one().google_refresh_token == 'r1'


@pytest.mark.parametrize('tokens, code, result', [
    ({'access_token': 'a', 'refresh_token': 'r', 'scope': 'openid email'}, 'c', 'denied'),  # 캘린더 체크 해제
    (None, None, 'denied'),  # 동의 화면에서 취소
    ({'access_token': 'a', 'scope': f'openid {CAL}'}, 'c', 'failed'),  # refresh token 없음
])
def test_connect_not_saved_when_not_granted(google_on, monkeypatch, db, tokens, code, result):
    _login(google_on, monkeypatch)
    _, done = _connect(google_on, monkeypatch, tokens, code=code)
    assert _query(done)['calendar'] == result
    assert db.query(User).filter_by(google_sub='sub-1').one().google_refresh_token is None


def test_connect_with_other_google_account_is_rejected(google_on, monkeypatch, db):
    _login(google_on, monkeypatch)
    monkeypatch.setattr(google, 'fetch_userinfo', lambda token: {'sub': 'other', 'email': 'x@example.com', 'email_verified': True})
    _, done = _connect(google_on, monkeypatch, {'access_token': 'a', 'refresh_token': 'r', 'scope': CAL})
    assert _query(done)['calendar'] == 'wrong_account'
    assert db.query(User).filter_by(google_sub='sub-1').one().google_refresh_token is None


@pytest.mark.parametrize('next_path', ['//evil.example', 'https://evil.example', '/\\evil.example'])
def test_connect_next_stays_inside_app(google_on, monkeypatch, next_path):
    _login(google_on, monkeypatch)
    _, done = _connect(google_on, monkeypatch, {'access_token': 'a', 'refresh_token': 'r', 'scope': CAL}, next_path=next_path)
    assert done.headers['location'] == get_settings().frontend_url.rstrip('/') + '/dashboard?calendar=connected'


def test_connect_requires_login(google_on):
    assert google_on.get('/api/auth/google/calendar', follow_redirects=False).status_code == 401


def test_connect_in_dev_mode_returns_immediately(client):
    response = client.get('/api/auth/google/calendar', params={'next': '/planner'}, follow_redirects=False)
    assert response.headers['location'].endswith('/planner?calendar=connected')
