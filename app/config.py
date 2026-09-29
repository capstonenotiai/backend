"""환경변수 설정 (.env.example 참고)"""
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./notiai.db"
    session_secret: str = "change-me"
    cors_origins: str = "http://localhost:5173,https://notiai.pages.dev"
    frontend_url: str = "http://localhost:5173"
    dev_login: bool = True
    cookie_secure: bool = False

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"

    openai_api_key: str = ""
    openai_model: str = "gpt-6-luna"
    openai_max_output_tokens: int = 800

    extractor: str = "stub"
    extract_model: str = "gpt-4o-mini"
    model_api_url: str = ""
    model_repo_path: str = ""

    crawl_enabled: bool = False
    crawl_cron: str = "0 9 * * *"
    crawl_max_pages: int = 2

    # collected_at 이 이 일수 이내면 is_new
    new_event_days: int = 3
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
