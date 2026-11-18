"""Domain errors that routers translate into HTTP responses.

Raising these keeps permission and validation logic in the service layer,
where it is unit-testable, instead of scattered through templates. A denial
also carries what the audit handler needs, so every 403 is recorded once, in
one place, even though the request transaction is rolled back.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for expected, user-facing failures."""

    status_code = 400
    title = "That did not work"

    def __init__(
        self,
        message: str,
        *,
        field_errors: dict[str, list[str]] | None = None,
        target_type: str = "",
        target_id: str | int | None = "",
        reason: str = "",
    ):
        super().__init__(message)
        self.message = message
        self.field_errors = field_errors or {}
        self.target_type = target_type
        self.target_id = "" if target_id is None else str(target_id)
        self.reason = reason


class ValidationError(DomainError):
    status_code = 422
    title = "Check the highlighted fields"


class PermissionDenied(DomainError):
    """Server-side authorization failure. Hiding a button is not access control."""

    status_code = 403
    title = "You do not have access to this"
    action = "authz.denied"


class AuthRequired(DomainError):
    """No valid session. The handler redirects to sign-in."""

    status_code = 401
    title = "Sign in to continue"
    action = "authz.unauthenticated"


class NotFound(DomainError):
    status_code = 404
    title = "Not found"


class Conflict(DomainError):
    """State changed underneath the user: re-read and try again."""

    status_code = 409
    title = "This changed while you were working"


class StaleData(DomainError):
    """Availability or capacity is out of date; revalidate before confirming."""

    status_code = 409
    title = "This needs revalidating first"


class RateLimited(DomainError):
    status_code = 429
    title = "Too many requests"
