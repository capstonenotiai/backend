"""
POST /api/planner/chat — 프론트 services/plannerService.js 계약 그대로.

Request : { message, mode, history: [{ role, content }] }
Response: 200 { reply }  /  4xx·5xx { message }
"""
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

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
        return await run_in_threadpool(planner_recommendations.recommend, user.id, payload, server_items, now)
    except planner.PlannerError as error:
        return JSONResponse({'message': error.message}, status_code=error.status)


@router.post("/chat")
async def chat(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    raw = await request.body()
    if len(raw) > planner.LIMITS["body_bytes"]:
        return JSONResponse({"message": "요청이 너무 큽니다."}, status_code=413)
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"message": "요청 형식이 올바르지 않습니다."}, status_code=400)

    try:
        message, mode, history = planner.parse_chat_request(body)
        pref = preferences.get_or_create_preference(db, user)
        events = list_events(db, user, pref.enabled_sources)
        instructions = planner.build_instructions(mode, planner.build_events_context(events, today_local()))
        # OpenAI 호출은 동기 SDK → 이벤트 루프를 막지 않도록 스레드에서 실행
        reply = await run_in_threadpool(
            planner.create_reply, instructions, [*history, {"role": "user", "content": message}]
        )
    except planner.PlannerError as error:
        return JSONResponse({"message": error.message}, status_code=error.status)

    return {"reply": reply}
