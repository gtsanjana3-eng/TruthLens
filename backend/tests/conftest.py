import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="truthlens-test-"))
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    "STORAGE_DIR": str(_TMP / "evidence"),
    "PREVIEW_DIR": str(_TMP / "previews"),
    "REPORT_DIR": str(_TMP / "reports"),
    "PROCESS_ISOLATION": "false",
    "ML_ENABLED": "false",  # unit/integration tests never download or run real models (validated separately)
    "JWT_SECRET": "test-secret-0123456789-abcdefghijklmnop",
    "RATE_LIMIT_AUTH_PER_MINUTE": "1000",
    "RATE_LIMIT_UPLOAD_PER_MINUTE": "1000",
    "RATE_LIMIT_DEFAULT_PER_MINUTE": "100000",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ratelimit  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import jobs  # noqa: E402

jobs.SYNC_MODE = True
FIX = _TMP / "fixtures"
FIX.mkdir(exist_ok=True)


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ratelimit.reset()
    yield


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIX


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


PASSWORD = "Str0ngPassword!"


def register(client, email="alice@example.com", name="Alice Analyst"):
    r = client.post("/api/auth/register", json={"email": email, "full_name": name, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["user"]


@pytest.fixture
def auth(client):
    headers, _ = register(client)
    return headers


@pytest.fixture
def case_id(client, auth):
    r = client.post("/api/cases", json={"title": "Viral clip verification", "context_statement": "Filmed at night on 2024-03-15 with an iPhone."}, headers=auth)
    assert r.status_code == 201
    return r.json()["id"]


def upload(client, auth, case_id, path: Path, mime: str, name: str | None = None):
    with open(path, "rb") as f:
        return client.post(f"/api/cases/{case_id}/evidence", files={"file": (name or path.name, f, mime)}, headers=auth)


def pytest_sessionfinish(session, exitstatus):
    try:
        engine.dispose()
        shutil.rmtree(_TMP, ignore_errors=True)
    except Exception:
        pass
