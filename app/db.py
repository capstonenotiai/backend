from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

_url = get_settings().database_url
engine = create_engine(
    _url,
    connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {},
    # MySQL 은 오래 쉰 연결을 끊음(wait_timeout) → 사용 전 확인 + 1시간마다 새 연결
    pool_pre_ping=True,
    pool_recycle=3600,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


BACKEND_ROOT = Path(__file__).resolve().parents[1]
# Alembic 도입 전(create_all) 구조와 같은 버전 — migrations/versions/0001_initial_schema.py
BASELINE_REVISION = "0001"


def init_db() -> None:
    """
    DB 를 최신 구조로 맞춘다 (서버 시작 / CLI 에서 호출) = `alembic upgrade head`.
    Alembic 도입 전에 create_all 로 만든 DB(테이블은 있는데 alembic_version 이 없음)는
    BASELINE_REVISION 으로 표시(stamp)한 뒤 이어서 적용한다 — 기존 데이터는 그대로.
    """
    with engine.begin() as connection:
        run_migrations(connection)


def run_migrations(connection, revision: str = "head") -> None:
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.attributes["connection"] = connection
    tables = set(inspect(connection).get_table_names())
    if "alembic_version" not in tables and "users" in tables:
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, revision)
