from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text

from app.db import BACKEND_ROOT, run_migrations
from app.deps import get_or_create_dev_user
from app.models import (CrawlRun, Event, EventReport, Notification, Preference, ReviewLog,
                        ServiceFeedback, User, UserEvent, UserNotice)
from app.services import google, planner, planner_recommendations as recommendations
from app.services.dashboard import get_summary
from test_admin_review import login
from test_planner_opportunities import NOW, make_event, make_notice
from test_planner_recommendations import output_for, payload_for


@pytest.fixture(autouse=True)
def isolated_recommendations():
    recommendations._cache.clear()
    recommendations._refresh_times.clear()
    yield
    recommendations._cache.clear()
    recommendations._refresh_times.clear()


@pytest.mark.parametrize('kind', ['inconvenience', 'suggestion', 'praise', 'other'])
def test_feedback_creation(client, db, caplog, kind):
    response = client.post('/api/feedback', json={'type': kind, 'message': '  의견 본문  ', 'reply_email': 'reply@example.com'})
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {'id', 'created_at'}
    assert datetime.fromisoformat(body['created_at']).tzinfo is not None
    item = db.get(ServiceFeedback, body['id'])
    assert item.message == '의견 본문' and item.type == kind and item.reply_email == 'reply@example.com'
    assert item.user_id == get_or_create_dev_user(db).id
    assert '의견 본문' not in caplog.text and 'reply@example.com' not in caplog.text


@pytest.mark.parametrize('patch', [{'type': 'invalid'}, {'type': None}, {'message': '  '},
    {'message': 'x' * 2001}, {'message': 12}, {'reply_email': 'missing-at'},
    {'reply_email': 'x' * 255 + '@'}, {'reply_email': 12}])
def test_feedback_invalid_input(client, db, patch):
    response = client.post('/api/feedback', json={'type': 'other', 'message': '의견', **patch})
    assert response.status_code == 400 and set(response.json()) == {'message', 'errors'}
    assert db.scalar(select(func.count()).select_from(ServiceFeedback)) == 0


@pytest.mark.parametrize('email', [None, '', '  '])
def test_feedback_empty_email_and_message_bounds(client, db, email):
    response = client.post('/api/feedback', json={'type': 'other', 'message': ' ' + 'x' * 2000 + ' ', 'reply_email': email})
    assert response.status_code == 201
    assert db.get(ServiceFeedback, response.json()['id']).reply_email is None


@pytest.mark.parametrize('method,path,body', [
    ('POST', '/api/feedback', {'type': 'other', 'message': '의견'}),
    ('PUT', '/api/user/onboarding', {'done': True}),
    ('DELETE', '/api/users/me', None), ('GET', '/api/reports/mine', None)])
def test_new_endpoints_require_login(client, monkeypatch, method, path, body):
    monkeypatch.setattr('app.deps.get_settings', lambda: SimpleNamespace(dev_login=False))
    assert client.request(method, path, json=body).status_code == 401


def test_admin_feedback_authorization_order_and_pagination(client, db, monkeypatch):
    assert client.get('/api/admin/feedback').status_code == 401
    login(client, db, monkeypatch, admin=False)
    assert client.get('/api/admin/feedback').status_code == 403
    admin = login(client, db, monkeypatch)
    older = ServiceFeedback(user_id=admin.id, type='other', message='이전 의견', created_at=NOW - timedelta(days=1))
    newer = ServiceFeedback(user_id=admin.id, type='praise', message='새 의견', created_at=NOW)
    db.add_all([older, newer]); db.commit()
    body = client.get('/api/admin/feedback').json()
    assert body['total'] == 2 and [item['id'] for item in body['items']] == [newer.id, older.id]
    assert set(body['items'][0]) == {'id', 'user_id', 'type', 'message', 'reply_email', 'created_at'}
    assert client.get('/api/admin/feedback?limit=1&offset=1').json()['items'][0]['id'] == older.id
    for query in ('limit=201', 'limit=0', 'offset=-1'):
        assert client.get('/api/admin/feedback?' + query).status_code == 400


