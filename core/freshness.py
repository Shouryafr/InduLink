"""Data-freshness labels (brief sections 3, 6 item 5).

Availability and requirement data goes out of date. Rather than hiding that,
every recommendation shows when the data behind it was last updated and marks
it stale past a documented threshold. Stale data is revalidated before any
assignment is confirmed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

AVAILABILITY_THRESHOLD_DAYS = 30


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat them as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def age_days(updated_at: datetime | date | None, now: datetime | None = None) -> int | None:
    if updated_at is None:
        return None
    now = now or utcnow()
    if isinstance(updated_at, datetime):
        delta = _as_aware(now) - _as_aware(updated_at)
        return max(delta.days, 0)
    return max((now.date() - updated_at).days, 0)


def describe_age(days: int | None) -> str:
    """Plain-English age, used in freshness stamps."""
    if days is None:
        return "never updated"
    if days <= 0:
        return "updated today"
    if days == 1:
        return "updated yesterday"
    if days < 7:
        return f"updated {days} days ago"
    if days < 14:
        return "updated last week"
    if days < 60:
        weeks = days // 7
        return f"updated {weeks} weeks ago"
    if days < 730:
        months = max(days // 30, 1)
        return f"updated {months} months ago"
    years = days // 365
    return f"updated {years} years ago"


def stamp(
    updated_at: datetime | date | None,
    *,
    now: datetime | None = None,
    threshold_days: int = AVAILABILITY_THRESHOLD_DAYS,
    subject: str = "Availability",
) -> dict:
    """Build a freshness stamp for a template.

    ``stale`` is true past ``threshold_days``. The label always names the
    subject so it is unambiguous which record is being described.
    """
    days = age_days(updated_at, now)
    stale = days is not None and days > threshold_days
    if updated_at is None:
        label = f"{subject} never updated"
    else:
        label = f"{subject} {describe_age(days)}"
    if stale:
        label = f"{label} — revalidate before relying on it"
    return {
        "days": days,
        "stale": stale,
        "label": label,
        "tone": "warn" if stale else "muted",
        "updated_at": updated_at,
        "threshold_days": threshold_days,
    }
