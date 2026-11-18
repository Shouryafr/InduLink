"""Mentor recommendation, request, confirmation and decline.

Mentor fit is rules-based: structured skill levels against the challenge
requirements, availability, and remaining capacity in weekly hours. Soft scores
never override the hard constraints — a mentor who is unavailable for the
project window or already at capacity cannot be confirmed.

Exactly one mentor may be confirmed per project. That is enforced by the
database (a partial unique index) as well as here, so two reviewers confirming
at the same moment produce one success and one explained conflict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import evidence_policy
from data.models import (
    Mentor,
    MentorAssignment,
    MentorUnavailability,
    Project,
    ProjectVersion,
    Requirement,
    StudentProfile,
    Team,
    User,
    utcnow,
)
from services import audit_log, matching, mentor_profiles, notifications
from services.errors import Conflict, NotFound, PermissionDenied, ValidationError

@dataclass
class MentorFit:
    mentor_id: int
    user_id: int
    name: str
    score: float
    status: str
    reasons: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    unavailable_until: date | None = None
    weekly_hours: int = 0
    weekly_cap: int = 0
    active_projects: int = 0
    max_projects: int = 0

    @property
    def tone(self) -> str:
        return {
            "available": "ok",
            "high_utilization": "warn",
            "at_capacity": "risk",
            "unavailable": "risk",
        }.get(self.status, "unknown")

    @property
    def status_text(self) -> str:
        return {
            "available": "Available",
            "high_utilization": "High utilization",
            "at_capacity": "At capacity",
            "unavailable": "Unavailable for this window",
        }.get(self.status, self.status)

    @property
    def eligible(self) -> bool:
        return self.status in {"available", "high_utilization"}


def _unavailability(session: Session, mentor_id: int, start: date, deadline: date) -> MentorUnavailability | None:
    return session.scalar(
        select(MentorUnavailability).where(
            MentorUnavailability.mentor_id == mentor_id,
            MentorUnavailability.from_date <= deadline,
            MentorUnavailability.to_date >= start,
        )
    )


def fit_for_project(
    session: Session, project: Project, *, high_pct: int = 85
) -> list[MentorFit]:
    """Score every mentor against the challenge. One query per table."""
    version: ProjectVersion | None = session.scalar(
        select(ProjectVersion).where(
            ProjectVersion.project_id == project.id, ProjectVersion.is_current.is_(True)
        )
    )
    requirements: list[Requirement] = []
    skill_names: dict[int, str] = {}
    if version is not None:
        requirements = list(
            session.scalars(
                select(Requirement)
                .where(Requirement.version_id == version.id)
                .order_by(Requirement.is_mandatory.desc(), Requirement.weight.desc())
            ).all()
        )
    from data.models import Skill

    if requirements:
        skill_names = {
            skill.id: skill.canonical_name
            for skill in session.scalars(
                select(Skill).where(Skill.id.in_([row.skill_id for row in requirements]))
            ).all()
        }

    fits: list[MentorFit] = []
    mentors = list(session.scalars(select(Mentor).order_by(Mentor.id)).all())
    for mentor in mentors:
        levels = mentor_profiles.skill_levels(session, mentor)
        workload = mentor_profiles.workload(session, mentor, high_pct=high_pct)
        score = 0.0
        reasons: list[str] = []
        covered: list[str] = []
        gaps: list[str] = []
        for row in requirements:
            name = skill_names.get(row.skill_id, f"skill {row.skill_id}")
            level = levels.get(row.skill_id)
            if level is None:
                gaps.append(f"{name} (needs level {row.required_level})")
                reasons.append(
                    f"{name}: not listed in this mentor's expertise registry, so it contributes no points."
                )
                continue
            points = evidence_policy.contribution(level, row.required_level, row.weight)
            score += points
            covered.append(name)
            reasons.append(
                f"{name}: mentor level {level} against required {row.required_level} "
                f"({points:.1f} points of a {row.weight:.0%} weight)."
            )

        window = _unavailability(session, mentor.id, project.start_date, project.deadline)
        if window is not None:
            status = "unavailable"
            reasons.append(
                f"Recorded unavailable from {window.from_date.isoformat()} to "
                f"{window.to_date.isoformat()} ({window.reason or 'no reason given'}), which "
                "overlaps this project."
            )
        elif workload.at_capacity:
            status = "at_capacity"
            reasons.append(
                f"Already at capacity: {workload.active_projects} of "
                f"{workload.max_active_projects} projects and {workload.hours_per_week} of "
                f"{workload.weekly_cap} weekly hours."
            )
        elif workload.high_utilization:
            status = "high_utilization"
            reasons.append(f"Utilisation is {workload.utilization_pct}% of the weekly cap.")
        else:
            status = "available"
            reasons.append(
                f"{workload.capacity_remaining} project slot(s) and "
                f"{max(workload.weekly_cap - workload.hours_per_week, 0)} weekly hour(s) remain."
            )

        fits.append(
            MentorFit(
                mentor_id=mentor.id,
                user_id=mentor.user_id,
                name=mentor.user.display_name if mentor.user else f"Mentor {mentor.id}",
                score=round(score, 1),
                status=status,
                reasons=reasons,
                covered=covered,
                gaps=gaps,
                unavailable_until=window.to_date if window else None,
                weekly_hours=workload.hours_per_week,
                weekly_cap=workload.weekly_cap,
                active_projects=workload.active_projects,
                max_projects=workload.max_active_projects,
            )
        )

    order = {"available": 0, "high_utilization": 1, "at_capacity": 2, "unavailable": 3}
    fits.sort(key=lambda fit: (order.get(fit.status, 9), -fit.score, fit.mentor_id))
    return fits


def assignments_for_project(session: Session, project_id: int) -> list[MentorAssignment]:
    return list(
        session.scalars(
            select(MentorAssignment)
            .where(MentorAssignment.project_id == project_id)
            .order_by(MentorAssignment.created_at.desc())
        ).all()
    )


def requests_for_mentor(session: Session, mentor_id: int) -> list[MentorAssignment]:
    return list(
        session.scalars(
            select(MentorAssignment)
            .where(MentorAssignment.mentor_id == mentor_id)
            .order_by(MentorAssignment.created_at.desc())
        ).all()
    )


def get(session: Session, assignment_id: int) -> MentorAssignment:
    row = session.get(MentorAssignment, assignment_id)
    if row is None:
        raise NotFound("That mentor request does not exist.")
    return row


def request(
    session: Session,
    *,
    project: Project,
    mentor: Mentor,
    actor: User,
    reason: str = "",
    fit: dict | None = None,
    ip: str = "",
) -> MentorAssignment:
    """Record a proposed mentor for a challenge."""
    if actor.role not in {"industry", "admin"} or (
        actor.role == "industry" and project.owner_id != actor.id
    ):
        raise PermissionDenied(
            "Only the owning partner or an administrator can request a mentor."
        )
    if project.state not in {"approved", "open", "active"}:
        raise Conflict("A mentor can only be requested for an approved, open or active challenge.")
    existing = session.scalar(
        select(MentorAssignment).where(
            MentorAssignment.project_id == project.id,
            MentorAssignment.mentor_id == mentor.id,
        )
    )
    if existing is not None:
        raise Conflict(
            "That mentor already has an open or confirmed record for this challenge."
        )
    confirmed = session.scalar(
        select(MentorAssignment).where(
            MentorAssignment.project_id == project.id,
            MentorAssignment.state == "confirmed",
        )
    )
    if confirmed is not None:
        raise Conflict("This challenge already has a confirmed mentor.")

    window = _unavailability(session, mentor.id, project.start_date, project.deadline)
    if window is not None:
        raise Conflict(
            f"{mentor.user.display_name if mentor.user else 'That mentor'} is recorded as "
            f"unavailable from {window.from_date.isoformat()} to {window.to_date.isoformat()}."
        )

    row = MentorAssignment(
        project_id=project.id,
        mentor_id=mentor.id,
        state="proposed",
        reason=(reason or "Recommended from the mentor fit list.")[:300],
        fit_snapshot=fit or {},
    )
    session.add(row)
    session.flush()
    audit_log.record(
        session,
        actor=actor,
        action="mentor.requested",
        target_type="mentor_assignment",
        target_id=row.id,
        reason=row.reason[:200],
        detail={"project_id": project.id, "mentor_id": mentor.id},
        ip=ip,
    )
    notifications.notify(
        session,
        user_id=mentor.user_id,
        kind="mentor_request",
        title="A mentor request needs your decision",
        body=f"{project.title}: confirm or decline this request. Only one mentor can be confirmed.",
        href="/mentor/requests",
    )
    return row


def _project_team_user_ids(session: Session, project_id: int) -> list[int]:
    team = session.scalar(
        select(Team).where(Team.project_id == project_id, Team.state == "confirmed")
    )
    if team is None:
        return []
    ids: list[int] = []
    for member in team.members:
        profile = session.get(StudentProfile, member.student_id)
        if profile is not None:
            ids.append(profile.user_id)
    return ids


def decide(
    session: Session,
    *,
    assignment: MentorAssignment,
    actor: User,
    accept: bool,
    reason: str = "",
    ip: str = "",
) -> MentorAssignment:
    """Confirm or decline a mentor request. The mentor decides."""
    mentor = session.get(Mentor, assignment.mentor_id)
    if mentor is None:
        raise NotFound("That mentor does not exist.")
    if actor.role != "admin" and actor.id != mentor.user_id:
        raise PermissionDenied(
            "Only the requested mentor can answer this request.",
            target_type="mentor_assignment",
            target_id=assignment.id,
            reason="not_the_mentor",
        )
    if assignment.state != "proposed":
        raise Conflict(
            f"This request is already {assignment.state}. Reload the page to see the current state."
        )
    project = session.get(Project, assignment.project_id)
    if project is None:
        raise NotFound("That project does not exist.")

    if not accept:
        assignment.state = "declined"
        assignment.reason = (reason or "Declined by the mentor.")[:300]
        assignment.ended_at = utcnow()
        audit_log.record(
            session,
            actor=actor,
            action="mentor.declined",
            target_type="mentor_assignment",
            target_id=assignment.id,
            reason=assignment.reason[:200],
            ip=ip,
        )
        notifications.notify(
            session,
            user_id=project.owner_id,
            kind="mentor_declined",
            title="A mentor declined the request",
            body=f"{project.title}: {actor.display_name} declined. Try another mentor from the fit list.",
            href=f"/industry/projects/{project.id}/mentor",
        )
        return assignment

    window = _unavailability(session, mentor.id, project.start_date, project.deadline)
    if window is not None:
        raise Conflict(
            "You are recorded as unavailable for this project window. Remove the unavailability "
            "window or ask the partner to choose another mentor."
        )
    workload = mentor_profiles.workload(session, mentor)
    if workload.at_capacity:
        raise Conflict(
            "Confirming this project would take you beyond your stated capacity. Raise the "
            "capacity in the expertise registry or decline the request."
        )
    confirmed = session.scalar(
        select(MentorAssignment).where(
            MentorAssignment.project_id == project.id,
            MentorAssignment.state == "confirmed",
        )
    )
    if confirmed is not None:
        raise Conflict("This challenge already has a confirmed mentor.")

    assignment.state = "confirmed"
    assignment.confirmed_at = utcnow()
    assignment.reason = (reason or "Confirmed by the mentor.")[:300]
    try:
        session.flush()
    except IntegrityError as exc:
        # The partial unique index refused a second confirmed mentor: exactly one
        # reviewer wins the contended slot and the other is told why.
        session.rollback()
        raise Conflict(
            "Another mentor was confirmed for this challenge a moment ago. Only one mentor can "
            "be confirmed per project."
        ) from exc

    others = session.scalars(
        select(MentorAssignment).where(
            MentorAssignment.project_id == project.id,
            MentorAssignment.state == "proposed",
            MentorAssignment.id != assignment.id,
        )
    ).all()
    for other in others:
        other.state = "declined"
        other.ended_at = utcnow()
        other.reason = "Another mentor was confirmed for this challenge."
        other_mentor = session.get(Mentor, other.mentor_id)
        if other_mentor is not None:
            notifications.notify(
                session,
                user_id=other_mentor.user_id,
                kind="mentor_request_closed",
                title="A mentor request was closed",
                body=f"{project.title}: another mentor was confirmed, so this request was withdrawn.",
                href="/mentor/requests",
            )

    audit_log.record(
        session,
        actor=actor,
        action="mentor.confirmed",
        target_type="mentor_assignment",
        target_id=assignment.id,
        reason=assignment.reason[:200],
        detail={"project_id": project.id, "declined_others": len(others)},
        ip=ip,
    )
    notifications.notify_many(
        session,
        user_ids=[project.owner_id, *_project_team_user_ids(session, project.id)],
        kind="mentor_confirmed",
        title="A mentor is confirmed for the project",
        body=f"{project.title}: the mentor is confirmed. Review the fit notes before work starts.",
        href=f"/industry/projects/{project.id}/mentor",
    )
    return assignment


def confirmed_for_project(session: Session, project_id: int) -> MentorAssignment | None:
    return session.scalar(
        select(MentorAssignment).where(
            MentorAssignment.project_id == project_id,
            MentorAssignment.state == "confirmed",
        )
    )

