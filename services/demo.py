"""Reset the synthetic demo dataset.

The demo runs as its own environment with its own database file. Reset builds a
fresh database beside the current one, migrates and seeds it, then swaps the
application onto it. Rebuilding rather than deleting rows is deliberate: the
append-only triggers protect evidence, versions and assignment history, and a
reset must not become a back door around them.

The old file is deleted on a best-effort basis. On Windows the just-finished
request may still hold a handle for a moment; a leftover file is cleaned up on
the next reset and is never reused.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

from config import BASE_DIR, Settings
from data.database import Database
from services.errors import Conflict, PermissionDenied


@dataclass
class ResetOutcome:
    counts: dict
    seconds: float
    database_path: str
    removed_old: bool


def _sqlite_path(settings: Settings) -> Path | None:
    url = settings.database_url
    if not url.startswith("sqlite"):
        return None
    raw = url.split("///", 1)[-1]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def is_available(settings: Settings) -> bool:
    return (
        bool(settings.demo_mode)
        and not settings.is_production
        and _sqlite_path(settings) is not None
    )


def _migrate(database_url: str, settings: Settings) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["SECRET_KEY"] = settings.secret_key
    env["APP_ENV"] = settings.app_env
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:  # pragma: no cover - reported to the page
        raise Conflict(
            "The demo database could not be rebuilt: migrations failed. "
            "An administrator can check the server log."
        )


def reset(settings: Settings) -> tuple[ResetOutcome, Database]:
    """Build and seed a new demo database. Returns the outcome and its holder."""
    if settings.is_production:
        raise PermissionDenied("Demo reset is never available in production.")
    if not settings.demo_mode:
        raise PermissionDenied("Demo reset is only available when DEMO_MODE=true.")
    path = _sqlite_path(settings)
    if path is None:
        raise Conflict("Demo reset is implemented for the SQLite demo database only.")
    path.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    new_path = path.with_name(f"{path.stem}-r{stamp}{path.suffix}")
    new_url = f"sqlite+pysqlite:///{new_path.as_posix()}"
    _migrate(new_url, settings)

    new_settings = replace(settings, database_url=new_url)
    new_database = Database(new_settings)
    from data.seed_synthetic import seed

    with new_database.session() as session:
        counts = seed(session, settings=new_settings)
    seconds = round(time.perf_counter() - started, 2)
    return (
        ResetOutcome(
            counts=counts,
            seconds=seconds,
            database_path=str(new_path),
            removed_old=False,
        ),
        new_database,
    )


def swap(app, outcome: ResetOutcome, database: Database) -> None:
    """Point the application at the freshly seeded database."""
    old = app.state.db
    old_path = _sqlite_path(app.state.settings)
    app.state.db = database
    old.engine.dispose()
    if old_path is not None:
        try:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(old_path) + suffix)
                if candidate.exists():
                    candidate.unlink()
            outcome.removed_old = True
        except OSError:  # pragma: no cover - Windows may still hold the file open
            outcome.removed_old = False
    _cleanup_stale(Path(outcome.database_path))


def _cleanup_stale(current: Path) -> None:
    pattern = f"{current.stem.split('-r')[0]}-r*{current.suffix}"
    for candidate in current.parent.glob(pattern):
        if candidate == current:
            continue
        try:
            candidate.unlink()
            for suffix in ("-wal", "-shm"):
                extra = Path(str(candidate) + suffix)
                if extra.exists():
                    extra.unlink()
        except OSError:  # pragma: no cover - harmless leftover
            continue
