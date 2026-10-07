"""Structured recommendations and a bounded, process-local 30 minute cache."""
import hashlib
import json
import logging
from copy import deepcopy
from threading import Lock
from time import monotonic

from app.config import get_settings
from app.services import openai_usage
from app.services.planner import PlannerError
from app.services.planner_prompts import PLANNER_PROMPT_VERSION, instructions_for, schema_for
from app.services.planner_validation import validate_output

log = logging.getLogger(__name__)
CACHE_SECONDS = 30 * 60
CACHE_MAX_ENTRIES = 256
_cache = {}
_cache_lock = Lock()


def parse_request(body):
    mode = body.get('mode') if isinstance(body, dict) else None
    if not isinstance(mode, str) or mode not in ('priority', 'discover', 'focus'):
        raise PlannerError('추천 모드는 priority, discover, focus 중 하나여야 합니다.')
    return mode


def create_judgment(payload):
    settings = get_settings()
    if not settings.openai_api_key:
        log.error('[planner/recommendations] OPENAI_API_KEY is not configured')
        raise PlannerError('AI 플래너 서버 설정이 완료되지 않았습니다.', 500)

    import openai

    try:
        with openai.OpenAI(api_key=settings.openai_api_key, max_retries=1, timeout=60) as client:
            response = client.responses.create(
                model=settings.planner_model,
                instructions=instructions_for(payload['mode']),
                input=[{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                text={'format': {'type': 'json_schema', 'name': 'planner_' + payload['mode'],
                                 'strict': True, 'schema': schema_for(payload['mode'])}},
                store=False, max_output_tokens=settings.planner_max_output_tokens,
            )
        openai_usage.record('recommendation', settings.planner_model, response)
    except openai.APIStatusError as error:
        log.error('[planner/recommendations] OpenAI request failed status=%s', error.status_code)
        if error.status_code == 429:
            raise PlannerError('요청이 많아 잠시 후 다시 시도해 주세요.', 503) from None
        raise PlannerError('AI 응답 생성에 실패했습니다.', 502) from None
    except openai.OpenAIError as error:
        log.error('[planner/recommendations] OpenAI request failed: %s', type(error).__name__)
        raise PlannerError('AI 응답 생성에 실패했습니다.', 502) from None
    if response.status != 'completed' or not response.output_text:
        raise PlannerError('AI 응답 생성에 실패했습니다.', 502)
    try:
        return json.loads(response.output_text)
    except (json.JSONDecodeError, TypeError):
        # Completed but invalid JSON is repaired using server defaults.
        return None


def recommend(user_id, payload, server_items, now):
    settings = get_settings()
    snapshot = {'payload': payload, 'server_items': server_items, 'prompt_version': PLANNER_PROMPT_VERSION,
                'model': settings.planner_model, 'max_output_tokens': settings.planner_max_output_tokens}
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    key = (user_id, payload['mode'], digest)
    clock = monotonic()
    with _cache_lock:
        for stale in [entry for entry, (expires, _) in _cache.items() if expires <= clock]:
            del _cache[stale]
        cached = _cache.get(key)
        if cached:
            return deepcopy(cached[1])
    if payload['opportunities']:
        judgments, corrections = validate_output(payload, create_judgment(payload))
    else:
        judgments, corrections = [], []
    result = {'mode': payload['mode'], 'generated_at': now.isoformat(),
              'items': [{**item, **server_items[item['opportunity_id']]} for item in judgments],
              'needs_grouping_check': deepcopy(payload.get('needs_grouping_check', [])), 'corrections': corrections}
    with _cache_lock:
        while len(_cache) >= CACHE_MAX_ENTRIES:
            del _cache[min(_cache, key=lambda entry: _cache[entry][0])]
        _cache[key] = (monotonic() + CACHE_SECONDS, deepcopy(result))
    return result
