"""Password hashing, sessions and sign-in (brief sections 4 and 11).

* bcrypt with cost 12 (validated, not assumed).
* Passwords are SHA-256 pre-hashed then base64-encoded before bcrypt so long
  or multi-byte passwords are never silently truncated at bcrypt's 72-byte
  limit. The bcrypt work factor still governs the cost of guessing.
* Sessions are random 256-bit tokens. Only a SHA-256 hash of the token is
  stored, so a database leak does not hand over live sessions.
* Sign-in failures are rate-limited with a temporary lockout and answered
  with one generic message, so accounts cannot be enumerated.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import Settings
from core import security_policy
from data.models import Session as SessionRow
from data.models import User, utcnow
from services import audit_log

SESSION_COOKIE = "indulink_session"

# A real bcrypt hash used to keep the timing of "unknown e-mail" close to the
# timing of "wrong password". The value is a hash of a random string.
_DUMMY_HASH = bcrypt.hashpw(
    b"indulink-dummy-password-for-timing-only", bcrypt.gensalt(rounds=12)
).decode("utf-8")


def _prehash(password: str) -> bytes:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str, rounds: int = 12) -> str:
    """Hash a password with bcrypt.

    ``rounds`` defaults to 12, the brief's minimum. Tests lower it explicitly
    for speed; ``Settings.load`` refuses anything below 12 in production.
    """
    if rounds < 4:
        raise ValueError("bcrypt rounds below 4 are not usable.")
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=rounds)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        # Still spend the time so a missing account is not detectable by timing.
        bcrypt.checkpw(_prehash(password), _DUMMY_HASH.encode("utf-8"))
        return False
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- Results --------------------------------------------------------------


@dataclass
class AuthResult:
    user: User | None
    message: str = ""
    locked: bool = False

    @property
    def ok(self) -> bool:
        return self.user is not None


# --- Sign-in --------------------------------------------------------------


def authenticate(
    session: Session,
    *,
    email: str,
    password: str,
    settings: Settings,
    ip: str = "",
    now: datetime | None = None,
) -> AuthResult:
    now = now or utcnow()
    normalized = security_policy.normalize_email(email)
    user = session.scalar(select(User).where(User.email == normalized)) if normalized else None

    if user is not None and security_policy.is_locked_out(
        user.failed_signins,
        user.last_failed_signin_at,
        max_attempts=settings.signin_max_attempts,
        lockout=settings.signin_lockout,
        now=now,
    ):
        audit_log.record(
            session,
            actor=user,
            action="auth.signin_locked",
            target_type="user",
            target_id=user.id,
            ip=ip,
        )
        return AuthResult(None, security_policy.GENERIC_LOCKED_ERROR, locked=True)

    password_ok = verify_password(password or "", user.password_hash if user else None)

    if user is None or not password_ok:
        if user is not None:
            user.failed_signins += 1
            user.last_failed_signin_at = now
        audit_log.record(
            session,
            actor=user,
            action="auth.signin_failed",
            target_type="user",
            target_id=user.id if user else "",
            ip=ip,
        )
        return AuthResult(None, security_policy.GENERIC_SIGNIN_ERROR)

    if user.status != "active":
        audit_log.record(
            session,
            actor=user,
            action="auth.signin_blocked_status",
            target_type="user",
            target_id=user.id,
            reason=user.status,
            ip=ip,
        )
        return AuthResult(
            None,
            "This account is not active. Contact an administrator to restore access.",
        )

    user.failed_signins = 0
    user.last_failed_signin_at = None
    user.last_signed_in_at = now
    audit_log.record(
        session,
        actor=user,
        action="auth.signin_success",
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    return AuthResult(user)


# --- Sessions -------------------------------------------------------------


def create_session(
    session: Session,
    *,
    user: User,
    settings: Settings,
    ip: str = "",
    user_agent: str = "",
    now: datetime | None = None,
) -> str:
    """Issue a session and return the raw token for the cookie."""
    now = now or utcnow()
    token = secrets.token_urlsafe(32)
    row = SessionRow(
        id=hash_session_token(token),
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + settings.session_idle_timeout,
        absolute_expires_at=now + settings.session_absolute_timeout,
        ip=(ip or "")[:64],
        user_agent=(user_agent or "")[:300],
    )
    session.add(row)
    return token


def resolve_session(
    session: Session,
    *,
    token: str | None,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[User, SessionRow] | None:
    """Return the signed-in user for a raw token, or ``None``.

    Expired, revoked and suspended sessions are all rejected here, and the
    account status is re-checked on every request.
    """
    if not token:
        return None
    now = now or utcnow()
    row = session.get(SessionRow, hash_session_token(token))
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at <= now or row.absolute_expires_at <= now:
        return None
    user = session.get(User, row.user_id)
    if user is None or user.status != "active":
        return None
    return user, row


def touch_session(session: Session, row: SessionRow, *, settings: Settings, now: datetime | None = None) -> None:
    """Slide the idle expiry without ever passing the absolute expiry."""
    now = now or utcnow()
    row.last_seen_at = now
    row.expires_at = min(now + settings.session_idle_timeout, row.absolute_expires_at)


def revoke_session(session: Session, row: SessionRow, *, reason: str = "logout") -> None:
    row.revoked_at = utcnow()
    row.revoked_reason = reason[:120]


def revoke_all_for_user(session: Session, user_id: int, *, reason: str = "password_change") -> int:
    rows = session.scalars(
        select(SessionRow).where(
            SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None)
        )
    ).all()
    for row in rows:
        revoke_session(session, row, reason=reason)
    return len(rows)


def purge_expired(session: Session, *, now: datetime | None = None) -> int:
    now = now or utcnow()
    rows = session.scalars(
        select(SessionRow).where(SessionRow.absolute_expires_at <= now)
    ).all()
    for row in rows:
        session.delete(row)
    return len(rows)


# --- Registration ---------------------------------------------------------


def register_user(
    session: Session,
    *,
    email: str,
    password: str,
    role: str,
    display_name: str,
    settings: Settings,
    ip: str = "",
    rounds: int | None = None,
) -> User:
    """Create a self-service account for a student, mentor or industry partner.

    Administrators are never created here: only an existing admin can create
    another admin (brief section 4).
    """
    from services.errors import Conflict, ValidationError

    if role not in {"student", "mentor", "industry"}:
        raise ValidationError(
            "Choose student, mentor or industry partner.",
            field_errors={"role": ["Administrator accounts are created by an existing admin."]},
        )

    normalized = security_policy.normalize_email(email)
    errors: dict[str, list[str]] = {}
    if not security_policy.is_valid_email(normalized):
        errors["email"] = ["Enter a valid e-mail address."]
    password_errors = security_policy.password_policy_errors(password, normalized)
    if password_errors:
        errors["password"] = password_errors
    if not (display_name or "").strip():
        errors["display_name"] = ["Enter the name you want shown on your profile."]
    if errors:
        raise ValidationError("Some details need attention.", field_errors=errors)

    existing = session.scalar(select(User).where(User.email == normalized))
    if existing is not None:
        raise Conflict(
            "An account already exists for this e-mail address.",
            field_errors={"email": ["An account already exists for this e-mail address."]},
        )

    user = User(
        email=normalized,
        password_hash=hash_password(
            password, rounds=rounds if rounds is not None else settings.bcrypt_rounds
        ),
        role=role,
        # No e-mail verification is in scope, so an account is usable
        # immediately. Documented in DECISIONS.md.
        status="active",
        display_name=display_name.strip()[:160],
    )
    session.add(user)
    session.flush()
    audit_log.record(
        session,
        actor=user,
        action="auth.register",
        target_type="user",
        target_id=user.id,
        detail={"role": role},
        ip=ip,
    )
    return user


def change_password(
    session: Session,
    *,
    user: User,
    current_password: str,
    new_password: str,
    settings: Settings,
    ip: str = "",
) -> None:
    from services.errors import ValidationError

    if not verify_password(current_password, user.password_hash):
        raise ValidationError(
            "The current password does not match.",
            field_errors={"current_password": ["The current password does not match."]},
        )
    errors = security_policy.password_policy_errors(new_password, user.email)
    if errors:
        raise ValidationError("Choose a stronger password.", field_errors={"new_password": errors})

    user.password_hash = hash_password(new_password, rounds=settings.bcrypt_rounds)
    revoked = revoke_all_for_user(session, user.id, reason="password_change")
    audit_log.record(
        session,
        actor=user,
        action="auth.password_changed",
        target_type="user",
        target_id=user.id,
        detail={"sessions_revoked": revoked},
        ip=ip,
    )


def count_active_sessions(session: Session, user_id: int) -> int:
    now = utcnow()
    return int(
        session.scalar(
            select(func.count(SessionRow.id)).where(
                SessionRow.user_id == user_id,
                SessionRow.revoked_at.is_(None),
                SessionRow.expires_at > now,
            )
        )
        or 0
    )
