"""Requirement weights, validation and the project state machine.

Weights are entered as relative importance, normalised to sum 1.0 on save and
shown to the user as percentages. The stored value is validated on write and
again in the database (brief sections 5 and 7.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

WEIGHT_TOLERANCE = 0.001
WEIGHT_DECIMALS = 3

REQUIRED_LEVEL_MIN = 1
REQUIRED_LEVEL_MAX = 5


class ProjectState(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    OPEN = "open"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


VISIBILITIES: tuple[str, ...] = ("public", "university", "private")

#: Legal state transitions. Anything else is refused, not silently coerced.
ALLOWED_TRANSITIONS: dict[ProjectState, frozenset[ProjectState]] = {
    ProjectState.DRAFT: frozenset({ProjectState.PENDING_APPROVAL, ProjectState.CANCELLED}),
    ProjectState.PENDING_APPROVAL: frozenset(
        {ProjectState.APPROVED, ProjectState.DRAFT, ProjectState.CANCELLED}
    ),
    ProjectState.APPROVED: frozenset({ProjectState.OPEN, ProjectState.CANCELLED}),
    ProjectState.OPEN: frozenset({ProjectState.ACTIVE, ProjectState.CANCELLED}),
    ProjectState.ACTIVE: frozenset({ProjectState.COMPLETED, ProjectState.CANCELLED}),
    ProjectState.COMPLETED: frozenset(),
    ProjectState.CANCELLED: frozenset(),
}

#: Projects a student may browse. Draft and pending work is private to its owner.
STUDENT_VISIBLE_STATES: frozenset[ProjectState] = frozenset(
    {ProjectState.APPROVED, ProjectState.OPEN, ProjectState.ACTIVE, ProjectState.COMPLETED}
)

#: Projects that accept new candidate recommendations.
MATCHABLE_STATES: frozenset[ProjectState] = frozenset({ProjectState.OPEN})

#: States in which requirements may still be edited (creating a new version).
EDITABLE_STATES: frozenset[ProjectState] = frozenset(
    {
        ProjectState.DRAFT,
        ProjectState.PENDING_APPROVAL,
        ProjectState.APPROVED,
        ProjectState.OPEN,
    }
)


class TransitionError(ValueError):
    """Raised for an illegal state transition."""


def can_transition(current: ProjectState | str, target: ProjectState | str) -> bool:
    try:
        current_state = ProjectState(current)
        target_state = ProjectState(target)
    except ValueError:
        return False
    return target_state in ALLOWED_TRANSITIONS.get(current_state, frozenset())


def ensure_transition(current: ProjectState | str, target: ProjectState | str) -> ProjectState:
    if not can_transition(current, target):
        raise TransitionError(f"Cannot move a project from {current} to {target}.")
    return ProjectState(target)


def allowed_next_states(current: ProjectState | str) -> list[ProjectState]:
    try:
        state = ProjectState(current)
    except ValueError:
        return []
    return sorted(ALLOWED_TRANSITIONS[state], key=lambda s: s.value)


# --- Weights --------------------------------------------------------------


def normalize_weights(raw_weights: list[float]) -> list[float]:
    """Normalise relative importances so they sum to exactly 1.0.

    Zero and negative inputs are rejected: a requirement with no weight would
    be displayed but never scored, which is misleading. The rounding
    remainder is absorbed by the largest weight so the stored total is exact.
    """
    if not raw_weights:
        raise ValueError("At least one weight is required.")
    for value in raw_weights:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("Weights must be numbers.")
        if value <= 0:
            raise ValueError("Every weight must be greater than zero.")
    total = float(sum(raw_weights))
    if total <= 0:
        raise ValueError("Weights must sum to a positive number.")

    normalised = [round(value / total, WEIGHT_DECIMALS) for value in raw_weights]
    drift = round(1.0 - sum(normalised), WEIGHT_DECIMALS)
    if drift:
        largest = max(range(len(normalised)), key=lambda i: normalised[i])
        normalised[largest] = round(normalised[largest] + drift, WEIGHT_DECIMALS)
    return normalised


def weights_sum(weights: list[float]) -> float:
    return round(float(sum(weights)), WEIGHT_DECIMALS)


def weights_valid(weights: list[float], tolerance: float = WEIGHT_TOLERANCE) -> bool:
    if not weights:
        return False
    if any(w is None or w <= 0 for w in weights):
        return False
    return abs(weights_sum(weights) - 1.0) <= tolerance


def as_percentages(weights: list[float]) -> list[int]:
    """Percent labels that add up to 100 so the UI never shows 99% or 101%."""
    if not weights:
        return []
    total = sum(weights)
    if total <= 0:
        return [0 for _ in weights]
    raw = [w / total * 100 for w in weights]
    floors = [int(value) for value in raw]
    remainder = 100 - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)
    for index in order[:remainder]:
        floors[index] += 1
    return floors


# --- Requirement sets -----------------------------------------------------


@dataclass(frozen=True)
class RequirementDraft:
    """One requirement as entered on the challenge form."""

    skill_id: int | None
    required_level: int | None
    weight_raw: float | None
    is_mandatory: bool = False

    @property
    def label(self) -> str:
        return f"skill {self.skill_id}" if self.skill_id else "this requirement"


def validate_requirement_set(
    items: list[RequirementDraft], *, today: date | None = None
) -> tuple[list[float], dict[int, list[str]]]:
    """Validate a requirement set and return normalised weights plus errors.

    Errors are keyed by the index of the offending row so the form can place
    a message beside the field that failed, rather than one global sentence.
    """
    errors: dict[int, list[str]] = {}
    if not items:
        return [], {0: ["Add at least one required skill."]}

    seen: dict[int, int] = {}
    for index, item in enumerate(items):
        row_errors: list[str] = []
        if item.skill_id is None:
            row_errors.append("Choose a skill from the registry.")
        else:
            if item.skill_id in seen:
                row_errors.append("This skill is already listed above.")
            else:
                seen[item.skill_id] = index
        if item.required_level is None:
            row_errors.append("Choose a required level.")
        elif not (
            isinstance(item.required_level, int)
            and REQUIRED_LEVEL_MIN <= item.required_level <= REQUIRED_LEVEL_MAX
        ):
            row_errors.append("Required level must be 1 to 5.")
        if item.weight_raw is None:
            row_errors.append("Enter an importance value.")
        elif item.weight_raw <= 0:
            row_errors.append("Importance must be greater than zero.")
        if row_errors:
            errors[index] = row_errors

    if errors:
        return [], errors

    try:
        weights = normalize_weights([float(item.weight_raw) for item in items])  # type: ignore[arg-type]
    except ValueError as exc:
        return [], {0: [str(exc)]}
    return weights, {}


def mandatory_gaps(
    requirements: list[tuple[str, int, bool]], effective_levels: dict[str, int | None]
) -> list[str]:
    """Names of mandatory requirements not met by a known effective level.

    Unknown is never treated as zero, so it is not reported as a gap here: it
    is reported separately as "evidence needed" (brief section 6, item 2).
    """
    gaps: list[str] = []
    for name, required, mandatory in requirements:
        if not mandatory:
            continue
        level = effective_levels.get(name)
        if level is not None and level < required:
            gaps.append(name)
    return gaps