def test_preferences_partial_fields_and_notifications(client, db):
    original = {'ai_mode': 'focus', 'interests': ['academic'], 'enabled_sources': {'cbnu': False},
                'notifications': {'d3': False, 'd1': False, 'email': False}, 'auto_mode_recommend': False}
    assert client.put('/api/user/preferences', json=original).json() == original
    updated = client.put('/api/user/preferences', json={'interests': ['career', 'career']}).json()
    assert updated == {**original, 'interests': ['career']}
    updated = client.put('/api/user/preferences', json={'notifications': {'d1': True}}).json()
    assert updated['notifications'] == {'d3': False, 'd1': True, 'email': False}
    assert client.put('/api/user/preferences', json={}).json() == updated
    assert client.put('/api/user/preferences', json={'notifications': {}}).json() == updated
    db.expire_all()
    assert db.get(Preference, get_or_create_dev_user(db).id).ai_mode == 'focus'


@pytest.mark.parametrize('moment', [datetime(2026, 10, 11, 0, 4), datetime(2026, 10, 11, 0, 4, tzinfo=timezone.utc)])
def test_dashboard_iso_start_preserves_finish(db, moment):
    db.add(CrawlRun(site='cbnu', started_at=moment, finished_at=moment, status='done'))
    db.commit()
    summary = get_summary(db)
    assert summary.lastCollectedAt == '2026-10-11T09:04:00+09:00'
    assert summary.collectionFinishedAt == '09:04'


def test_dashboard_missing_timestamps(db):
    summary = get_summary(db)
    assert summary.lastCollectedAt == summary.collectionFinishedAt == '-'


def test_onboarding_roundtrip_preserves_profile(client):
    profile = client.get('/api/user').json()
    assert profile['onboarding_done'] is False
    for done in (True, False):
        response = client.put('/api/user/onboarding', json={'done': done})
        assert response.status_code == 200 and response.json() == {**profile, 'onboarding_done': done}
        assert client.get('/api/user').json()['onboarding_done'] is done


@pytest.mark.parametrize('body', [{}, {'done': 'true'}, {'done': 1}, {'done': None}])
def test_onboarding_validation(client, body):
    assert client.put('/api/user/onboarding', json=body).status_code == 400


@pytest.mark.parametrize('has_token,revoke_fails,foreign_keys', [(True, False, False), (True, True, False),
    (False, False, False), (True, False, True)])
def test_account_deletion_retains_other_users_and_review_history(client, db, monkeypatch, caplog,
                                                               has_token, revoke_fails, foreign_keys):
    monkeypatch.setattr('app.deps.get_settings', lambda: SimpleNamespace(dev_login=False))
    user = login(client, db, monkeypatch, admin=False)
    other = User(google_sub='another', name='another', email='another@example.com')
    user.google_refresh_token = 'test-only-revoke-token' if has_token else None
    db.add(other); db.flush()
    notice = make_notice(db)
    event = make_event(db, notice)
    for owner in (user, other):
        db.add_all([
            Preference(user_id=owner.id), UserEvent(user_id=owner.id, event_id=event.id, registered=True, google_event_id='keep-google'),
            UserNotice(user_id=owner.id, notice_id=notice.id),
            Notification(user_id=owner.id, event_id=event.id, kind='event_d1', target_date='2030-10-08', title='알림'),
            EventReport(user_id=owner.id, event_id=event.id, reason='date'),
            ServiceFeedback(user_id=owner.id, type='other', message='의견'),
            ReviewLog(notice_id=notice.id, actor_id=owner.id, action='approve', reason='검토', before=[], after=[]),
        ])
    db.commit()
    user_id, other_id = user.id, other.id
    calls = []
    def revoke(token):
        calls.append(token)
        if revoke_fails:
            raise google.GoogleError('test-only-revoke-token')
    monkeypatch.setattr(google, 'revoke_token', revoke)
    monkeypatch.setattr(google, 'delete_event', lambda *args: pytest.fail('Google calendar events must remain'))
    # Enable cascades for every connection used by the API as well as this session.
    from app.db import engine
    from sqlalchemy import event as sa_event
    def enable_foreign_keys(connection, *_):
        connection.execute('PRAGMA foreign_keys=ON')
    if foreign_keys and engine.dialect.name == 'sqlite':
        sa_event.listen(engine, 'checkout', enable_foreign_keys)
    try:
        response = client.delete('/api/users/me')
    finally:
        if foreign_keys and engine.dialect.name == 'sqlite':
            sa_event.remove(engine, 'checkout', enable_foreign_keys)
            with engine.connect() as connection:
                connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
    assert response.status_code == 204 and response.content == b''
    assert calls == (['test-only-revoke-token'] if has_token else [])
    assert 'test-only-revoke-token' not in caplog.text
    assert 'expires=Thu, 01 Jan 1970' in response.headers['set-cookie']
    db.expire_all()
    assert db.get(User, user_id) is None and db.get(User, other_id) is not None
    for model in (Preference, UserEvent, UserNotice, Notification, EventReport, ServiceFeedback):
        assert db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)) == 0
        assert db.scalar(select(func.count()).select_from(model).where(model.user_id == other_id)) == 1
    assert db.scalar(select(func.count()).select_from(ReviewLog).where(ReviewLog.actor_id.is_(None))) == 1
    assert db.scalar(select(func.count()).select_from(ReviewLog).where(ReviewLog.actor_id == other_id)) == 1
    assert db.get(Event, event.id) and db.get(type(notice), notice.id)
    assert client.get('/api/user').status_code == 401


