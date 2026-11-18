"""Industry problem statement portal: creation, versioning and visibility.

Requirement edits never overwrite history. Editing writes a new
``project_versions`` row with its own requirement set and marks it current, so
an earlier version stays readable and can be diffed against the new one
(brief sections 7.1, 9.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from core import requirements as req_rules
from data.models import (
    Project,
    ProjectVersion,
    Requirement,
    SubmissionToken,
    User,
    utcnow,
)
from services import audit_log
from services.errors import Conflict, NotFound, PermissionDenied, ValidationError

DATE_FORMAT = "%Y-%m-%d"


@dataclass
class RequirementInput:
    skill_id: int
    skill_name: str
    required_level: int
    weight: float
    is_mandatory: bool


def get_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise NotFound("That project does not exist, or you cannot see it.")
    return project


def current_version(session: Session, project_id: int) -> ProjectVersion | None:
    return session.scalar(
        select(ProjectVersion)
        .where(ProjectVersion.project_id == project_id, ProjectVersion.is_current.is_(True))
        .options(selectinload(ProjectVersion.requirements))
    )


def version_requirements(session: Session, version_id: int) -> list[Requirement]:
    return list(
        session.scalars(
            select(Requirement)
            .where(Requirement.version_id == version_id)
            .order_by(Requirement.is_mandatory.desc(), Requirement.weight.desc())
        ).all()
    )


def to_inputs(session: Session, rows: list[Requirement]) -> list[RequirementInput]:
    from data.models import Skill

    skill_ids = [row.skill_id for row in rows]
    names = (
        {s.id: s.canonical_name for s in session.scalars(select(Skill).where(Skill.id.in_(skill_ids))).all()}
        if skill_ids
        else {}
    )
    return [
        RequirementInput(
            skill_id=row.skill_id,
            skill_name=names.get(row.skill_id, f"Skill {row.skill_id}"),
            required_level=row.required_level,
            weight=row.weight,
            is_mandatory=row.is_mandatory,
        )
        for row in rows
    ]


# --- Visibility -----------------------------------------------------------


def can_view(viewer: User | None, project: Project) -> bool:
    if viewer is None:
        return False
    if viewer.role == "admin" or viewer.id == project.owner_id:
        return True
    state = req_rules.ProjectState(project.state)
    if state not in req_rules.STUDENT_VISIBLE_STATES:
        return False
    if project.visibility == "public":
        return True
    if project.visibility == "university":
        return viewer.role in {"student", "mentor", "industry"}
    return False


def require_view(viewer: User | None, project: Project) -> None:
    """Raise unless the viewer may open this project. Audited by the 403 handler."""
    if can_view(viewer, project):
        return
    raise PermissionDenied(
        "This project is private, or your account does not have access to it.",
        target_type="project",
        target_id=project.id,
        reason="project_visibility",
    )


def visible_projects(session: Session, viewer: User | None, *, limit: int = 100) -> list[Project]:
    query = select(Project).order_by(Project.deadline.asc()).limit(limit)
    if viewer is None:
        return []
    if viewer.role == "admin":
        return list(session.scalars(query).all())
    visible_states = [state.value for state in req_rules.STUDENT_VISIBLE_STATES]
    visibility = ["public", "university"] if viewer.role in {"student", "mentor"} else ["public"]
    rows = list(session.scalars(query).all())
    return [
        project
        for project in rows
        if project.owner_id == viewer.id
        or (project.state in visible_states and project.visibility in visibility)
    ]


# --- Creation -------------------------------------------------------------


def _parse_date(value, field: str, errors: dict[str, list[str]]) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        errors.setdefault(field, []).append("Enter a date.")
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        errors.setdefault(field, []).append("Use the date format YYYY-MM-DD.")
        return None


def _consume_token(
    session: Session, *, token: str | None, actor: User, purpose: str
) -> Project | None:
    """Return the project a repeated submission already created, if any."""
    if not token:
        return None
    existing = session.scalar(select(SubmissionToken).where(SubmissionToken.token == token))
    if existing is None:
        return None
    if existing.user_id != actor.id or existing.purpose != purpose:
        raise Conflict("This form submission token belongs to a different request.")
    if existing.result_type == "project" and existing.result_id:
        return session.get(Project, int(existing.result_id))
    return None


def create_project(
    session: Session,
    *,
    owner: User,
    title: str,
    domain: str,
    description: str,
    difficulty: int,
    team_size_min: int,
    team_size_max: int,
    start_date,
    deadline,
    visibility: str,
    requirement_drafts: list[req_rules.RequirementDraft],
    actor: User,
    eligibility_note: str = "",
    evaluation_criteria: str = "",
    deliverables_summary: str = "",
    form_token: str | None = None,
    ip: str = "",
) -> Project:
    if owner.role not in {"industry", "admin"}:
        raise PermissionDenied("Only an industry partner or administrator can publish a challenge.")
    if actor.id != owner.id and actor.role != "admin":
        raise PermissionDenied("You can only create challenges for your own organisation.")

    existing = _consume_token(session, token=form_token, actor=actor, purpose="project_create")
    if existing is not None:
        return existing

    errors: dict[str, list[str]] = {}
    cleaned_title = (title or "").strip()
    if not cleaned_title:
        errors["title"] = ["Give the challenge a title."]
    elif len(cleaned_title) > 200:
        errors["title"] = ["Keep the title under 200 characters."]
    if str(visibility) not in req_rules.VISIBILITIES:
        errors["visibility"] = ["Choose who can see this challenge."]
    try:
        difficulty_int = int(difficulty)
    except (TypeError, ValueError):
        difficulty_int = 0
    if not 1 <= difficulty_int <= 5:
        errors["difficulty"] = ["Choose a difficulty from 1 to 5."]

    try:
        min_int = int(team_size_min)
        max_int = int(team_size_max)
    except (TypeError, ValueError):
        min_int = max_int = 0
    if min_int < 1:
        errors["team_size_min"] = ["A team needs at least one member."]
    if max_int < min_int:
        errors["team_size_max"] = ["The maximum team size cannot be below the minimum."]

    parsed_start = _parse_date(start_date, "start_date", errors)
    parsed_deadline = _parse_date(deadline, "deadline", errors)
    today = date.today()
    if parsed_deadline is not None and parsed_deadline <= today:
        errors["deadline"] = ["The deadline must be in the future."]
    if parsed_start is not None and parsed_deadline is not None and parsed_deadline < parsed_start:
        errors["deadline"] = ["The deadline cannot be before the start date."]

    weights, weight_errors = req_rules.validate_requirement_set(requirement_drafts, today=today)
    for index, messages in weight_errors.items():
        key = f"requirement_{index}"
        errors.setdefault(key, []).extend(messages)

    if errors:
        raise ValidationError("This challenge cannot be saved yet.", field_errors=errors)

    project = Project(
        owner_id=owner.id,
        title=cleaned_title,
        domain=(domain or "").strip()[:60],
        description=(description or "").strip(),
        difficulty=difficulty_int,
        team_size_min=min_int,
        team_size_max=max_int,
        start_date=parsed_start,
        deadline=parsed_deadline,
        state=req_rules.ProjectState.DRAFT.value,
        visibility=str(visibility),
        eligibility_note=(eligibility_note or "").strip(),
        evaluation_criteria=(evaluation_criteria or "").strip(),
        deliverables_summary=(deliverables_summary or "").strip(),
    )
    session.add(project)
    session.flush()

    version = _write_version(
        session,
        project=project,
        requirement_drafts=requirement_drafts,
        weights=weights,
        version_no=1,
        reason="Initial challenge submission",
        actor=actor,
    )

    if form_token:
        session.add(
            SubmissionToken(
                token=form_token[:64],
                user_id=actor.id,
                purpose="project_create",
                result_type="project",
                result_id=str(project.id),
            )
        )

    audit_log.record(
        session,
        actor=actor,
        action="project.created",
        target_type="project",
        target_id=project.id,
        after_ref=f"v{version.version_no}",
        detail={"title": cleaned_title, "requirements": len(requirement_drafts)},
        ip=ip,
    )
    return project


def _write_version(
    session: Session,
    *,
    project: Project,
    requirement_drafts: list[req_rules.RequirementDraft],
    weights: list[float],
    version_no: int,
    reason: str,
    actor: User,
) -> ProjectVersion:
    rows = [
        Requirement(
            skill_id=draft.skill_id,
            required_level=draft.required_level,
            weight=weights[index],
            is_mandatory=bool(draft.is_mandatory),
        )
        for index, draft in enumerate(requirement_drafts)
    ]
    version = ProjectVersion(
        project_id=project.id,
        version_no=version_no,
        created_by=actor.id,
        reason=reason[:300],
        is_current=True,
        snapshot={
            "title": project.title,
            "domain": project.domain,
            "description": project.description,
            "difficulty": project.difficulty,
            "team_size_min": project.team_size_min,
            "team_size_max": project.team_size_max,
            "start_date": project.start_date.isoformat(),
            "deadline": project.deadline.isoformat(),
            "visibility": project.visibility,
            "requirements": [
                {
                    "skill_id": draft.skill_id,
                    "required_level": draft.required_level,
                    "weight": weights[index],
                    "is_mandatory": bool(draft.is_mandatory),
                }
                for index, draft in enumerate(requirement_drafts)
            ],
        },
    )
    # Exactly one current version per project, enforced by a partial unique index,
    # so the previous current version is cleared and flushed before the
    # replacement row is inserted.
    previous = session.scalars(
        select(ProjectVersion).where(
            ProjectVersion.project_id == project.id,
            ProjectVersion.is_current.is_(True),
        )
    ).all()
    for row in previous:
        row.is_current = False
    if previous:
        session.flush()

    session.add(version)
    session.flush()
    for row in rows:
        row.version_id = version.id
        session.add(row)
    return version


def edit_requirements(
    session: Session,
    *,
    project: Project,
    actor: User,
    requirement_drafts: list[req_rules.RequirementDraft],
    reason: str,
    ip: str = "",
) -> ProjectVersion:
    if actor.id != project.owner_id and actor.role != "admin":
        raise PermissionDenied("Only the owning organisation or an administrator can edit requirements.")
    if req_rules.ProjectState(project.state) not in req_rules.EDITABLE_STATES:
        raise Conflict(
            "This project is no longer editable. Requirements can only change while it is "
            "in draft, awaiting approval, approved or open."
        )
    if not (reason or "").strip():
        raise ValidationError(
            "Record why the requirements changed.",
            field_errors={"reason": ["Record why the requirements changed."]},
        )

    weights, weight_errors = req_rules.validate_requirement_set(requirement_drafts)
    if weight_errors:
        raise ValidationError(
            "These requirements cannot be saved.",
            field_errors={f"requirement_{k}": v for k, v in weight_errors.items()},
        )

    previous = current_version(session, project.id)
    previous_no = previous.version_no if previous else 0
    version = _write_version(
        session,
        project=project,
        requirement_drafts=requirement_drafts,
        weights=weights,
        version_no=previous_no + 1,
        reason=reason,
        actor=actor,
    )
    audit_log.record(
        session,
        actor=actor,
        action="project.requirements_changed",
        target_type="project",
        target_id=project.id,
        reason=reason.strip()[:200],
        before_ref=f"v{previous_no}" if previous_no else "",
        after_ref=f"v{version.version_no}",
        detail={"requirements": len(requirement_drafts)},
        ip=ip,
    )
    return version


def transition(
    session: Session,
    *,
    project: Project,
    target: str,
    actor: User,
    reason: str = "",
    ip: str = "",
) -> Project:
    """Move a project through its state machine, auditing the change."""
    current = req_rules.ProjectState(project.state)
    try:
        target_state = req_rules.ensure_transition(current, target)
    except req_rules.TransitionError as exc:
        # A refused transition is a conflict with the project's current state,
        # not a server fault: the page must explain it instead of returning 500.
        allowed = req_rules.allowed_next_states(current)
        options = ", ".join(state.value.replace("_", " ") for state in allowed) or "none"
        raise Conflict(
            f"{exc} This challenge is '{current.value.replace('_', ' ')}', so the next "
            f"state can only be: {options}.",
            target_type="project",
            target_id=project.id,
        ) from exc

    is_admin = actor.role == "admin"
    is_owner = actor.id == project.owner_id
    if not (is_admin or is_owner):
        raise PermissionDenied("You cannot change this project's state.")

    # Approval and reopening are administrator decisions.
    if target_state in {req_rules.ProjectState.APPROVED} and not is_admin:
        raise PermissionDenied("Only an administrator can approve a challenge.")
    if target_state in {req_rules.ProjectState.CANCELLED} and not (is_admin or is_owner):
        raise PermissionDenied("You cannot cancel this project.")
    if target_state is req_rules.ProjectState.ACTIVE:
        version = current_version(session, project.id)
        if version is None or not version_requirements(session, version.id):
            raise ValidationError("A project cannot start without at least one requirement.")
    if target_state in {req_rules.ProjectState.CANCELLED, req_rules.ProjectState.COMPLETED}:
        if not (reason or "").strip():
            raise ValidationError(
                "Record a reason for this change.",
                field_errors={"reason": ["Record a reason for this change."]},
            )

    before = project.state
    project.state = target_state.value
    audit_log.record(
        session,
        actor=actor,
        action="project.state_changed",
        target_type="project",
        target_id=project.id,
        reason=(reason or "").strip()[:200],
        before_ref=before,
        after_ref=target_state.value,
        ip=ip,
    )
    return project


def version_diff(previous: ProjectVersion | None, current: ProjectVersion) -> dict:
    """Human-readable diff between two versions, for the review screen."""
    if previous is None:
        return {"added": [], "removed": [], "changed": [], "summary": "First version"}

    def keyed(snapshot: dict) -> dict[int, dict]:
        return {item["skill_id"]: item for item in snapshot.get("requirements", [])}

    before = keyed(previous.snapshot or {})
    after = keyed(current.snapshot or {})

    added = [after[sid] for sid in after if sid not in before]
    removed = [before[sid] for sid in before if sid not in after]
    changed = [
        {"before": before[sid], "after": after[sid]}
        for sid in after
        if sid in before
        and (before[sid]["required_level"] != after[sid]["required_level"]
            or before[sid]["is_mandatory"] != after[sid]["is_mandatory"]
            or before[sid]["weight"] != after[sid]["weight"])
    ]
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "summary": f"v{previous.version_no} to v{current.version_no}",
    }


def snapshot_stamp(version: ProjectVersion | None, *, now=None) -> dict:
    from core import freshness

    return freshness.stamp(
        version.created_at if version else None,
        now=now,
        threshold_days=90,
        subject="Requirements",
    )


def set_stale_flag(project: Project) -> None:
    """Touch the project so downstream snapshots can detect a change."""
    project.updated_at = utcnow()
