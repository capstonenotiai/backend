"""
AI 플래너 — 프론트 functions/ (Cloudflare Pages Functions) 의 planner/chat 을 옮긴 것.

API 계약은 그대로 유지: { message, mode, history } → { reply }
추가된 점: 사용자의 NotiAI 일정 목록을 system prompt 에 넣어 실제 일정 기반으로 답한다.
"""
import logging
from datetime import date

from app.config import get_settings
from app.services import openai_usage
from app.schemas import EventOut

log = logging.getLogger(__name__)

# ── 입력 제한 (functions/lib/validation.js 와 동일) ─────────────────────────
LIMITS = {
    "body_bytes": 64 * 1024,
    "message_chars": 3000,
    "history_items": 12,
    "history_content_chars": 2000,
}
CONTEXT_EVENT_LIMIT = 20

# ── 채팅용 프롬프트 ─────────────────────────────────────────────────────
BASE_PROMPT = """
너는 대학생 일정 관리 서비스 NotiAI의 AI Planner다.

답변은 한국어로 한다.
사용자가 제공한 사실과 일정 정보만 기반으로 답한다.
확인되지 않은 일정, 날짜, 혜택, 자격조건을 만들어내지 않는다.
모르는 정보는 모른다고 말하고, 공지 원문이나 담당 부서 확인을 권한다.
사용자의 명시적인 요청이 현재 모드보다 우선한다.
답변은 간결하고 실용적으로 한다.
서식은 **굵게** 와 '- ' 목록만 사용하고, 제목(#)이나 표는 쓰지 않는다.
""".strip()

MODE_PROMPTS = {
    "general": """
[현재 모드: 내 일정 질문]
- 사용자의 등록 일정과 공지에 대한 질문에 특정 카테고리에 치우치지 않고 답한다.
""".strip(),
    "priority": """
[현재 모드: 우선순위]
- 제공된 일정과 공지에서 먼저 해야 할 행동과 준비·확인할 내용을 설명한다.
- 확인된 날짜와 사용자 상태만 근거로 삼는다.
""".strip(),
    "discover": """
[현재 모드: 활동 탐색]
- 제공된 공지에서 참여할 활동의 혜택과 지원 조건, 확인할 내용을 설명한다.
- 존재하지 않는 혜택이나 자격조건을 만들어내지 않는다.
""".strip(),
    "focus": """
[현재 모드: 집중할 활동 고르기]
- 제공된 활동을 비교해 집중할 대상을 고르는 데 도움을 준다.
- 확인된 정보로만 비교하고 정보가 부족하면 확인할 내용을 안내한다.
""".strip(),
}
DEFAULT_MODE = "general"
MODE_ALIASES = {'explorer': 'discover', 'study': 'general', 'balanced': 'general'}

SOURCE_LABELS = {"cbnu": "CBNU 포털", "wevity": "Wevity", "contestkorea": "ContestKorea"}


class PlannerError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def resolve_mode(mode) -> str:
    if not isinstance(mode, str):
        return DEFAULT_MODE
    mode = MODE_ALIASES.get(mode, mode)
    return mode if mode in MODE_PROMPTS else DEFAULT_MODE


def parse_chat_request(body) -> tuple[str, str, list[dict]]:
    if not isinstance(body, dict):
        raise PlannerError("요청 형식이 올바르지 않습니다.")

    message = body.get("message").strip() if isinstance(body.get("message"), str) else ""
    if not message:
        raise PlannerError("메시지를 입력해 주세요.")
    if len(message) > LIMITS["message_chars"]:
        raise PlannerError(f"메시지는 {LIMITS['message_chars']}자 이하로 입력해 주세요.")

    raw_history = body.get("history") if isinstance(body.get("history"), list) else []
    history = [
        {"role": item["role"], "content": item["content"].strip()[: LIMITS["history_content_chars"]]}
        for item in raw_history
        if isinstance(item, dict) and item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str)
    ]
    history = [item for item in history if item["content"]]
    if history and history[-1]["role"] == "user" and history[-1]["content"] == message:
        history.pop()

    return message, resolve_mode(body.get("mode")), history[-LIMITS["history_items"] :]


def _dday(end: str, today: date) -> str:
    days = (date.fromisoformat(end) - today).days
    return "D-DAY" if days == 0 else f"D-{days}"


def build_events_context(events: list[EventOut], today: date) -> str:
    upcoming = sorted(
        (event for event in events if event.end_date and event.end_date >= today.isoformat()),
        key=lambda event: event.end_date,
    )[:CONTEXT_EVENT_LIMIT]
    if not upcoming:
        return f"[사용자의 NotiAI 일정 목록 (오늘: {today.isoformat()})]\n- 마감 전인 일정이 없다."

    lines = []
    for event in upcoming:
        period = f"{event.start_date} ~ {event.end_date}" if event.start_date else f"~ {event.end_date}"
        parts = [
            _dday(event.end_date, today),
            event.title,
            period,
            event.location or "장소 없음",
            SOURCE_LABELS.get(event.source, event.source),
            "캘린더 등록됨" if event.registered else "캘린더 미등록",
        ]
        if event.review_status == "needs_review":
            parts.append("날짜 검토 필요")
        if event.detail:
            parts.append(event.detail)
        lines.append("- " + " | ".join(parts))

    return (
        f"[사용자의 NotiAI 일정 목록 (오늘: {today.isoformat()}, 마감 임박순 최대 {CONTEXT_EVENT_LIMIT}건)]\n"
        + "\n".join(lines)
        + "\n\n일정 관련 질문은 위 목록만 근거로 답하고, 목록에 없는 일정은 모른다고 말한다."
    )


def build_instructions(mode: str, events_context: str) -> str:
    return f"{BASE_PROMPT}\n\n{MODE_PROMPTS[resolve_mode(mode)]}\n\n{events_context}"


def create_reply(instructions: str, input_messages: list[dict]) -> str:
    settings = get_settings()
    if not settings.openai_api_key:
        log.error("[planner/chat] OPENAI_API_KEY is not configured")
        raise PlannerError("AI 플래너 서버 설정이 완료되지 않았습니다.", 500)

    import openai

    client = openai.OpenAI(api_key=settings.openai_api_key, max_retries=1, timeout=25)
    try:
        response = client.responses.create(
            model=settings.openai_model,
            instructions=instructions,
            input=input_messages,
            store=False,
            max_output_tokens=settings.openai_max_output_tokens,
        )
        openai_usage.record("chat", settings.openai_model, response)
    except openai.APIStatusError as error:
        log.error("[planner/chat] OpenAI request failed status=%s", error.status_code)
        if error.status_code == 429:
            raise PlannerError("요청이 많아 잠시 후 다시 시도해 주세요.", 503) from None
        raise PlannerError("AI 응답 생성에 실패했습니다.", 502) from None
    except openai.OpenAIError as error:
        log.error("[planner/chat] OpenAI request failed: %s", type(error).__name__)
        raise PlannerError("AI 응답 생성에 실패했습니다.", 502) from None

    text = (response.output_text or "").strip()
    if not text:
        raise PlannerError("AI 응답 생성에 실패했습니다.", 502)
    return text
