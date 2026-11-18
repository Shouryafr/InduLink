"""Team suggestion and confirmation.

A suggestion is explainable, greedy and deterministic. Confirmation is a hard
gate: team size, every member's eligibility and the one-confirmed-team-per-project
rule are all checked on the server, and the refusal says which constraint failed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from core import matching as rules
from data.models import Project, StudentProfile, Team, TeamMember, User, utcnow
from services import audit_log, matching, notifications
from services.errors import Conflict, NotFound, PermissionDenied, ValidationError

MAX_TEAM_SIZE = 12


def require_owner_or_admin(user: User, project: Project) -> None:
    if user.role == "admin" or (
        user.role == "industry" and project.owner_id == user.id
    ):
        return
    raise PermissionDenied(
        "Only the owning partner or an administrator can manage this team.",
        target_type="project",
        target_id=project.id,
        reason="team_not_owner",
    )


def suggest(
    session: Session,
    project: Project,
    *,
    viewer_role: str = "industry",
    exclude_student_ids: list[int] | None = None,
) -> tuple[rules.TeamSuggestion, list[rules.MatchResult]]:
    ranked = matching.rank_project(session, project, viewer_role=viewer_role)
    excluded = set(exclude_student_ids or [])
    if excluded:
        ranked = [result for result in ranked if result.student_id not in excluded]
    suggestion = rules.optimise_team(
        matching.project_view(project),
        matching.requirements_for(session, project)[1],
        ranked,
    )
    return suggestion, ranked


def teams_for(session: Session, project_id: int, *, limit: int = 50) -> list[Team]:
    return list(
        session.scalars(
            select(Team)
            .where(Team.project_id == project_id)
            .options(selectinload(Team.members))
            .order_by(Team.created_at.desc())
            .limit(limit)
        ).all()
    )


def confirmed_team(session: Session, project_id: int) -> Team | None:
    return session.scalar(
        select(Team)
        .where(Team.project_id == project_id, Team.state == "confirmed")
        .options(selectinload(Team.members))
    )


def member_names(session: Session, team: Team) -> list[str]:
    names: list[str] = []
    for member in team.members:
        profile = session.get(StudentProfile, member.student_id)
        if profile is not None and profile.user is not None:
            names.append(profile.user.display_name)
    return names


def confirm(
    session: Session,
    *,
    project: Project,
    actor: User,
    student_ids: list[int],
    label: str = "",
    rationale: str = "",
    ip: str = "",
) -> Team:
    """Confirm a team, refusing anything that breaks a hard constraint."""
    require_owner_or_admin(actor, project)
    if project.state not in {"open", "active", "approved"}:
        raise Conflict(
            "A team can only be confirmed while the challenge is approved, open or active."
        )
    if not student_ids:
        raise ValidationError(
            "Choose the students for this team.",
            field_errors={"members": ["Choose at least one student."]},
        )
    if len(student_ids) > MAX_TEAM_SIZE:
        raise ValidationError(
            f"A team cannot have more than {MAX_TEAM_SIZE} members in this prototype.",
            field_errors={"members": [f"Maximum {MAX_TEAM_SIZE} members."]},
        )
    if len(set(student_ids)) != len(student_ids):
        raise ValidationError(
            "The same student is listed twice.",
            field_errors={"members": ["Remove the duplicate."]},
        )
    existing = confirmed_team(session, project.id)
    if existing is not None:
        raise Conflict(
            f"This challenge already has a confirmed team ({existing.label or 'unnamed'}). "
            "Dissolve it before confirming another."
        )

    ranked = matching.rank_project(session, project, viewer_role=actor.role)
    by_id = {result.student_id: result for result in ranked}
    unknown = [student_id for student_id in student_ids if student_id not in by_id]
    if unknown:
        raise PermissionDenied(
            "One of those students is not in the candidate list you can see.",
            target_type="project",
            target_id=project.id,
            reason="student_not_visible",
        )
    members = [by_id[student_id] for student_id in student_ids]
    violations = rules.validate_team(
        minimum=project.team_size_min,
        maximum=project.team_size_max,
        members=members,
    )
    if violations:
        raise ValidationError(
            "This team breaks a hard constraint, so it cannot be confirmed.",
            field_errors={"members": violations},
        )

    team = Team(
        project_id=project.id,
        state="confirmed",
        label=(label or f"Team for {project.title}")[:120],
        rationale=(rationale or "Confirmed from the ranked candidate list.")[:4000],
        score_snapshot={
            "members": [
                {
                    "student_id": member.student_id,
                    "name": member.display_name,
                    "score": member.ranking_score,
                    "eligibility": member.eligibility,
                    "rank": member.rank,
                }
                for member in members
            ],
            "confirmed_at": utcnow().isoformat(),
        },
    )
    session.add(team)
    try:
        session.flush()
    except IntegrityError as exc:
        # The partial unique index refused a second confirmed team: exactly one
        # confirmation wins the race and the other is told why.
        session.rollback()
        raise Conflict(
            "Another confirmation for this challenge landed a moment ago. "
            "Reload the page to see the team that was confirmed."
        ) from exc
    for member in members:
        position_by_skill = max(
            member.rows, key=lambda row: row.points, default=None
        )
        session.add(
            TeamMember(
                team_id=team.id,
                student_id=member.student_id,
                role=(position_by_skill.skill_name.lower() if position_by_skill else "contributor"),
                assigned_hours=6,
            )
        )
    session.flush()

    audit_log.record(
        session,
        actor=actor,
        action="team.confirmed",
        target_type="team",
        target_id=team.id,
        reason=(rationale or "")[:200],
        detail={"project_id": project.id, "members": student_ids},
        ip=ip,
    )
    member_user_ids = []
    for member in members:
        profile = session.get(StudentProfile, member.student_id)
        if profile is not None:
            member_user_ids.append(profile.user_id)
    notifications.notify_many(
        session,
        user_ids=member_user_ids,
        kind="team",
        title="You have been confirmed on a project team",
        body=f"{project.title}: your place on the team is confirmed. Open the project to see the team.",
        href="/student/projects",
    )
    return team


def dissolve(session: Session, *, team: Team, actor: User, reason: str, ip: str = "") -> Team:
    project = session.get(Project, team.project_id)
    if project is None:
        raise NotFound("That project does not exist.")
    require_owner_or_admin(actor, project)
    if team.state != "confirmed":
        raise Conflict("Only a confirmed team can be dissolved.")
    if not (reason or "").strip():
        raise ValidationError(
            "Record why the team is being dissolved.",
            field_errors={"reason": ["Record a reason."]},
        )
    team.state = "dissolved"
    audit_log.record(
        session,
        actor=actor,
        action="team.dissolved",
        target_type="team",
        target_id=team.id,
        reason=reason.strip()[:200],
        before_ref="confirmed",
        after_ref="dissolved",
        ip=ip,
    )
    return team
