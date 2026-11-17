"""POST /api/planner/chat/stream — 답변 조각을 SSE 로 바로 보낸다."""
import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.config import Settings
from app.services import planner


def parse_sse(text):
    events = []
    for block in text.strip().split('\n\n'):
        lines = dict(line.split(': ', 1) for line in block.splitlines())
        events.append((lines['event'], json.loads(lines['data'])))
    return events


def fake_openai(monkeypatch, events=None, error=None, effort='low'):
    calls, recorded = [], []

    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = self

        def create(self, **kwargs):
            calls.append(kwargs)
            if error:
                raise error
            return iter(events)

    monkeypatch.setattr(openai, 'OpenAI', FakeClient)
    monkeypatch.setattr(planner, 'get_settings', lambda: Settings(_env_file=None, openai_api_key='fake', chat_reasoning_effort=effort))
    monkeypatch.setattr(planner.openai_usage, 'record', lambda *args: recorded.append(args))
    return calls, recorded


def delta(text):
    return SimpleNamespace(type='response.output_text.delta', delta=text)


COMPLETED = SimpleNamespace(type='response.completed', response=SimpleNamespace(usage=None))


def test_stream_sends_deltas_then_full_reply(client, monkeypatch):
    calls, recorded = fake_openai(monkeypatch, [SimpleNamespace(type='response.created'), delta('이번 주 '), delta('마감은\n\n'), delta('2건이에요.'), COMPLETED])
    response = client.post('/api/planner/chat/stream', json={'message': '이번 주 마감', 'mode': 'general', 'history': []})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    assert 'no-transform' in response.headers['cache-control']
    events = parse_sse(response.text)
    assert events == [('delta', {'text': '이번 주 '}), ('delta', {'text': '마감은\n\n'}), ('delta', {'text': '2건이에요.'}),
                      ('done', {'reply': '이번 주 마감은\n\n2건이에요.'})]
    assert calls[0]['stream'] is True and calls[0]['store'] is False and calls[0]['reasoning'] == {'effort': 'low'}
    assert calls[0]['input'][-1] == {'role': 'user', 'content': '이번 주 마감'}
    assert len(recorded) == 1 and recorded[0][0] == 'chat'


@pytest.mark.parametrize('status,expected', [(429, 503), (500, 502)])
def test_stream_openai_status_error_becomes_error_event(client, monkeypatch, status, expected):
    error = openai.APIStatusError('x', response=httpx.Response(status, request=httpx.Request('POST', 'https://example.com')), body=None)
    fake_openai(monkeypatch, error=error)
    events = parse_sse(client.post('/api/planner/chat/stream', json={'message': '안녕'}).text)
    assert events == [('error', {'message': events[0][1]['message'], 'status': expected})]


def test_stream_failure_after_partial_text_and_empty_reply(client, monkeypatch):
    fake_openai(monkeypatch, [delta('일부'), SimpleNamespace(type='response.failed')])
    events = parse_sse(client.post('/api/planner/chat/stream', json={'message': '안녕'}).text)
    assert events[0] == ('delta', {'text': '일부'}) and events[-1][0] == 'error' and events[-1][1]['status'] == 502
    fake_openai(monkeypatch, [delta('  '), COMPLETED])
    events = parse_sse(client.post('/api/planner/chat/stream', json={'message': '안녕'}).text)
    assert events[-1][0] == 'error'


def test_stream_validates_input_before_opening_stream(client, monkeypatch):
    calls, _ = fake_openai(monkeypatch, [])
    response = client.post('/api/planner/chat/stream', content=b'not json', headers={'content-type': 'application/json'})
    assert response.status_code == 400 and response.json()['message']
    response = client.post('/api/planner/chat/stream', json={'message': ''})
    assert response.status_code == 400 and 'message' in response.json()
    assert not calls


def test_stream_missing_key_reports_setup_error(client, monkeypatch):
    monkeypatch.setattr(planner, 'get_settings', lambda: Settings(_env_file=None, openai_api_key=''))
    events = parse_sse(client.post('/api/planner/chat/stream', json={'message': '안녕'}).text)
    assert events == [('error', {'message': 'AI 플래너 서버 설정이 완료되지 않았습니다.', 'status': 500})]
