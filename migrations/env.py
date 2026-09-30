"""
Alembic 실행 환경.
- 명령줄(alembic ...): .env 의 DATABASE_URL 로 접속
- 서버 시작(app/db.py init_db): 앱이 넘겨준 connection 사용
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401 — 테이블 등록
from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name and "connection" not in config.attributes:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """autogenerate 시 앱 전용 컬럼 타입을 DB 실제 타입으로 기록 (마이그레이션이 앱 코드에 의존하지 않게)"""
    from app.crypto import EncryptedText

    if type_ == "type" and isinstance(obj, EncryptedText):
        return "sa.Text()"
    return False


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        render_item=render_item,
        # SQLite 는 ALTER TABLE 이 제한적 → 테이블을 새로 만들어 옮기는 batch 모드
        render_as_batch=get_settings().database_url.startswith("sqlite"),
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=get_settings().database_url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
