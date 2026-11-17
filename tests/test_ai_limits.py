"""AI 채팅·추천 사용 제한."""
import json
from datetime import timedelta

import pytest

from app.config import Settings
from app.deps import get_or_create_dev_user
from app.models import OpenAIUsage, utcnow
from app.services import ai_limits, planner
from app.services.planner import PlannerError


def limits(monkeypatch, **values):
    settings = Settings(_env_file=None, **values)
    monkeypatch.setattr(ai_limits, 'get_settings', lambda: settings)
    return settings


@pytest.fixture()
def fake_reply(monkeypatch):
    calls = []
    monkeypatch.setattr(planner, 'create_reply', lambda instructions, messages: calls.append(messages) or '답변')
    monkeypatch.setattr(planner, 'stream_reply', lambda instructions, messages: iter([('delta', '답'), ('done', '답변')]))
    return calls


def test_chat_per_minute_and_per_day(client, db, monkeypatch, fake_reply):
    limits(monkeypatch, chat_per_minute=2, chat_per_day=3)
    send = lambda: client.post('/api/planner/chat', json={'message': '안녕'})
    assert [send().status_code for _ in range(2)] == [200, 200]
    blocked = send()
    assert blocked.status_code == 429 and blocked.json()['message'] == '잠시 후 다시 시도해 주세요.'
    assert len(fake_reply) == 2  # 막힌 요청은 OpenAI 를 부르지 않는다
    # 1분이 지나면 분당 제한은 풀리지만 하루 3회 제한에 걸린다.
    clock = [ai_limits.monotonic() + 61]
    monkeypatch.setattr(ai_limits, 'monotonic', lambda: clock[0])
    assert send().status_code == 200
    clock[0] += 61
    daily = send()
    assert daily.status_code == 429 and '오늘' in daily.json()['message']


def test_invalid_input_does_not_use_quota(client, monkeypatch, fake_reply):
    limits(monkeypatch, chat_per_minute=1, chat_per_day=1)
    assert client.post('/api/planner/chat', json={'message': ''}).status_code == 400
    assert client.post('/api/planner/chat', json={'message': '안녕'}).status_code == 200


def test_stream_uses_same_quota_and_returns_json_before_stream(client, monkeypatch, fake_reply):
    limits(monkeypatch, chat_per_minute=1)
    first = client.post('/api/planner/chat/stream', json={'message': '안녕'})
    assert first.status_code == 200 and 'event: done' in first.text
    second = client.post('/api/planner/chat/stream', json={'message': '안녕'})
    assert second.status_code == 429 and second.headers['content-type'].startswith('application/json')
    assert client.post('/api/planner/chat', json={'message': '안녕'}).status_code == 429


def test_one_chat_at_a_time_per_user():
    with ai_limits.single_chat(1):
        with pytest.raises(PlannerError) as error, ai_limits.single_chat(1):
            pass
        assert error.value.status == 429
        with ai_limits.single_chat(2):  # 다른 사용자는 영향 없음
            pass
    with ai_limits.single_chat(1):  # 끝나면 다시 쓸 수 있다
        pass


def test_daily_token_budget_counts_only_chat_and_recommendation(client, db, monkeypatch, fake_reply):
    limits(monkeypatch, ai_daily_token_budget=1000)
    db.add_all([OpenAIUsage(kind='enrichment', model='m', input_tokens=5000, output_tokens=0),
                OpenAIUsage(kind='chat', model='m', input_tokens=900, output_tokens=50),
                OpenAIUsage(kind='chat', model='m', input_tokens=900, output_tokens=0, created_at=utcnow() - timedelta(days=2))])
    db.commit()
    assert client.post('/api/planner/chat', json={'message': '안녕'}).status_code == 200  # 950 < 1000
    db.add(OpenAIUsage(kind='recommendation', model='m', input_tokens=40, output_tokens=10)); db.commit()
    over = client.post('/api/planner/chat', json={'message': '안녕'})
    assert over.status_code == 503 and '소진' in over.json()['message']
    limits(monkeypatch, ai_daily_token_budget=0)  # 0 이면 끈다
    assert client.post('/api/planner/chat', json={'message': '안녕'}).status_code == 200


def test_recommendation_counts_only_uncached_generation(client, db, monkeypatch):
    from app.services import planner_recommendations as service
    from tests.test_planner_recommendations import payload_for, output_for
    limits(monkeypatch, recommend_per_day=1)
    calls = []
    monkeypatch.setattr(service, 'create_judgment', lambda payload: calls.append(1) or output_for(payload))
    payload = payload_for('priority', 2)
    from tests.test_frontend_requests import NOW
    first = service.recommend(1, payload, {o['opportunity_id']: {} for o in payload['opportunities']}, NOW)
    assert first and len(calls) == 1
    # 같은 입력은 캐시라 횟수를 쓰지 않는다.
    service.recommend(1, payload, {o['opportunity_id']: {} for o in payload['opportunities']}, NOW)
    assert len(calls) == 1
    other = payload_for('discover', 2)
    with pytest.raises(PlannerError) as error:
        service.recommend(1, other, {o['opportunity_id']: {} for o in other['opportunities']}, NOW)
    assert error.value.status == 429 and len(calls) == 1
    # 다른 사용자는 따로 센다.
    service.recommend(2, other, {o['opportunity_id']: {} for o in other['opportunities']}, NOW)
    assert len(calls) == 2
