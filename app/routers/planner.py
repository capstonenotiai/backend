"""
POST /api/planner/chat — 프론트 services/plannerService.js 계약 그대로.

Request : { message, mode, history: [{ role, content }] }
Response: 200 { reply }  /  4xx·5xx { message }
"""
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.services import planner, planner_context, planner_recommendations, preferences
from app.services.events import list_events
from app.timeutil import now_local, today_local

router = APIRouter(prefix="/api/planner", tags=["planner"])


@router.post('/recommendations')
async def recommendations(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    raw = await request.body()
    if len(raw) > planner.LIMITS['body_bytes']:
        return JSONResponse({'message': '요청이 너무 큽니다.'}, status_code=413)
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({'message': '요청 형식이 올바르지 않습니다.'}, status_code=400)
    try:
        mode = planner_recommendations.parse_request(body)
        now = now_local()
        payload, server_items = planner_context.build_recommendation_context(db, user, mode, now)
        return await run_in_threadpool(planner_recommendations.recommend, user.id, payload, server_items, now,
                                      refresh=body.get('refresh') is True)
    except planner.PlannerError as error:
        return JSONResponse({'message': error.message}, status_code=error.status)


async def _chat_input(request: Request, user: User, db: Session):
    """요청 본문 검증과 일정 컨텍스트 구성. 오류면 JSONResponse 를 돌려준다."""
    raw = await request.body()
    if len(raw) > planner.LIMITS["body_bytes"]:
        return JSONResponse({"message": "요청이 너무 큽니다."}, status_code=413)
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"message": "요청 형식이 올바르지 않습니다."}, status_code=400)
    try:
        message, mode, history = planner.parse_chat_request(body)
    except planner.PlannerError as error:
        return JSONResponse({"message": error.message}, status_code=error.status)
    pref = preferences.get_or_create_preference(db, user)
    events = list_events(db, user, pref.enabled_sources)
    instructions = planner.build_instructions(mode, planner.build_events_context(events, today_local(), pref.interests))
    return instructions, [*history, {"role": "user", "content": message}]


@router.post("/chat")
async def chat(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prepared = await _chat_input(request, user, db)
    if isinstance(prepared, JSONResponse):
        return prepared
    try:
        # OpenAI 호출은 동기 SDK → 이벤트 루프를 막지 않도록 스레드에서 실행
        reply = await run_in_threadpool(planner.create_reply, *prepared)
    except planner.PlannerError as error:
        return JSONResponse({"message": error.message}, status_code=error.status)

    return {"reply": reply}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """POST /chat 과 같은 입력. 응답은 text/event-stream:
    event: delta {text} … event: done {reply}  /  event: error {message, status}
    입력 오류는 스트림을 열기 전에 JSON 4xx 로 돌려준다."""
    prepared = await _chat_input(request, user, db)
    if isinstance(prepared, JSONResponse):
        return prepared

    def events():
        try:
            for kind, value in planner.stream_reply(*prepared):
                yield _sse("delta", {"text": value}) if kind == "delta" else _sse("done", {"reply": value})
        except planner.PlannerError as error:
            yield _sse("error", {"message": error.message, "status": error.status})

    # 동기 OpenAI 스트림은 스레드에서 돌리고, 조각은 버퍼링 없이 바로 보낸다.
    return StreamingResponse(iterate_in_threadpool(events()), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
