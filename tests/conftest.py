"""Shared test fixtures.

The schema is built once by running the real Alembic migrations, then copied
per test. That way tests exercise the same DDL production runs, including the
append-only triggers, without paying for a migration on every test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.middleware import CSRF_COOKIE
from config import Settings
from data.database import Database
from data.models import Base

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_SECRET = "test-secret-key-with-enough-length-0123456789"


def _migrate(db_path: Path) -> None:
    env = dict(os.environ)
    env["SECRET_KEY"] = TEST_SECRET
    env["APP_ENV"] = "test"
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:  # pragma: no cover - fail loudly
        raise RuntimeError(f"alembic upgrade failed:\n{completed.stdout}\n{completed.stderr}")


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("schema") / "template.db"
    _migrate(path)
    return path


@pytest.fixture(scope="session")
def seeded_template(migrated_template, tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("seeded") / "seeded.db"
    shutil.copy(migrated_template, path)
    settings = Settings(
        app_env="test",
        secret_key=TEST_SECRET,
        database_url=f"sqlite+pysqlite:///{path.as_posix()}",
        bcrypt_rounds=4,
    )
    database = Database(settings)
    from data.seed_synthetic import seed

    with database.session() as session:
        seed(session, settings=settings)
    database.engine.dispose()
    return path


def _copy(source: Path, target_dir: Path) -> Path:
    target = target_dir / "test.db"
    shutil.copy(source, target)
    return target


@pytest.fixture
def db_path(migrated_template, tmp_path) -> Path:
    return _copy(migrated_template, tmp_path)


@pytest.fixture
def seeded_db_path(seeded_template, tmp_path) -> Path:
    return _copy(seeded_template, tmp_path)


def make_settings(path: Path, **overrides) -> Settings:
    values = {
        "app_env": "test",
        "secret_key": TEST_SECRET,
        "database_url": f"sqlite+pysqlite:///{path.as_posix()}",
        "bcrypt_rounds": 4,
        "demo_mode": True,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(db_path) -> Settings:
    return make_settings(db_path)


@pytest.fixture
def database(settings) -> Database:
    db = Database(settings)
    yield db
    db.engine.dispose()


@pytest.fixture
def app(settings):
    from app.main import create_app

    application = create_app(settings)
    yield application
    application.state.db.engine.dispose()


@pytest.fixture
def seeded_app(seeded_db_path):
    from app.main import create_app

    application = create_app(make_settings(seeded_db_path))
    yield application
    application.state.db.engine.dispose()


class Browser:
    """A TestClient wrapper that behaves like a browser with CSRF enabled.

    The CSRF cookie is ``nonce.signature``; the form field carries the nonce.
    Reading it from the cookie keeps tests honest: they must send a real token,
    exactly as the pages do.
    """

    def __init__(self, client: TestClient):
        self.client = client

    @property
    def csrf(self) -> str:
        raw = self.client.cookies.get(CSRF_COOKIE, "")
        return raw.split(".")[0] if raw else ""

    def get(self, path: str, **kwargs):
        return self.client.get(path, follow_redirects=False, **kwargs)

    def post(self, path: str, data: dict | None = None, *, with_token: bool = True, **kwargs):
        payload = dict(data or {})
        if with_token:
            payload.setdefault("csrf_token", self.csrf)
        return self.client.post(path, data=payload, follow_redirects=False, **kwargs)

    def sign_in(self, email: str, password: str = "DemoPass!2026", *, next_url: str = ""):
        self.get("/sign-in")  # establishes the CSRF cookie
        return self.post(
            "/sign-in", {"email": email, "password": password, "next": next_url}
        )


@pytest.fixture
def browser(app) -> Browser:
    with TestClient(app) as client:
        yield Browser(client)


@pytest.fixture
def seeded_browser(seeded_app) -> Browser:
    with TestClient(seeded_app) as client:
        yield Browser(client)


@pytest.fixture
def session(database):
    session = database.session_factory()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


def assert_tables_present(path: Path) -> None:
    from sqlalchemy import inspect

    engine = Database(
        Settings(
            app_env="test",
            secret_key=TEST_SECRET,
            database_url=f"sqlite+pysqlite:///{path.as_posix()}",
            bcrypt_rounds=4,
        )
    ).engine
    names = set(inspect(engine).get_table_names())
    missing = set(Base.metadata.tables) - names
    engine.dispose()
    assert not missing, f"missing tables: {sorted(missing)}"