@pytest.mark.parametrize('status', [200, 400])
def test_google_revoke_uses_form_post(monkeypatch, status):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(status)
    monkeypatch.setattr(google.httpx, 'post', post)
    if status == 200:
        google.revoke_token('test-only')
    else:
        with pytest.raises(google.GoogleError):
            google.revoke_token('test-only')
    assert calls == [('https://oauth2.googleapis.com/revoke', {'data': {'token': 'test-only'}, 'timeout': 15})]


def test_recommendations_refresh_replaces_cache_and_limits_user_mode(client, db, monkeypatch):
    monkeypatch.setattr('app.routers.planner.now_local', lambda: NOW)
    notice = make_notice(db); make_event(db, notice); db.commit()
    clock = [100.0]
    calls = []
    def judgment(payload):
        calls.append(payload)
        result = output_for(payload)
        for item in result['items']:
            item['reason'] = f'새 추천 {len(calls)}'
        return result
    monkeypatch.setattr(recommendations, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(recommendations, 'create_judgment', judgment)
    first = client.post('/api/planner/recommendations', json={'mode': 'priority'}).json()
    refreshed = client.post('/api/planner/recommendations', json={'mode': 'priority', 'refresh': True}).json()
    assert first['items'][0]['reason'] != refreshed['items'][0]['reason'] and len(calls) == 2
    assert refreshed['summary'] == '추천 활동 1개 중 3일 안에 진행할 활동이 1개 있어요.'
    assert client.post('/api/planner/recommendations', json={'mode': 'priority'}).json() == refreshed
    rejected = client.post('/api/planner/recommendations', json={'mode': 'priority', 'refresh': True})
    assert rejected.status_code == 429 and rejected.json() == {'message': '잠시 후 다시 시도해 주세요.'}
    clock[0] += 59.9
    assert client.post('/api/planner/recommendations', json={'mode': 'priority', 'refresh': True}).status_code == 429
    clock[0] = 160.0
    assert client.post('/api/planner/recommendations', json={'mode': 'priority', 'refresh': True}).status_code == 200
    assert client.post('/api/planner/recommendations', json={'mode': 'discover', 'refresh': True}).status_code == 200
    recommendations.allow_refresh(999, 'priority')


@pytest.mark.parametrize('refresh', [False, None, 1, 'true', [], {}])
def test_non_boolean_refresh_uses_cache(client, db, monkeypatch, refresh):
    monkeypatch.setattr('app.routers.planner.now_local', lambda: NOW)
    notice = make_notice(db); make_event(db, notice); db.commit()
    calls = []
    monkeypatch.setattr(recommendations, 'create_judgment', lambda payload: calls.append(payload) or output_for(payload))
    first = client.post('/api/planner/recommendations', json={'mode': 'priority'}).json()
    response = client.post('/api/planner/recommendations', json={'mode': 'priority', 'refresh': refresh})
    assert response.status_code == 200 and response.json() == first and len(calls) == 1
    assert recommendations._refresh_times == {}


def test_refresh_limit_is_atomic_bounded_and_prunes(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(recommendations, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(recommendations, 'REFRESH_MAX_ENTRIES', 2)
    def try_refresh(_):
        try:
            recommendations.allow_refresh(1, 'priority')
            return 200
        except planner.PlannerError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(try_refresh, range(8)))
    assert statuses.count(200) == 1 and statuses.count(429) == 7
    recommendations.allow_refresh(2, 'priority')
    with pytest.raises(planner.PlannerError) as error:
        recommendations.allow_refresh(3, 'priority')
    assert error.value.status == 429 and len(recommendations._refresh_times) == 2
    clock[0] = 60
    recommendations.allow_refresh(3, 'priority')
    assert len(recommendations._refresh_times) == 1


def test_failed_refresh_keeps_old_cache_and_consumes_cooldown(monkeypatch):
    payload = payload_for('priority', 1)
    monkeypatch.setattr(recommendations, 'create_judgment', output_for)
    first = recommendations.recommend(1, payload, {'o1': {'days_until_deadline': None}}, NOW)
    def fail(_):
        raise planner.PlannerError('실패', 502)
    monkeypatch.setattr(recommendations, 'create_judgment', fail)
    with pytest.raises(planner.PlannerError, match='실패'):
        recommendations.recommend(1, payload, {'o1': {'days_until_deadline': None}}, NOW, refresh=True)
    assert recommendations.recommend(1, payload, {'o1': {'days_until_deadline': None}}, NOW) == first
    with pytest.raises(planner.PlannerError) as error:
        recommendations.recommend(1, payload, {'o1': {'days_until_deadline': None}}, NOW, refresh=True)
    assert error.value.status == 429


@pytest.mark.parametrize('mode', ['priority', 'discover', 'focus'])
def test_recommendation_summary_counts_and_empty_cache(monkeypatch, mode):
    monkeypatch.setattr(recommendations, 'create_judgment', output_for)
    payload = payload_for(mode, 5)
    server = {f'o{index}': {'days_until_deadline': days} for index, days in enumerate([0, 3, 4, -1, None], 1)}
    result = recommendations.recommend(1, payload, server, NOW)
    assert result['summary'] == '추천 활동 5개 중 3일 안에 진행할 활동이 2개 있어요.'
    assert recommendations.recommend(1, payload, server, NOW)['summary'] == result['summary']
    empty = recommendations.recommend(1, payload_for(mode, 0), {}, NOW)
    assert empty['summary'] == '현재 추천할 활동이 없어요.'


@pytest.mark.parametrize('mode,resolved', [(None, 'general'), ('unknown', 'general'), ([], 'general'),
    ('general', 'general'), ('priority', 'priority'), ('discover', 'discover'), ('focus', 'focus'),
    ('study', 'general'), ('balanced', 'general'), ('explorer', 'discover')])
def test_chat_mode_prompts(client, monkeypatch, mode, resolved):
    calls = []
    monkeypatch.setattr(planner, 'create_reply', lambda instructions, messages: calls.append(instructions) or '답변')
    response = client.post('/api/planner/chat', json={'message': '내 일정', 'mode': mode})
    assert response.json() == {'reply': '답변'}
    assert planner.resolve_mode(mode) == resolved and planner.MODE_PROMPTS[resolved] in calls[0]
    assert 'json_schema' not in calls[0]


def test_my_reports_ownership_order_and_missing_event(client, db):
    user = get_or_create_dev_user(db)
    other = User(name='다른 사용자', email='other@example.com'); db.add(other); db.flush()
    event = make_event(db, title='행사 제목')
    older = EventReport(user_id=user.id, event_id=event.id, reason='date', memo='날짜 확인', created_at=NOW - timedelta(days=1))
    newer = EventReport(user_id=user.id, event_id=event.id, reason='other', memo='추가 의견', created_at=NOW)
    db.add_all([older, newer, EventReport(user_id=other.id, event_id=event.id, reason='other')]); db.commit()
    body = client.get('/api/reports/mine').json()
    assert [item['id'] for item in body] == [newer.id, older.id]
    assert body[1]['title'] == '행사 제목' and body[1]['reason'] == 'date' and body[1]['memo'] == '날짜 확인'
    assert all(item['status'] == 'received' and datetime.fromisoformat(item['created_at']).tzinfo for item in body)
    # A legacy SQLite database may contain a report whose event was removed with FK checks disabled.
    if db.bind.dialect.name == 'sqlite':
        db.execute(text('DELETE FROM events WHERE id = :id'), {'id': event.id}); db.commit()
        assert all(item['title'] is None for item in client.get('/api/reports/mine').json())


def test_my_reports_empty(client):
    assert client.get('/api/reports/mine').json() == []


def test_migration_backfill_downgrade_and_existing_schema(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "b12.db"}')
    with engine.begin() as connection:
        run_migrations(connection, '0009')
        for user_id, major, interests in [(1, '', '[]'), (2, '소프트웨어', '[]'), (3, '', '["academic"]'), (4, '   ', '[]')]:
            connection.execute(text("INSERT INTO users(id,email,name,created_at) VALUES (:id,'test@local','user','2030-01-01')"), {'id': user_id})
            connection.execute(text("INSERT INTO preferences(user_id,ai_mode,major,interests,enabled_sources,notifications,auto_mode_recommend) VALUES (:id,'priority',:major,:interests,'{}','{}',1)"),
                               {'id': user_id, 'major': major, 'interests': interests})
        run_migrations(connection)
        assert connection.execute(text('SELECT id,onboarding_done FROM users ORDER BY id')).all() == [(1, 0), (2, 1), (3, 1), (4, 0)]
        assert 'service_feedback' in inspect(connection).get_table_names()
        assert next(column for column in inspect(connection).get_columns('review_logs') if column['name'] == 'actor_id')['nullable']
        config = Config(); config.set_main_option('script_location', str(BACKEND_ROOT / 'migrations'))
        config.attributes['connection'] = connection
        command.downgrade(config, '0009')
        assert 'onboarding_done' not in {column['name'] for column in inspect(connection).get_columns('users')}
        assert 'service_feedback' not in inspect(connection).get_table_names()
        assert not next(column for column in inspect(connection).get_columns('review_logs') if column['name'] == 'actor_id')['nullable']
        assert connection.execute(text('SELECT count(*) FROM users')).scalar() == 4
        run_migrations(connection)
        # Re-applying against a schema already created by Base.metadata must be harmless.
        command.stamp(config, '0009')
        connection.execute(text('UPDATE users SET onboarding_done=0 WHERE id=2'))
        run_migrations(connection)
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0010'
        assert connection.execute(text('SELECT onboarding_done FROM users WHERE id=2')).scalar() == 0
        connection.execute(text("INSERT INTO notices(id,site,source_url,title_raw,raw_text,crawled_at) VALUES (1,'cbnu','https://example.com/review','공지','원문','2030-01-01')"))
        connection.execute(text("INSERT INTO review_logs(notice_id,actor_id,action,reason,before,after,created_at) VALUES (1,NULL,'approve','검토','[]','[]','2030-01-01')"))
        with pytest.raises(RuntimeError, match='Cannot downgrade retained reviews'):
            command.downgrade(config, '0009')
        assert connection.execute(text('SELECT count(*) FROM review_logs')).scalar() == 1
        assert 'service_feedback' in inspect(connection).get_table_names()
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0010'
    engine.dispose()
