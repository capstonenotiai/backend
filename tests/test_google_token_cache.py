import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event as ThreadEvent

import httpx
import pytest

from app.models import Event
from app.services import google


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    with google._token_cache_lock:
        google._token_cache.clear()

    def no_network(*args, **kwargs):
        pytest.fail('Unexpected external HTTP call')

    for method in ('post', 'patch', 'delete', 'get'):
        monkeypatch.setattr(google.httpx, method, no_network)
    yield
    with google._token_cache_lock:
        google._token_cache.clear()


@pytest.fixture
def issuance(monkeypatch):
    calls = []

    def post_token(data):
        calls.append(data)
        return {'access_token': f'fake-access-{len(calls)}', 'expires_in': 120}

    monkeypatch.setattr(google, '_post_token', post_token)
    return calls


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(google.time, 'monotonic', lambda: now[0])
    return now


def calendar_call(operation):
    event = Event(title='테스트 일정', start_date='', end_date='2030-10-01', location='')
    if operation == 'post':
        return google.insert_event('fake-refresh', event)
    if operation == 'patch':
        return google.update_event('fake-refresh', 'fake-event', event)
    return google.delete_event('fake-refresh', 'fake-event')


def test_insert_update_delete_reuse_token(monkeypatch, issuance):
    headers = []

    def request(url, **kwargs):
        headers.append(kwargs['headers'])
        return httpx.Response(200, json={'id': 'fake-event'})

    for method in ('post', 'patch', 'delete'):
        monkeypatch.setattr(google.httpx, method, request)
    assert calendar_call('post') == 'fake-event'
    calendar_call('patch')
    calendar_call('delete')
    assert len(issuance) == 1
    assert headers == [{'Authorization': 'Bearer fake-access-1'}] * 3


@pytest.mark.parametrize('expires_in,ttl', [(120, 60), (None, 3540)])
def test_expiry_and_default_lifetime(monkeypatch, clock, expires_in, ttl):
    calls = []

    def issue(data):
        calls.append(data)
        response = {'access_token': f'fake-access-{len(calls)}'}
        if expires_in is not None:
            response['expires_in'] = expires_in
        return response

    monkeypatch.setattr(google, '_post_token', issue)
    assert google.refresh_access_token('fake-refresh') == 'fake-access-1'
    clock[0] += ttl - 1
    assert google.refresh_access_token('fake-refresh') == 'fake-access-1'
    clock[0] += 1
    assert google.refresh_access_token('fake-refresh') == 'fake-access-2'
    assert len(calls) == 2


@pytest.mark.parametrize('operation', ['post', 'patch', 'delete'])
@pytest.mark.parametrize('retry_status', [200, 401])
def test_401_refreshes_and_retries_once(monkeypatch, issuance, operation, retry_status):
    google.refresh_access_token('fake-refresh')
    headers = []

    def request(url, **kwargs):
        headers.append(kwargs['headers'])
        if len(headers) == 1:
            assert google._token_key('fake-refresh') in google._token_cache
            return httpx.Response(401)
        return httpx.Response(retry_status, json={'id': 'fake-event'})

    monkeypatch.setattr(google.httpx, operation, request)
    if retry_status == 401:
        with pytest.raises(google.GoogleError, match='401'):
            calendar_call(operation)
        assert not google._token_cache
    else:
        calendar_call(operation)
    assert len(issuance) == 2
    assert headers == [
        {'Authorization': 'Bearer fake-access-1'},
        {'Authorization': 'Bearer fake-access-2'},
    ]


def test_different_tokens_are_isolated(issuance):
    assert google.refresh_access_token('fake-refresh-a') == 'fake-access-1'
    assert google.refresh_access_token('fake-refresh-b') == 'fake-access-2'
    assert google.refresh_access_token('fake-refresh-a') == 'fake-access-1'
    assert len(issuance) == 2
    assert len(google._token_cache) == 2


@pytest.mark.parametrize('status', [200, 400, None])
def test_revoke_invalidates_even_on_failure(monkeypatch, issuance, status):
    google.refresh_access_token('fake-refresh')
    google.refresh_access_token('fake-other-refresh')

    def revoke(url, **kwargs):
        assert google._token_key('fake-refresh') not in google._token_cache
        assert url == google.REVOKE_URL
        assert kwargs == {'data': {'token': 'fake-refresh'}, 'timeout': 15}
        if status is None:
            raise httpx.ConnectError('fake transport failure')
        return httpx.Response(status)

    monkeypatch.setattr(google.httpx, 'post', revoke)
    if status == 200:
        google.revoke_token('fake-refresh')
    else:
        with pytest.raises(google.GoogleError if status else httpx.ConnectError):
            google.revoke_token('fake-refresh')
    assert google._token_key('fake-refresh') not in google._token_cache
    assert google._token_key('fake-other-refresh') in google._token_cache


def test_failed_issuance_does_not_leave_stale_cache(monkeypatch, issuance, clock):
    google.refresh_access_token('fake-refresh')
    clock[0] += 60

    def invalid_grant(data):
        raise google.GoogleError('token 요청 실패 (400)')

    monkeypatch.setattr(google, '_post_token', invalid_grant)
    with pytest.raises(google.GoogleError):
        google.refresh_access_token('fake-refresh')
    assert not google._token_cache


def test_keys_are_hashes_and_logs_do_not_contain_refresh_token(issuance, caplog):
    refresh = 'fake-refresh-for-privacy-test'
    google.refresh_access_token(refresh)
    assert list(google._token_cache) == [hashlib.sha256(refresh.encode()).hexdigest()]
    assert refresh not in repr(google._token_cache)
    assert refresh not in caplog.text


def test_cache_limit_and_expired_entry_cleanup(monkeypatch, issuance, clock):
    monkeypatch.setattr(google, '_TOKEN_CACHE_LIMIT', 2)
    google.refresh_access_token('fake-a')
    clock[0] += 1
    google.refresh_access_token('fake-b')
    google.refresh_access_token('fake-c')
    assert len(google._token_cache) == 2
    assert google._token_key('fake-a') not in google._token_cache
    clock[0] += 60
    google.refresh_access_token('fake-d')
    assert list(google._token_cache) == [google._token_key('fake-d')]


def test_short_lifetime_is_not_cached(monkeypatch, clock):
    monkeypatch.setattr(google, '_post_token', lambda data: {'access_token': 'fake-access', 'expires_in': 60})
    assert google.refresh_access_token('fake-refresh') == 'fake-access'
    assert not google._token_cache


def test_concurrent_requests_issue_once(monkeypatch):
    barrier = Barrier(4)
    issuing = ThreadEvent()
    release = ThreadEvent()
    calls = []

    def issue(data):
        calls.append(data)
        issuing.set()
        assert release.wait(timeout=5)
        return {'access_token': 'fake-access'}

    def refresh():
        barrier.wait(timeout=5)
        return google.refresh_access_token('fake-refresh')

    monkeypatch.setattr(google, '_post_token', issue)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(refresh) for _ in range(4)]
        try:
            assert issuing.wait(timeout=5)
        finally:
            release.set()
        assert [future.result(timeout=5) for future in futures] == ['fake-access'] * 4
    assert len(calls) == 1
