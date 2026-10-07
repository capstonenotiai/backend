import os
import tempfile

import pytest

# app 을 import 하기 전에 테스트용 설정 (엔진이 import 시점에 만들어짐)
# 기본은 임시 SQLite. MySQL 로 돌리려면 TEST_DATABASE_URL 지정 (⚠️ 테이블을 지우고 다시 만듦 — 테스트 전용 DB 사용)
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{os.path.join(_tmp, 'test.db')}"
os.environ["DEV_LOGIN"] = "true"
os.environ["EXTRACTOR"] = "stub"
os.environ["OPENAI_API_KEY"] = ""
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["CRAWL_ENABLED"] = "false"
os.environ["EVENT_RETENTION_DAYS"] = "90"
os.environ["MODEL_API_TOKEN"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def client(db):
    seed(db)
    with TestClient(app) as test_client:
        yield test_client
