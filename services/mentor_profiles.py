"""Mentor expertise registry and capacity (brief sections 8.5, 8.7).

Structured skill tags only: free-text expertise is never matchable. Capacity is
expressed as weekly hours against a weekly cap, not project count alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core import skills as skill_rules
from data.models import (
    Mentor,
    MentorAssignment,
    MentorSkill,
    MentorUnavailability,
    User,
)
from services import audit_log
from services.errors import NotFound, PermissionDenied, ValidationError

MAX_WEEKLY_CAP = 60


def get_or_create(session: Session, user: User) -> Mentor:
    mentor = session.scalar(select(Mentor).where(Mentor.user_id == user.id))
    if mentor is not None:
        return mentor
    if user.role != "mentor":
        raise PermissionDenied("Only a mentor account has a mentor registry entry.")
    mentor = Mentor(user_id=user.id)
    session.add(mentor)
    session.flush()
    return mentor


def get_by_id(session: Session, mentor_id: int) -> Mentor:
    mentor = session.get(Mentor, mentor_id)
    if mentor is None:
        raise NotFound("That mentor does not exist.")
    return mentor


def update_registry(
    session: Session,
    *,
    mentor: Mentor,
    actor: User,
    domains: str = "",
    experience: str = "",
    mentoring_experience_years: int | None = None,
    mentoring_projects_count: int | None = None,
    max_active_projects: int = 3,
    weekly_hours_cap: int = 10,
    hours_per_project: int | None = 2,
    preferred_levels: str = "",
    ip: str = "",
) -> Mentor:
    if actor.id != mentor.user_id and actor.role != "admin":
        raise PermissionDenied("You can only edit your own registry entry.")

    errors: dict[str, list[str]] = {}
    if max_active_projects is None or max_active_projects < 1:
        errors["max_active_projects"] = ["Capacity must be at least one project."]
    if weekly_hours_cap is None or weekly_hours_cap < 1 or weekly_hours_cap > MAX_WEEKLY_CAP:
        errors["weekly_hours_cap"] = [f"Enter a weekly cap between 1 and {MAX_WEEKLY_CAP} hours."]
    if hours_per_project is not None and hours_per_project < 0:
        errors["hours_per_project"] = ["Hours per project cannot be negative."]
    if mentoring_experience_years is not None and mentoring_experience_years < 0:
        errors["mentoring_experience_years"] = ["Years of mentoring cannot be negative."]
    if mentoring_projects_count is not None and mentoring_projects_count < 0:
        errors["mentoring_projects_count"] = ["Projects mentored cannot be negative."]
    if errors:
        raise ValidationError("Some registry details need attention.", field_errors=errors)

    mentor.domains = (domains or "").strip()
    mentor.experience = (experience or "").strip()
    mentor.mentoring_experience_years = mentoring_experience_years
    mentor.mentoring_projects_count = mentoring_projects_count
    mentor.max_active_projects = max_active_projects
    mentor.weekly_hours_cap = weekly_hours_cap
    mentor.hours_per_project = hours_per_project
    mentor.preferred_levels = (preferred_levels or "").strip()[:60]

    audit_log.record(
        session,
        actor=actor,
        action="mentor.registry_updated",
        target_type="mentor",
        target_id=mentor.id,
        detail={"weekly_hours_cap": weekly_hours_cap, "max_active_projects": max_active_projects},
        ip=ip,
    )
    return mentor


def set_skill(
    session: Session,
    *,
    mentor: Mentor,
    skill_id: int,
    level: int | None,
    evidence_basis: str = "",
    actor: User,
    ip: str = "",
) -> MentorSkill | None:
    if actor.id != mentor.user_id and actor.role != "admin":
        raise PermissionDenied("You can only edit your own registry entry.")

    row = session.scalar(
        select(MentorSkill).where(
            MentorSkill.mentor_id == mentor.id, MentorSkill.skill_id == skill_id
        )
    )
    if level is None:
        if row is not None:
            session.delete(row)
            audit_log.record(
                session,
                actor=actor,
                action="mentor_skill.removed",
                target_type="mentor_skill",
                target_id=f"{mentor.id}:{skill_id}",
                ip=ip,
            )
        return None

    if not skill_rules.is_valid_level(level):
        raise ValidationError(
            "Choose a level from 1 to 5.",
            field_errors={"level": ["Choose a level from 1 to 5."]},
        )
    if row is None:
        row = MentorSkill(mentor_id=mentor.id, skill_id=skill_id, level=level)
        session.add(row)
    row.level = level
    row.evidence_basis = (evidence_basis or "").strip()[:300]
    audit_log.record(
        session,
        actor=actor,
        action="mentor_skill.set",
        target_type="mentor_skill",
        target_id=f"{mentor.id}:{skill_id}",
        after_ref=str(level),
        ip=ip,
    )
    return row


def add_unavailability(
    session: Session,
    *,
    mentor: Mentor,
    from_date: date,
    to_date: date,
    reason: str,
    actor: User,
    ip: str = "",
) -> MentorUnavailability:
    if actor.id != mentor.user_id and actor.role != "admin":
        raise PermissionDenied("You can only edit your own availability.")
    if to_date < from_date:
        raise ValidationError(
            "The end date must not be before the start date.",
            field_errors={"to_date": ["The end date must not be before the start date."]},
        )
    row = MentorUnavailability(
        mentor_id=mentor.id, from_date=from_date, to_date=to_date, reason=reason[:200]
    )
    session.add(row)
    audit_log.record(
        session,
        actor=actor,
        action="mentor.unavailability_added",
        target_type="mentor",
        target_id=mentor.id,
        detail={"from": from_date.isoformat(), "to": to_date.isoformat()},
        ip=ip,
    )
    return row


def is_unavailable_on(session: Session, mentor_id: int, day: date) -> MentorUnavailability | None:
    return session.scalar(
        select(MentorUnavailability).where(
            MentorUnavailability.mentor_id == mentor_id,
            MentorUnavailability.from_date <= day,
            MentorUnavailability.to_date >= day,
        )
    )


@dataclass
class Workload:
    mentor_id: int
    active_projects: int
    max_active_projects: int
    hours_per_week: int
    weekly_cap: int
    utilization_pct: int
    capacity_remaining: int
    at_capacity: bool
    high_utilization: bool

    @property
    def tone(self) -> str:
        if self.at_capacity:
            return "risk"
        if self.high_utilization:
            return "warn"
        return "ok"

    @property
    def label(self) -> str:
        if self.at_capacity:
            return "At capacity"
        if self.high_utilization:
            return "High utilization"
        return "Capacity available"


def workload(session: Session, mentor: Mentor, *, high_pct: int = 85) -> Workload:
    """Utilisation from weekly hours, compared with the mentor's own cap."""
    active = int(
        session.scalar(
            select(func.count(MentorAssignment.id)).where(
                MentorAssignment.mentor_id == mentor.id,
                MentorAssignment.state == "confirmed",
            )
        )
        or 0
    )
    per_project = mentor.hours_per_project or 0
    hours = active * per_project
    cap = mentor.weekly_hours_cap or 1
    pct = int(round(hours / cap * 100)) if cap else 0
    at_capacity = active >= mentor.max_active_projects or hours >= cap
    return Workload(
        mentor_id=mentor.id,
        active_projects=active,
        max_active_projects=mentor.max_active_projects,
        hours_per_week=hours,
        weekly_cap=cap,
        utilization_pct=pct,
        capacity_remaining=max(0, mentor.max_active_projects - active),
        at_capacity=at_capacity,
        high_utilization=(not at_capacity) and pct >= high_pct,
    )


def skill_levels(session: Session, mentor: Mentor) -> dict[int, int]:
    rows = session.scalars(
        select(MentorSkill).where(MentorSkill.mentor_id == mentor.id)
    ).all()
    return {row.skill_id: row.level for row in rows}
