"""Database engine, session factory and integrity pragmas.

SQLite runs in WAL mode with foreign keys enforced on *every* connection. That
is verified by a test rather than assumed, because a silent failure here would
let orphaned rows accumulate.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from config import Settings


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def build_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create an engine with the required pragmas installed per connection."""
    connect_args: dict = {}
    if _is_sqlite(database_url):
        connect_args["check_same_thread"] = False
        # A busy timeout stops a concurrent write from raising immediately.
        connect_args["timeout"] = 15

    engine = create_engine(
        database_url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if _is_sqlite(database_url):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=15000")
            # WAL is not available for in-memory databases.
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
            except Exception:  # noqa: BLE001 - in-memory databases refuse WAL
                pass
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Database:
    """Small holder so the app can hand one engine to routers and services."""

    def __init__(self, settings: Settings, *, echo: bool = False) -> None:
        self.settings = settings
        self.engine = build_engine(settings.database_url, echo=echo)
        self.session_factory = build_session_factory(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def read_only_session(self) -> Iterator[Session]:
        """Session that cannot write. Used by the What-If sandbox and analytics."""
        session = self.session_factory()
        try:
            if _is_sqlite(self.settings.database_url):
                session.execute(text("PRAGMA query_only=ON"))
            yield session
        finally:
            session.rollback()
            session.close()

    def pragma_report(self) -> dict:
        """Values of the integrity pragmas, for the startup log and the tests."""
        with self.engine.connect() as conn:
            foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()
            journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        return {"foreign_keys": bool(foreign_keys), "journal_mode": str(journal).lower()}

    def integrity_check(self) -> str:
        with self.engine.connect() as conn:
            return str(conn.execute(text("PRAGMA integrity_check")).scalar())
