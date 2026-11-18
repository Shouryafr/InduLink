"""HTTP middleware: request context, CSRF, and security headers.

CSRF uses a signed double-submit cookie. The cookie is signed with the server
key so a sibling subdomain cannot plant a value of its choosing, and the value
is compared in constant time against the form field (or the ``X-CSRF-Token``
header, which later HTMX requests use). The check runs in middleware rather
than in each route so a new form cannot accidentally ship without it.
"""

from __future__ import annotations

import email
import logging
import secrets
from urllib.parse import parse_qsl

from sqlalchemy.orm import Session

from core import security_policy
from data.models import User
from services import audit_log, authentication
from services.errors import DomainError

logger = logging.getLogger("indulink.security")

CSRF_COOKIE = "indulink_csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
MAX_BUFFERED_BODY = 12 * 1024 * 1024  # slightly above the 10 MB upload cap

CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    # No inline scripts anywhere in the application. Inline *styles* remain
    # permitted because score bars carry a computed width.
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'"
)


def _parse_cookies(scope) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for key, value in scope.get("headers", []):
        if key == b"cookie":
            for part in value.decode("latin-1").split(";"):
                if "=" in part:
                    name, _, raw = part.partition("=")
                    cookies[name.strip()] = raw.strip()
    return cookies


def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return ""


def _extract_submitted_token(body: bytes, content_type: str) -> str:
    """Find the CSRF token in a urlencoded or multipart body."""
    if not body:
        return ""
    lowered = content_type.lower()
    if lowered.startswith("application/x-www-form-urlencoded"):
        try:
            pairs = parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
        except ValueError:
            return ""
        for key, value in pairs:
            if key == CSRF_FIELD:
                return value
        return ""
    if lowered.startswith("multipart/form-data"):
        raw = b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
        message = email.message_from_bytes(raw)
        for part in message.walk():
            if part.get_param("name", header="content-disposition") != CSRF_FIELD:
                continue
            payload = part.get_payload(decode=True)
            return payload.decode("utf-8", "replace") if payload else ""
    return ""


def _cookie_header(name: str, value: str, *, secure: bool, max_age: int | None = None) -> bytes:
    parts = [f"{name}={value}", "Path=/", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    return ("; ".join(parts)).encode("latin-1")


class RequestContextMiddleware:
    """One database session and one resolved identity per request.

    The session is committed when the request finishes normally and rolled
    back when anything raises, so a failed request cannot leave partial state.
    """

    def __init__(self, app, database, settings) -> None:
        self.app = app
        self.database = database
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        # Resolved per request, not captured once: the demo reset swaps the
        # whole database object, and later requests must use the new one.
        database = getattr(getattr(self.app, "state", None), "db", None) or self.database
        session: Session = database.session_factory()
        state["db"] = session
        try:
            token = _parse_cookies(scope).get(authentication.SESSION_COOKIE)
            resolved = authentication.resolve_session(session, token=token, settings=self.settings)
            if resolved is None:
                state["user"] = None
                state["session_row"] = None
                state["identity"] = None
            else:
                user, row = resolved
                state["user"] = user
                state["session_row"] = row
                # Read once, while the session is open: the error handler may
                # render after this session has been rolled back and closed.
                state["identity"] = {
                    "id": user.id,
                    "role": user.role,
                    "display_name": user.display_name or "",
                    "email": user.email,
                }
                authentication.touch_session(session, row, settings=self.settings)
            state["ip"] = (scope.get("client")[0] if scope.get("client") else "") or ""
            state["path"] = scope.get("path", "")
        except Exception:  # pragma: no cover - identity problems must not 500 the site
            logger.exception("Request identity resolution failed")
            session.rollback()
            state["user"] = None
            state["session_row"] = None

        try:
            await self.app(scope, receive, send)
        except Exception:
            session.rollback()
            raise
        else:
            try:
                session.commit()
            except Exception:  # pragma: no cover - surfaced in logs, not to the page
                session.rollback()
                logger.exception("Commit failed for %s", scope.get("path"))
                raise
        finally:
            session.close()


class CSRFMiddleware:
    """Signed double-submit CSRF protection on every state-changing request."""

    def __init__(self, app, *, secret_key: str, cookie_secure: bool) -> None:
        self.app = app
        self.secret_key = secret_key
        self.cookie_secure = cookie_secure

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        cookies = _parse_cookies(scope)
        nonce = security_policy.verify_signed_csrf(self.secret_key, cookies.get(CSRF_COOKIE, ""))
        issue_cookie = nonce is None
        if nonce is None:
            nonce = security_policy.new_csrf_nonce()
        state["csrf_token"] = nonce

        cookie_bytes = (
            _cookie_header(
                CSRF_COOKIE,
                security_policy.sign_csrf(self.secret_key, nonce),
                secure=self.cookie_secure,
                max_age=60 * 60 * 24 * 30,
            )
            if issue_cookie
            else None
        )

        async def send_with_cookie(message):
            if message["type"] == "http.response.start" and cookie_bytes is not None:
                headers = list(message.get("headers", []))
                headers.append((b"set-cookie", cookie_bytes))
                message = {**message, "headers": headers}
            await send(message)

        method = scope.get("method", "GET").upper()
        if method in SAFE_METHODS:
            await self.app(scope, receive, send_with_cookie)
            return

        body, replay = await self._buffer_body(receive)
        if len(body) > MAX_BUFFERED_BODY:
            await self._reject(scope, send_with_cookie, "The request body is too large.", 413)
            return

        submitted = _header(scope, CSRF_HEADER.encode("latin-1")) or _extract_submitted_token(
            body, _header(scope, b"content-type")
        )
        if not submitted or not security_policy.constant_time_equals(submitted, nonce):
            session = state.get("db")
            if session is not None:
                try:
                    audit_log.record(
                        session,
                        actor=state.get("user"),
                        action="security.csrf_rejected",
                        target_type="route",
                        target_id=scope.get("path", ""),
                        reason="missing or mismatched CSRF token",
                        ip=state.get("ip", ""),
                    )
                except Exception:  # pragma: no cover - never mask the 403
                    logger.exception("Could not record CSRF rejection")
            logger.warning("CSRF rejection on %s", scope.get("path"))
            await self._reject(
                scope,
                send_with_cookie,
                "This form has expired or came from another site. Reload the page and try again.",
                403,
            )
            return

        await self.app(scope, replay, send_with_cookie)

    async def _buffer_body(self, receive):
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":  # pragma: no cover - client went away
                break
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)

        async def replay():
            return {"type": "http.request", "body": body, "more_body": False}

        return body, replay

    async def _reject(self, scope, send, message: str, status: int) -> None:
        body = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Request blocked</title>"
            "<link rel=\"stylesheet\" href=\"/static/css/app.css\"></head>"
            "<body><main class=\"container section\"><h1>Request blocked</h1>"
            f"<p>{message}</p>"
            "<p><a href=\"/\">Return to the home page</a></p></main></body></html>"
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    """Response headers from brief section 11."""

    def __init__(self, app, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in {b"x-content-type-options", b"x-frame-options"}
                ]
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"same-origin"))
                headers.append(
                    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()")
                )
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"content-security-policy", CSP.encode("latin-1")))
                if self.hsts:
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def new_form_token() -> str:
    """Idempotency token embedded in a form so a double click creates one record."""
    return secrets.token_urlsafe(32)
