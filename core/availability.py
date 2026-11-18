"""Availability windows, compared across timezones.

Availability is recorded as a date range plus the IANA timezone the student
meant. The date range is what the hard constraint uses; the timezone is shown
as a caveat when it differs from the partner's, because the prototype has no
meeting scheduler and must not imply one.

Unknown availability is never treated as "unavailable": it makes a candidate
provisional and says so, exactly as unknown proficiency is handled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Windows ships no IANA database, so a valid-looking name is accepted when the
#: ``tzdata`` package is absent. Nothing here schedules meetings; the name is
#: compared and displayed, which does not need the transition rules.
_ZONE_PATTERN = re.compile(r"^(UTC|[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){1,2})$")

STATUS_AVAILABLE = "available"
STATUS_PARTIAL = "partial"
STATUS_OUTSIDE = "outside"
STATUS_UNKNOWN = "unknown"

STATUS_TEXT = {
    STATUS_AVAILABLE: "Available for the whole project",
    STATUS_PARTIAL: "Available for part of the project",
    STATUS_OUTSIDE: "Not available during the project",
    STATUS_UNKNOWN: "Availability not recorded",
}


@dataclass(frozen=True)
class AvailabilityCheck:
    status: str
    overlap_days: int
    project_days: int
    project_timezone: str
    student_timezone: str
    detail: str

    @property
    def text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status)

    @property
    def timezone_differs(self) -> bool:
        return (
            bool(self.student_timezone)
            and bool(self.project_timezone)
            and self.student_timezone != self.project_timezone
        )

    @property
    def is_hard_failure(self) -> bool:
        return self.status == STATUS_OUTSIDE


def is_valid_timezone(name: str) -> bool:
    if not name or not isinstance(name, str):
        return False
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        # No local transition database (typical on Windows): fall back to the
        # IANA naming shape, which is all this prototype needs.
        return bool(_ZONE_PATTERN.match(name))
    except (ValueError, TypeError):
        return False
    return True


def overlap_days(
    first_from: date | None,
    first_to: date | None,
    second_from: date,
    second_to: date,
) -> int:
    """Inclusive day count where two date ranges overlap. Zero when they do not."""
    if first_from is None or first_to is None:
        return 0
    start = max(first_from, second_from)
    end = min(first_to, second_to)
    return max((end - start).days + 1, 0)


def check(
    *,
    available_from: date | None,
    available_to: date | None,
    student_timezone: str,
    project_start: date,
    project_deadline: date,
    project_timezone: str,
) -> AvailabilityCheck:
    project_days = max((project_deadline - project_start).days + 1, 0)
    if available_from is None or available_to is None:
        return AvailabilityCheck(
            status=STATUS_UNKNOWN,
            overlap_days=0,
            project_days=project_days,
            project_timezone=project_timezone,
            student_timezone=student_timezone,
            detail=(
                "No availability window recorded, so the availability constraint "
                "cannot be checked. Shown as unverified, not as zero."
            ),
        )

    overlap = overlap_days(available_from, available_to, project_start, project_deadline)
    if overlap == 0:
        return AvailabilityCheck(
            status=STATUS_OUTSIDE,
            overlap_days=0,
            project_days=project_days,
            project_timezone=project_timezone,
            student_timezone=student_timezone,
            detail=(
                f"Available {available_from.isoformat()} to {available_to.isoformat()}, "
                f"which does not overlap the project ({project_start.isoformat()} to "
                f"{project_deadline.isoformat()})."
            ),
        )

    if overlap >= project_days:
        return AvailabilityCheck(
            status=STATUS_AVAILABLE,
            overlap_days=overlap,
            project_days=project_days,
            project_timezone=project_timezone,
            student_timezone=student_timezone,
            detail=f"The availability window covers all {project_days} days of the project.",
        )

    return AvailabilityCheck(
        status=STATUS_PARTIAL,
        overlap_days=overlap,
        project_days=project_days,
        project_timezone=project_timezone,
        student_timezone=student_timezone,
        detail=(
            f"The availability window covers {overlap} of the project's {project_days} days; "
            "the start or end of the project falls outside it. Recorded as a caveat, not a refusal."
        ),
    )


def timezone_note(student_timezone: str, project_timezone: str) -> str:
    if not student_timezone or not project_timezone or student_timezone == project_timezone:
        return ""
    return (
        f"Availability is recorded in {student_timezone}; the partner works in "
        f"{project_timezone}. Confirm working hours with the student before confirming a team."
    )
