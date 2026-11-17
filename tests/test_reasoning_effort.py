"""AI 플래너 채팅의 reasoning effort 설정 전달."""
from types import SimpleNamespace

import openai
import pytest

from app.config import Settings
from app.services import planner


@pytest.mark.parametrize('effort,expected', [('low', {'effort': 'low'}), ('', None), ('  ', None)])
def test_chat_passes_reasoning_effort(monkeypatch, effort, expected):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = self

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status='completed', output_text='답변', usage=None)

    monkeypatch.setattr(openai, 'OpenAI', FakeClient)
    monkeypatch.setattr(planner, 'get_settings', lambda: Settings(_env_file=None, openai_api_key='fake', chat_reasoning_effort=effort))
    monkeypatch.setattr(planner.openai_usage, 'record', lambda *a, **k: None)
    assert planner.create_reply('지시', [{'role': 'user', 'content': '안녕'}]) == '답변'
    assert calls[0].get('reasoning') == expected


def test_defaults_are_low():
    settings = Settings(_env_file=None)
    assert settings.planner_reasoning_effort == 'low' and settings.chat_reasoning_effort == 'low'
