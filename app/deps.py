"""Request dependencies: database session, identity and role guards.

Every protected route depends on one of ``require_user`` or ``require_roles``.
Role checks alone are not enough for object-level access, so services also
check ownership and membership; a route guard is the outer gate, not the whole
gate (brief section 11).
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request
from sqlalchemy.orm import Session

from data.models import User
from services.errors import AuthRequired, PermissionDenied


def get_db(request: Request) -> Session:
    """The single request-scoped session created by ``RequestContextMiddleware``."""
    session: Session | None = getattr(request.state, "db", None)
    if session is None:  # pragma: no cover - middleware always runs in the app
        raise RuntimeError("No request session available.")
    return session


def get_current_user(request: Request) -> User | None:
    return getattr(request.state, "user", None)


def get_ip(request: Request) -> str:
    return getattr(request.state, "ip", "") or ""


def require_user(request: Request) -> User:
    user = get_current_user(request)
    if user is None:
        raise AuthRequired(
            "Sign in to continue.",
            reason="no session",
            target_type="route",
            target_id=request.url.path,
        )
    return user


def require_roles(*roles: str) -> Callable[[Request], User]:
    """Dependency factory that admits only the listed roles."""

    allowed = frozenset(roles)

    def dependency(request: Request) -> User:
        user = require_user(request)
        if user.role not in allowed:
            raise PermissionDenied(
                "This area is for a different account type.",
                target_type="route",
                target_id=request.url.path,
                reason=f"role {user.role} not permitted",
            )
        return user

    return dependency


def require_same_user_or_admin(request: Request, target_user_id: int) -> User:
    user = require_user(request)
    if user.id != target_user_id and user.role != "admin":
        raise PermissionDenied(
            "You can only open your own records.",
            target_type="user",
            target_id=target_user_id,
            reason="not owner",
        )
    return user
