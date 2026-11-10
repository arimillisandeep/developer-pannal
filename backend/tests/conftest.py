"""Test bootstrap: isolated PostgreSQL schema and temporary storage."""
import os
import re
import tempfile
import uuid
from datetime import date
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_env_values = dotenv_values(_BACKEND_DIR / ".env")
_source_database_url = os.environ.get("DATABASE_URL") or _env_values.get("DATABASE_URL")
if not _source_database_url or not _source_database_url.startswith(("postgresql://", "postgresql+")):
    raise RuntimeError("Tests require the configured PostgreSQL DATABASE_URL; SQLite fallback is disabled.")

# All tests run in a fresh schema inside the configured PostgreSQL database.
# The UUID name prevents collisions; teardown drops only this test-owned schema.
_TEST_SCHEMA = f"workcode_test_{uuid.uuid4().hex}"
if not re.fullmatch(r"[a-z0-9_]+", _TEST_SCHEMA):
    raise RuntimeError("Generated an invalid isolated PostgreSQL test schema name.")
_setup_engine = create_engine(_source_database_url, pool_pre_ping=True)
try:
    with _setup_engine.begin() as _connection:
        _connection.execute(text(f'CREATE SCHEMA "{_TEST_SCHEMA}"'))
finally:
    _setup_engine.dispose()

_test_url = make_url(_source_database_url).update_query_dict(
    {"options": f"-c search_path={_TEST_SCHEMA}"}
)
os.environ["DATABASE_URL"] = _test_url.render_as_string(hide_password=False)
os.environ["STORAGE_ROOT"] = str(Path(tempfile.mkdtemp(prefix="workcode-test-")) / "storage")
os.environ["ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["LOGIN_RATE_LIMIT"] = "10000"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAccount, EmployeeProfile, User  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    try:
        from app.core.db import engine as test_engine
        with test_engine.connect() as connection:
            current_schema = connection.execute(text("SELECT current_schema()")).scalar_one()
        if current_schema != _TEST_SCHEMA:
            raise RuntimeError("PostgreSQL test connection did not select its isolated schema; refusing to run tests.")
        init_db()
        db = SessionLocal()
        try:
            for slot in (1, 2, 3):
                u = User(email=f"admin{slot}@wc.test", username=f"admin{slot}", full_name=f"Admin {slot}",
                         role="admin", password_hash=hash_password("AdminPass123"))
                db.add(u)
                db.flush()
                db.add(AdminAccount(user_id=u.id, admin_slot=slot))

            dev = User(email="dev@wc.test", username="dev", full_name="Dev Onroll",
                       role="developer_onroll", password_hash=hash_password("DevPass123"))
            intern = User(email="intern@wc.test", username="intern", full_name="Intern One",
                          role="intern", password_hash=hash_password("InternPass123"))
            db.add_all([dev, intern])
            db.flush()
            db.add(EmployeeProfile(user_id=dev.id, employee_code="WC-E0001", joining_date=date(2025,1,10)))
            db.add(EmployeeProfile(user_id=intern.id, employee_code="WC-E0002", joining_date=date(2025,6,1),
                                   expected_end_date=date(2025,12,1), employment_status="probation"))
            db.commit()
        finally:
            db.close()
        yield
    finally:
        from app.core.db import engine as test_engine
        test_engine.dispose()
        cleanup_engine = create_engine(_source_database_url, pool_pre_ping=True)
        try:
            with cleanup_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{_TEST_SCHEMA}" CASCADE'))
        finally:
            cleanup_engine.dispose()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def login(client, identifier, password):
    r = client.post("/api/auth/login", json={"identifier": identifier, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def admin_token(client):
    return login(client, "admin1@wc.test", "AdminPass123")["access_token"]


@pytest.fixture
def dev_token(client):
    return login(client, "dev@wc.test", "DevPass123")["access_token"]


@pytest.fixture
def intern_token(client):
    return login(client, "intern@wc.test", "InternPass123")["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}
