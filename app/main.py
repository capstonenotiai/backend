"""
NotiAI 백엔드 (FastAPI)

실행: uvicorn app.main:app --reload
문서: http://localhost:8000/docs
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import init_db
from app.routers import auth, calendar, dashboard, events, health, planner, users
from app.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    scheduler = start_scheduler()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


settings = get_settings()
app = FastAPI(title="NotiAI Backend", version="0.1.0", lifespan=lifespan)

# 프론트는 credentials: 'include' 로 호출 → origin 을 정확히 지정해야 함 ('*' 불가)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 서명된 세션 쿠키. 프론트/백엔드 도메인이 다르면 COOKIE_SECURE=true (SameSite=None; Secure)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="notiai_session",
    max_age=14 * 24 * 60 * 60,
    same_site="none" if settings.cookie_secure else "lax",
    https_only=settings.cookie_secure,
)


# 프론트 apiClient.js 는 에러 응답의 { message } 를 화면에 보여준다 → 모든 에러를 이 형태로
@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "요청을 처리하지 못했습니다."
    return JSONResponse({"message": message}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse(
        {"message": "요청 형식이 올바르지 않습니다.", "errors": jsonable_encoder(exc.errors())}, status_code=400
    )


for module in (health, auth, users, events, dashboard, calendar, planner):
    app.include_router(module.router)
