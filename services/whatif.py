"""What-If sandbox: change the inputs in memory, keep the data immutable.

The scenario is recomputed from the same candidate pool, with the edited
requirement set applied to a copy. Nothing in the project, skill or evidence
tables is written; the checksum before and after the run is compared and the
result is refused if they differ. Only the scenario record itself is stored, so a
reviewer can re-read what was simulated.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import matching as rules
from data.models import (
    Evidence,
    MentorAssignment,
    Project,
    ProjectVersion,
    Requirement,
    Scenario,
    StudentProfile,
    StudentSkill,
    Team,
    TeamMember,
    User,
)
from services import audit_log, matching
from services.errors import Conflict, NotFound, PermissionDenied, ValidationError

TOP_N = 8
MAX_LEVEL = 5


@dataclass
class ScenarioOutcome:
    scenario: Scenario
    before: list[dict]
    after: list[dict]
    entrants: list[str]
    leavers: list[str]
    moved: list[str]
    changes: list[str]
    checksum_before: str
    checksum_after: str
    unchanged: bool


def _rows(session: Session, stmt) -> list[list]:
    return [list(row) for row in session.execute(stmt).all()]


def domain_checksum(session: Session, project_id: int) -> str:
    """A digest of every domain row a What-If must not touch."""
    project = session.get(Project, project_id)
    if project is None:
        return ""
    versions = _rows(
        session,
        select(
            ProjectVersion.id,
            ProjectVersion.version_no,
            ProjectVersion.is_current,
            ProjectVersion.snapshot,
        )
        .where(ProjectVersion.project_id == project_id)
        .order_by(ProjectVersion.id),
    )
    requirements = _rows(
        session,
        select(
            Requirement.id,
            Requirement.version_id,
            Requirement.skill_id,
            Requirement.required_level,
            Requirement.weight,
            Requirement.is_mandatory,
        )
        .join(ProjectVersion, ProjectVersion.id == Requirement.version_id)
        .where(ProjectVersion.project_id == project_id)
        .order_by(Requirement.id),
    )
    teams = _rows(
        session,
        select(Team.id, Team.state, Team.label).where(Team.project_id == project_id).order_by(Team.id),
    )
    members = _rows(
        session,
        select(TeamMember.team_id, TeamMember.student_id, TeamMember.role)
        .join(Team, Team.id == TeamMember.team_id)
        .where(Team.project_id == project_id)
        .order_by(TeamMember.id),
    )
    assignments = _rows(
        session,
        select(
            MentorAssignment.id,
            MentorAssignment.mentor_id,
            MentorAssignment.state,
            MentorAssignment.confirmed_at,
        )
        .where(MentorAssignment.project_id == project_id)
        .order_by(MentorAssignment.id),
    )
    students = _rows(
        session,
        select(
            StudentProfile.id,
            StudentProfile.visibility,
            StudentProfile.availability_hours,
            StudentProfile.availability_from,
            StudentProfile.availability_to,
            StudentProfile.updated_at,
        ).order_by(StudentProfile.id),
    )
    declared = _rows(
        session,
        select(
            StudentSkill.student_id,
            StudentSkill.skill_id,
            StudentSkill.declared_level,
            StudentSkill.updated_at,
        ).order_by(StudentSkill.id),
    )
    evidence = _rows(
        session,
        select(
            Evidence.id,
            Evidence.student_id,
            Evidence.skill_id,
            Evidence.status,
            Evidence.demonstrated_level,
            Evidence.updated_at,
        ).order_by(Evidence.id),
    )
    payload = {
        "project": [
            project.id,
            project.title,
            project.state,
            project.team_size_min,
            project.team_size_max,
            project.start_date.isoformat(),
            project.deadline.isoformat(),
            project.updated_at.isoformat() if project.updated_at else "",
        ],
        "versions": [[row[0], row[1], row[2], row[3]] for row in versions],
        "requirements": requirements,
        "teams": teams,
        "members": members,
        "assignments": assignments,
        "students": [[str(value) if value is not None else None for value in row] for row in students],
        "declared": declared,
        "evidence": [[str(value) if value is not None else None for value in row] for row in evidence],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _guard(user: User, project: Project) -> None:
    if user.role == "admin" or (user.role == "industry" and project.owner_id == user.id):
        return
    raise PermissionDenied(
        "Only the owning partner or an administrator can run a What-If.",
        target_type="project",
        target_id=project.id,
        reason="whatif_not_owner",
    )


def _parse(changes: dict) -> dict:
    parsed = {
        "skill_id": None,
        "new_level": None,
        "mandatory": "keep",
        "team_size_max": None,
        "exclude_student_id": None,
    }
    raw_skill = str(changes.get("skill_id", "")).strip()
    if raw_skill:
        try:
            parsed["skill_id"] = int(raw_skill)
        except ValueError:
            raise ValidationError("Choose a requirement from this challenge.")
    raw_level = str(changes.get("new_level", "")).strip()
    if raw_level:
        try:
            parsed["new_level"] = int(raw_level)
        except ValueError:
            raise ValidationError("The simulated level must be a whole number from 1 to 5.")
        if not 1 <= parsed["new_level"] <= MAX_LEVEL:
            raise ValidationError("The simulated level must be from 1 to 5.")
    mandatory = str(changes.get("mandatory", "keep")).strip().lower()
    if mandatory not in {"keep", "yes", "no"}:
        raise ValidationError("Choose whether the requirement stays mandatory.")
    parsed["mandatory"] = mandatory
    raw_max = str(changes.get("team_size_max", "")).strip()
    if raw_max:
        try:
            parsed["team_size_max"] = int(raw_max)
        except ValueError:
            raise ValidationError("The simulated team size must be a whole number.")
    raw_exclude = str(changes.get("exclude_student_id", "")).strip()
    if raw_exclude:
        try:
            parsed["exclude_student_id"] = int(raw_exclude)
        except ValueError:
            raise ValidationError("Choose a candidate to exclude from the simulation.")
    return parsed


def run(
    session: Session,
    *,
    project: Project,
    actor: User,
    changes: dict,
    label: str = "",
    ip: str = "",
) -> ScenarioOutcome:
    _guard(actor, project)
    if project.state not in {"open", "approved"}:
        raise Conflict(
            "A What-If is only available while the challenge is approved or open for matching."
        )
    version, requirements = matching.requirements_for(session, project)
    if version is None or not requirements:
        raise Conflict("This challenge has no requirements to simulate.")
    parsed = _parse(changes)
    if (
        parsed["skill_id"] is not None
        and parsed["skill_id"] not in {row.skill_id for row in requirements}
    ):
        raise ValidationError("That skill is not a requirement of this challenge.")

    candidates = matching.candidate_pool(
        session, visibilities=matching.VISIBLE_TO.get(actor.role, {"public"})
    )
    if parsed["exclude_student_id"] is not None:
        candidates = [c for c in candidates if c.student_id != parsed["exclude_student_id"]]

    checksum_before = domain_checksum(session, project.id)
    before = rules.rank_candidates(matching.project_view(project), requirements, candidates)

    def _simulate(row: rules.RequirementView) -> rules.RequirementView:
        if parsed["skill_id"] is None or row.skill_id != parsed["skill_id"]:
            return row
        level = parsed["new_level"] if parsed["new_level"] is not None else row.required_level
        mandatory = row.is_mandatory
        if parsed["mandatory"] == "yes":
            mandatory = True
        elif parsed["mandatory"] == "no":
            mandatory = False
        return replace(row, required_level=level, is_mandatory=mandatory)

    simulated = [_simulate(row) for row in requirements]
    after = rules.rank_candidates(matching.project_view(project), simulated, candidates)

    checksum_after = domain_checksum(session, project.id)
    if checksum_before != checksum_after:
        raise Conflict(
            "The simulation changed stored data, so it was discarded. This is a defect; "
            "report it rather than trusting the result."
        )

    before_by_id = {result.student_id: result for result in before[:TOP_N]}
    after_by_id = {result.student_id: result for result in after[:TOP_N]}
    entrants = [
        after_by_id[student_id].display_name
        for student_id in after_by_id
        if student_id not in before_by_id
    ]
    leavers = [
        before_by_id[student_id].display_name
        for student_id in before_by_id
        if student_id not in after_by_id
    ]
    moved: list[str] = []
    for student_id in sorted(set(before_by_id) & set(after_by_id)):
        old_rank = before_by_id[student_id].rank
        new_rank = after_by_id[student_id].rank
        if old_rank != new_rank:
            moved.append(
                f"{after_by_id[student_id].display_name}: rank {old_rank} to {new_rank}"
            )

    change_text: list[str] = []
    if parsed["skill_id"] is not None:
        name = next(
            row.skill_name for row in requirements if row.skill_id == parsed["skill_id"]
        )
        if parsed["new_level"] is not None:
            change_text.append(f"Required level for {name} set to {parsed['new_level']}.")
        if parsed["mandatory"] != "keep":
            change_text.append(
                f"{name} is {'now' if parsed['mandatory'] == 'yes' else 'no longer'} mandatory."
            )
    if parsed["team_size_max"] is not None:
        if not 1 <= parsed["team_size_max"] <= 12:
            raise ValidationError("The simulated team size must be 1 to 12.")
        change_text.append(f"Team size maximum shown as {parsed['team_size_max']} (display only).")
    if parsed["exclude_student_id"] is not None:
        excluded = session.get(StudentProfile, parsed["exclude_student_id"])
        name = excluded.user.display_name if excluded and excluded.user else "one candidate"
        change_text.append(f"{name} excluded from the candidate pool.")
    if not change_text:
        change_text.append("No change applied; the simulation reproduces the current ranking.")

    results = {
        "before": [
            {
                "student_id": result.student_id,
                "name": result.display_name,
                "rank": result.rank,
                "score": result.ranking_score,
                "eligibility": result.eligibility,
            }
            for result in before[:TOP_N]
        ],
        "after": [
            {
                "student_id": result.student_id,
                "name": result.display_name,
                "rank": result.rank,
                "score": result.ranking_score,
                "eligibility": result.eligibility,
            }
            for result in after[:TOP_N]
        ],
        "entrants": entrants,
        "leavers": leavers,
        "moved": moved,
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "unchanged": checksum_before == checksum_after,
        "changes": change_text,
    }
    scenario = Scenario(
        project_id=project.id,
        base_snapshot_id=(
            matching.latest_snapshot(session, project.id).id
            if matching.latest_snapshot(session, project.id)
            else None
        ),
        created_by=actor.id,
        label=(label or change_text[0])[:200],
        changes=parsed,
        results=results,
    )
    session.add(scenario)
    session.flush()
    audit_log.record(
        session,
        actor=actor,
        action="scenario.run",
        target_type="scenario",
        target_id=scenario.id,
        reason=scenario.label,
        detail={
            "project_id": project.id,
            "unchanged": results["unchanged"],
            "checksum": checksum_after[:16],
        },
        ip=ip,
    )
    return ScenarioOutcome(
        scenario=scenario,
        before=results["before"],
        after=results["after"],
        entrants=entrants,
        leavers=leavers,
        moved=moved,
        changes=change_text,
        checksum_before=checksum_before,
        checksum_after=checksum_after,
        unchanged=results["unchanged"],
    )


def get(session: Session, scenario_id: int) -> Scenario:
    row = session.get(Scenario, scenario_id)
    if row is None:
        raise NotFound("That scenario does not exist.")
    return row


def recent(session: Session, project_id: int, *, limit: int = 10) -> list[Scenario]:
    return list(
        session.scalars(
            select(Scenario)
            .where(Scenario.project_id == project_id)
            .order_by(Scenario.created_at.desc())
            .limit(limit)
        ).all()
    )
