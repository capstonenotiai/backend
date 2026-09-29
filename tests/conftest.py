import os
import tempfile

import pytest

# app 을 import 하기 전에 테스트용 설정 (엔진이 import 시점에 만들어짐)
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"
os.environ["DEV_LOGIN"] = "true"
os.environ["EXTRACTOR"] = "stub"
os.environ["OPENAI_API_KEY"] = ""
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["CRAWL_ENABLED"] = "false"

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
