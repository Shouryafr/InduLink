"""Security policy primitives: pure functions, no web or ORM imports.

Covers password policy, e-mail normalisation, CSRF token signing and sign-in
lockout arithmetic (brief section 11).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

# --- E-mail ---------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
_EMAIL_MAX = 254


def normalize_email(raw: str) -> str:
    """Trim and lower-case an address.

    Returns "" for input that cannot be an address; callers turn that into a
    field-level error. Normalising keeps ``Ada@x.com`` and ``ada@x.com`` from
    becoming two accounts.
    """
    if raw is None:
        return ""
    value = raw.strip().lower()
    if len(value) > _EMAIL_MAX:
        return ""
    return value


def is_valid_email(raw: str) -> bool:
    value = normalize_email(raw)
    return bool(value) and bool(_EMAIL_RE.match(value))


# --- Passwords ------------------------------------------------------------

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 200  # bcrypt silently truncates past 72 bytes; we cap first


def password_policy_errors(password: str, email: str = "") -> list[str]:
    """Return a list of human-readable policy failures.

    Minimum length 10 (brief section 11). A password equal to the address, or
    made of a single repeated character, is rejected as trivially guessable.
    """
    errors: list[str] = []
    if password is None or not isinstance(password, str):
        return ["Enter a password."]
    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(
            f"Use at least {PASSWORD_MIN_LENGTH} characters. This one has {len(password)}."
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        errors.append(f"Use at most {PASSWORD_MAX_LENGTH} characters.")
    if password.strip() == "":
        errors.append("The password cannot be only spaces.")
    if len(set(password)) == 1 and len(password) >= PASSWORD_MIN_LENGTH:
        errors.append("The password cannot repeat a single character.")
    normalized_email = normalize_email(email)
    if normalized_email and password.strip().lower() == normalized_email:
        errors.append("The password cannot be your e-mail address.")
    return errors


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two strings without leaking length-dependent timing."""
    if left is None or right is None:
        return False
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


# --- CSRF -----------------------------------------------------------------


def new_csrf_nonce() -> str:
    """A random value a browser can hold in a cookie and echo into a form."""
    return secrets.token_urlsafe(24)


def _csrf_signature(secret: str, nonce: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:32]


def sign_csrf(secret: str, nonce: str) -> str:
    """Return ``nonce.signature`` for a signed double-submit CSRF cookie.

    Signing stops a sibling subdomain from planting an arbitrary cookie value
    and pairing it with a matching form field: without the server key the
    signature does not verify.
    """
    if not secret:
        raise ValueError("A secret key is required to sign CSRF tokens.")
    if not nonce:
        raise ValueError("A CSRF nonce is required.")
    return f"{nonce}.{_csrf_signature(secret, nonce)}"


def verify_signed_csrf(secret: str, signed_value: str) -> str | None:
    """Return the nonce when ``signed_value`` carries a valid signature."""
    if not secret or not signed_value or "." not in signed_value:
        return None
    nonce, _, signature = signed_value.rpartition(".")
    if not nonce or not signature:
        return None
    if not hmac.compare_digest(signature, _csrf_signature(secret, nonce)):
        return None
    return nonce


# --- Sign-in throttling ---------------------------------------------------


def is_locked_out(
    failures: int,
    last_failure_at: datetime | None,
    *,
    max_attempts: int,
    lockout: timedelta,
    now: datetime,
) -> bool:
    """Temporary lockout after ``max_attempts`` consecutive failures."""
    if failures < max_attempts or last_failure_at is None:
        return False
    return now - last_failure_at < lockout


def lockout_expiry(
    last_failure_at: datetime, *, lockout: timedelta
) -> datetime:
    return last_failure_at + lockout


GENERIC_SIGNIN_ERROR = "E-mail or password not recognised."
GENERIC_LOCKED_ERROR = (
    "Too many attempts. Sign-in is paused for a few minutes, then you can try again."
)
