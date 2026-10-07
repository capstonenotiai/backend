"""
환경변수 설정 — backend/.env 에 필드 이름을 대문자로 적는다. (예: database_url → DATABASE_URL)
.env 에 없는 값은 아래 기본값을 쓴다. .env 는 git 에 올리지 않고 팀 내부에서 직접 전달.
"""
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DB. 예: mysql+pymysql://notiai:notiai@localhost:3307/notiai?charset=utf8mb4 (없으면 SQLite 파일)
    database_url: str = "sqlite:///./notiai.db"
    # 세션 쿠키 서명 키 — 배포 시 반드시 긴 랜덤 문자열
    session_secret: str = "change-me"
    # Google refresh token 암호화 키 (Fernet). 비우면 SESSION_SECRET 에서 생성 — app/crypto.py
    token_encryption_key: str = ""
    # 프론트 주소 (CORS 허용, 쉼표 구분) / 로그인 후 돌아갈 주소
    cors_origins: str = "http://localhost:5173,https://notiai.pages.dev"
    frontend_url: str = "http://localhost:5173"
    # true: 로그인 안 한 요청도 '개발용 사용자'로 처리 (배포 시 false)
    dev_login: bool = True
    # Actual signed-in Google accounts only. DEV_LOGIN never grants admin access.
    admin_emails: str = ""
    # 프론트/백엔드 도메인이 다른 https 배포에서 true (SameSite=None; Secure)
    cookie_secure: bool = False

    # Google OAuth (Google Cloud Console → OAuth 클라이언트)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"

    # OpenAI — AI 플래너 + EXTRACTOR=gpt
    openai_api_key: str = ""
    openai_model: str = "gpt-6-luna"
    openai_max_output_tokens: int = 800

    # 일정 추출기: stub | gpt | model_api (app/services/extractor.py)
    extractor: str = "stub"
    extract_model: str = "gpt-4o-mini"
    model_api_url: str = ""
    # Shared X-Model-Token; keep only in the local environment/.env, never in logs.
    model_api_token: str = ""
    extraction_max_attempts: int = 3
    # capstonenotiai/model repo 로컬 경로 — EXTRACTOR=gpt 일 때 SYSTEM_PROMPT/postprocess 재사용 (크롤러와는 무관)
    model_repo_path: str = ""

    # 정기 수집 (crawler/runner.py). crawl_max_pages: 사이트당 목록 페이지 수 (Wevity 는 x20 건)
    crawl_enabled: bool = False
    crawl_cron: str = "0 9 * * *"
    crawl_max_pages: int = 2

    # collected_at 이 이 일수 이내면 is_new
    new_event_days: int = 3
    # /api/events 에서 제외하는 기준 (DB 에서 지우지는 않음) — app/services/events.py list_events
    #   마감 후 N일 지남 / 날짜 없는 일정은 수집 후 N일 지남. 캘린더 등록·북마크한 일정은 계속 표시
    event_retention_days: int = 90
    timezone: str = "Asia/Seoul"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
