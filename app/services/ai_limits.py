"""AI 채팅·추천 사용 제한 (비용 악용 방지).

- 사용자별: 채팅 분당·하루 횟수, 추천 새로 생성 하루 횟수, 채팅 동시 1개
- 서비스 전체: 오늘(Asia/Seoul) 채팅+추천 토큰 합계 상한 (openai_usage 기록 기준, 보강 작업은 제외)

횟수는 프로세스 메모리에 둔다(api 컨테이너 1개). 재시작하면 초기화되지만 전체 토큰 상한은 DB 기준이라 유지된다.
기준 수치(2026-11-17): 하루 활성 100명 × (채팅 5회 2.2k + 추천 3회 8.6k 토큰) ≈ 370만 토큰 → 상한 1,000만.
"""
import threading
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, time, timezone
from time import monotonic

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import OpenAIUsage
from app.services.planner import PlannerError
from app.timeutil import now_local

BUDGET_KINDS = ('chat', 'recommendation')
_lock = threading.Lock()
_minute: dict[int, deque] = defaultdict(deque)
_daily: dict[tuple, int] = defaultdict(int)
_active: set[int] = set()


def reset() -> None:
    """테스트용: 메모리 기록 초기화"""
    with _lock:
        _minute.clear(); _daily.clear(); _active.clear()


def _today():
    return now_local().date()


def _tokens_today() -> int:
    start = datetime.combine(_today(), time.min, tzinfo=get_settings().tz).astimezone(timezone.utc)
    with SessionLocal() as db:
        total = db.scalar(select(func.coalesce(func.sum(OpenAIUsage.input_tokens + OpenAIUsage.output_tokens), 0))
                          .where(OpenAIUsage.created_at >= start, OpenAIUsage.kind.in_(BUDGET_KINDS)))
    return int(total or 0)


def check_budget() -> None:
    limit = get_settings().ai_daily_token_budget
    if limit > 0 and _tokens_today() >= limit:
        raise PlannerError('오늘 AI 사용량이 모두 소진됐어요. 내일 다시 이용해 주세요.', 503)


def _take_daily(user_id: int, kind: str, limit: int) -> None:
    key = (user_id, kind, _today())
    if limit > 0 and _daily[key] >= limit:
        raise PlannerError('오늘 사용할 수 있는 횟수를 모두 썼어요. 내일 다시 이용해 주세요.', 429)
    # 지난 날짜 기록은 버린다.
    for stale in [k for k in _daily if k[2] != key[2]]:
        del _daily[stale]
    _daily[key] += 1


def take_chat(user_id: int) -> None:
    """채팅 1회를 쓴다. 넘으면 PlannerError(429/503)."""
    settings = get_settings()
    check_budget()
    with _lock:
        now = monotonic()
        recent = _minute[user_id]
        while recent and now - recent[0] >= 60:
            recent.popleft()
        if settings.chat_per_minute > 0 and len(recent) >= settings.chat_per_minute:
            raise PlannerError('잠시 후 다시 시도해 주세요.', 429)
        _take_daily(user_id, 'chat', settings.chat_per_day)
        recent.append(now)


def take_recommendation(user_id: int) -> None:
    """캐시에 없는 추천을 새로 만들 때 1회를 쓴다."""
    check_budget()
    with _lock:
        _take_daily(user_id, 'recommendation', get_settings().recommend_per_day)


@contextmanager
def single_chat(user_id: int):
    """사용자별 동시 채팅 1개."""
    with _lock:
        if user_id in _active:
            raise PlannerError('이전 답변을 받는 중이에요. 끝난 뒤 다시 보내 주세요.', 429)
        _active.add(user_id)
    try:
        yield
    finally:
        with _lock:
            _active.discard(user_id)
