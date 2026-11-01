"""Test fixtures.

Integration tests run against a REAL PostgreSQL database (``pdm_test``), not
mocks or SQLite: partitioning, COPY, SKIP LOCKED and JSONB behaviour are exactly
what we need to verify. The schema is rebuilt from the Alembic migration at the
start of every test session, so the migration itself is tested too.
"""

import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="pdm-test-"))
os.environ.setdefault("PDM_DATABASE_URL", "postgresql+psycopg://pdm:pdm@localhost:5432/pdm_test")
os.environ["PDM_JWT_SECRET"] = "test-secret-not-for-production-0123456789abcdef"
os.environ["PDM_ENV"] = "test"
os.environ["PDM_COOKIE_SECURE"] = "false"
os.environ["PDM_LOG_JSON"] = "false"
os.environ["PDM_LOG_LEVEL"] = "WARNING"
os.environ["PDM_ARTIFACT_DIR"] = str(_tmp / "artifacts")
os.environ["PDM_UPLOAD_DIR"] = str(_tmp / "uploads")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import User  # noqa: E402
from app.db.session import get_engine, new_session  # noqa: E402
from app.main import app  # noqa: E402
from app.ml import registry  # noqa: E402
from app.modules.auth.router import login_limiter  # noqa: E402
from app.modules.equipment.service import seed_catalog  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
PASSWORD = "Correct-Horse-Battery-9"
TRUNCATE = (
    "TRUNCATE audit_log, jobs, ingestion_batches, alerts, predictions, model_versions, maintenance_events, "
    "failure_events, sensor_readings, equipment, sensor_definitions, equipment_types, refresh_tokens, users "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO public"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")


def reset_database() -> None:
    with get_engine().begin() as conn:
        conn.execute(text(TRUNCATE))
        conn.execute(
            text(
                "UPDATE risk_thresholds SET min_probability = CASE risk_level "
                "WHEN 'low' THEN 0 WHEN 'medium' THEN 0.1 WHEN 'high' THEN 0.3 ELSE 0.6 END"
            )
        )
    with new_session() as db:
        seed_catalog(db)
        db.commit()
    registry._cache.clear()
    login_limiter.reset()


@pytest.fixture
def clean_db() -> None:
    reset_database()


@pytest.fixture
def db(clean_db: None) -> Iterator[Session]:
    session = new_session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def client(clean_db: None) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def create_user(email: str, role: str, password: str = PASSWORD, active: bool = True) -> User:
    with new_session() as s:
        user = User(
            email=email, full_name=role.title(), password_hash=hash_password(password), role=role, is_active=active
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client: TestClient) -> Callable[[str], dict[str, str]]:
    """``auth("engineer")`` → headers for a fresh user with that role."""
    made: dict[str, dict[str, str]] = {}

    def _auth(role: str) -> dict[str, str]:
        if role not in made:
            email = f"{role}@example.com"
            create_user(email, role)
            made[role] = login(client, email)
        return made[role]

    return _auth


def make_equipment(
    client: TestClient, headers: dict[str, str], serial: str = "PUM-1", type_code: str = "pump", **extra: object
) -> dict:
    body = {"name": f"Unit {serial}", "serial_number": serial, "equipment_type_code": type_code, **extra}
    r = client.post("/api/v1/equipment", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()
