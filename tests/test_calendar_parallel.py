"""같은 공지 일정 일괄 등록/해제: Google 호출 병렬화와 부분 실패 처리."""
import threading

from sqlalchemy import select

from app.deps import get_or_create_dev_user
from app.models import UserEvent
from app.services import google
from tests.test_service_v9 import extracted


def _register(client, events):
    return client.post('/api/calendar/register', json={'event_id': events[0].id, 'confirmed': True})


def _states(db, user, events):
    db.expire_all()
    return {s.event_id: s for s in db.scalars(select(UserEvent).where(
        UserEvent.user_id == user.id, UserEvent.event_id.in_([e.id for e in events])))}


def test_bundle_google_writes_run_concurrently(client, db, monkeypatch):
    _, events, _ = extracted(db)
    user = get_or_create_dev_user(db); user.google_refresh_token = 'test-only'; db.commit()
    barrier = threading.Barrier(2, timeout=5)

    def insert(token, event):
        barrier.wait()  # 두 호출이 동시에 진행 중이어야 통과한다 (순차 실행이면 timeout)
        return f'google-{event.id}'
    monkeypatch.setattr(google, 'insert_event', insert)
    assert _register(client, events).status_code == 200
    states = _states(db, user, events)
    assert states[events[0].id].google_event_id == f'google-{events[0].id}'
    assert states[events[1].id].google_event_id == f'google-{events[1].id}'


def test_partial_failure_keeps_successful_siblings_and_retry_skips_them(client, db, monkeypatch):
    _, events, _ = extracted(db)
    user = get_or_create_dev_user(db); user.google_refresh_token = 'test-only'; db.commit()
    calls = []

    def flaky(token, event):
        calls.append(event.id)
        if event.id == events[1].id:
            raise google.GoogleError('boom')
        return f'google-{event.id}'
    monkeypatch.setattr(google, 'insert_event', flaky)
    r = _register(client, events)
    assert r.status_code == 502
    states = _states(db, user, events)
    assert states[events[0].id].registered and states[events[0].id].google_event_id == f'google-{events[0].id}'
    assert not states[events[1].id].registered

    calls.clear()
    monkeypatch.setattr(google, 'insert_event', lambda token, event: calls.append(event.id) or f'google-{event.id}')
    assert _register(client, events).status_code == 200
    assert calls == [events[1].id]  # 이미 등록된 일정은 다시 보내지 않는다


def test_unregister_deletes_concurrently_and_keeps_failed_one(client, db, monkeypatch):
    _, events, _ = extracted(db)
    user = get_or_create_dev_user(db); user.google_refresh_token = 'test-only'; db.commit()
    monkeypatch.setattr(google, 'insert_event', lambda token, event: f'google-{event.id}')
    assert _register(client, events).status_code == 200

    def delete(token, google_id):
        if google_id == f'google-{events[1].id}':
            raise google.GoogleError('boom')
    monkeypatch.setattr(google, 'delete_event', delete)
    assert client.delete(f'/api/calendar/register/{events[0].id}').status_code == 502
    states = _states(db, user, events)
    assert not states[events[0].id].registered and states[events[0].id].google_event_id is None
    assert states[events[1].id].registered and states[events[1].id].google_event_id == f'google-{events[1].id}'
