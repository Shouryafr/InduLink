"""Application configuration.

Reads from the environment. A missing required secret fails at startup with a
clear message rather than silently falling back to an insecure default
(brief section 11, Secrets).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "var" / "indulink.db"


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or unsafe."""


def _env_bool(env: dict, name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(env: dict, name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    secret_key: str = ""
    database_url: str = ""
    demo_mode: bool = True
    cookie_secure: bool = False
    bcrypt_rounds: int = 12
    session_idle_timeout: timedelta = timedelta(days=30)
    session_absolute_timeout: timedelta = timedelta(days=30)
    signin_max_attempts: int = 5
    signin_lockout: timedelta = timedelta(minutes=15)
    availability_freshness_days: int = 30
    evidence_review_sla_days: int = 14
    # Phase 2+ thresholds live here so they are configurable, not hard-coded.
    readiness_effort_limit_hours: int = 40
    mentor_high_utilization_pct: int = 85
    demand_min_projects: int = 10
    extra: dict = field(default_factory=dict)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @classmethod
    def load(cls, env: dict | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        app_env = env.get("APP_ENV", "development")
        secret_key = env.get("SECRET_KEY", "").strip()
        database_url = env.get("DATABASE_URL", "").strip()

        if not database_url:
            database_url = f"sqlite+pysqlite:///{DEFAULT_DB_PATH.as_posix()}"

        if not secret_key:
            if app_env.lower() in {"production", "prod"}:
                raise ConfigurationError(
                    "SECRET_KEY is required when APP_ENV=production. "
                    "Set it in the environment (see .env.example); it is used to "
                    "sign CSRF tokens."
                )
            # Development only: an ephemeral key. Sessions and tokens issued
            # with it do not survive a restart, which is stated in the README.
            import secrets

            secret_key = secrets.token_urlsafe(48)

        if len(secret_key) < 32 and app_env.lower() in {"production", "prod"}:
            raise ConfigurationError("SECRET_KEY must be at least 32 characters long.")

        demo_mode = _env_bool(env, "DEMO_MODE", True)
        if demo_mode and app_env.lower() in {"production", "prod"}:
            # The Production Launch Brief forbids demo logins and synthetic data
            # in production. Refusing to start is the only safe behaviour.
            raise ConfigurationError(
                "DEMO_MODE must be false when APP_ENV=production. Demo logins and synthetic "
                "data are not permitted in production."
            )

        bcrypt_rounds = _env_int(env, "BCRYPT_ROUNDS", 12)
        if bcrypt_rounds < 12 and app_env.lower() in {"production", "prod"}:
            raise ConfigurationError(
                "BCRYPT_ROUNDS must be at least 12 in production (brief section 11)."
            )
        if bcrypt_rounds < 4:
            raise ConfigurationError("BCRYPT_ROUNDS must be at least 4, even in tests.")

        return cls(
            app_env=app_env,
            secret_key=secret_key,
            database_url=database_url,
            demo_mode=demo_mode,
            cookie_secure=_env_bool(
                env, "COOKIE_SECURE", app_env.lower() in {"production", "prod"}
            ),
            bcrypt_rounds=bcrypt_rounds,
            session_idle_timeout=timedelta(hours=_env_int(env, "SESSION_IDLE_HOURS", 720)),
            session_absolute_timeout=timedelta(
                hours=_env_int(env, "SESSION_ABSOLUTE_HOURS", 720)
            ),
            signin_max_attempts=_env_int(env, "SIGNIN_MAX_ATTEMPTS", 5),
            signin_lockout=timedelta(minutes=_env_int(env, "SIGNIN_LOCKOUT_MINUTES", 15)),
            availability_freshness_days=_env_int(env, "AVAILABILITY_FRESHNESS_DAYS", 30),
            evidence_review_sla_days=_env_int(env, "EVIDENCE_REVIEW_SLA_DAYS", 14),
            readiness_effort_limit_hours=_env_int(env, "READINESS_EFFORT_LIMIT_HOURS", 40),
            mentor_high_utilization_pct=_env_int(env, "MENTOR_HIGH_UTILIZATION_PCT", 85),
            demand_min_projects=_env_int(env, "DEMAND_MIN_PROJECTS", 10),
        )
