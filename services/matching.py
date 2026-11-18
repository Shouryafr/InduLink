"""Load candidates, score them with ``core.matching`` and record the history.

All scoring lives in ``core.matching``; this module only does the database work
and the audit trail. One query per table (students, declared skills, evidence)
keeps the candidate list free of N+1 queries, which a test asserts.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import evidence_policy
from core import matching as rules
from data.models import (
    Evidence,
    Project,
    ProjectVersion,
    RecommendationSnapshot,
    Requirement,
    StudentProfile,
    StudentSkill,
    User,
    utcnow,
)
from services import projects as project_service
from services.errors import Conflict, NotFound, PermissionDenied

#: Roles and the profile visibilities they may see in a candidate list.
VISIBLE_TO = {
    "admin": {"private", "university", "public"},
    "industry": {"public"},
    "mentor": {"university", "public"},
}


def project_view(project: Project) -> rules.ProjectView:
    return rules.ProjectView(
        project_id=project.id,
        title=project.title,
        start_date=project.start_date,
        deadline=project.deadline,
        timezone=project.timezone,
        team_size_min=project.team_size_min,
        team_size_max=project.team_size_max,
    )


def requirements_for_version(
    session: Session, version: ProjectVersion
) -> list[rules.RequirementView]:
    rows = project_service.version_requirements(session, version.id)
    inputs = project_service.to_inputs(session, rows)
    return [
        rules.RequirementView(
            skill_id=item.skill_id,
            skill_name=item.skill_name,
            required_level=item.required_level,
            weight=item.weight,
            is_mandatory=item.is_mandatory,
        )
        for item in inputs
    ]


def requirements_for(
    session: Session, project: Project
) -> tuple[ProjectVersion | None, list[rules.RequirementView]]:
    version = project_service.current_version(session, project.id)
    if version is None:
        return None, []
    return version, requirements_for_version(session, version)


def candidate_pool(
    session: Session,
    *,
    visibilities: set[str] | None = None,
    profile_ids: list[int] | None = None,
) -> list[rules.Candidate]:
    """Build every candidate in three queries, regardless of cohort size."""
    student_query = (
        select(StudentProfile, User)
        .join(User, User.id == StudentProfile.user_id)
        .where(User.status == "active", User.role == "student")
        .order_by(StudentProfile.id)
    )
    if visibilities is not None:
        student_query = student_query.where(StudentProfile.visibility.in_(sorted(visibilities)))
    if profile_ids is not None:
        student_query = student_query.where(StudentProfile.id.in_(profile_ids))
    pairs = session.execute(student_query).all()
    if not pairs:
        return []

    ids = [profile.id for profile, _user in pairs]
    declared = {
        (row.student_id, row.skill_id): row.declared_level
        for row in session.scalars(
            select(StudentSkill).where(StudentSkill.student_id.in_(ids))
        ).all()
    }
    evidence_rows = list(
        session.scalars(select(Evidence).where(Evidence.student_id.in_(ids))).all()
    )

    claims: dict[tuple[int, int], list[evidence_policy.EvidenceClaim]] = {}
    counts: dict[int, int] = {}
    for row in evidence_rows:
        claims.setdefault((row.student_id, row.skill_id), []).append(
            evidence_policy.EvidenceClaim(
                evidence_id=row.id,
                category=row.category,
                status=row.status,
                demonstrated_level=row.demonstrated_level,
                evidence_date=row.evidence_date,
                expiry_date=row.expiry_date,
                reviewer_id=row.reviewer_id,
                source=row.source,
            )
        )
        counts[row.student_id] = counts.get(row.student_id, 0) + 1

    candidates: list[rules.Candidate] = []
    pairs_by_student: dict[int, list[int]] = {}
    for (student_id, skill_id) in set(declared) | set(claims):
        pairs_by_student.setdefault(student_id, []).append(skill_id)

    for profile, user in pairs:
        skills: dict[int, rules.CandidateSkill] = {}
        for skill_id in sorted(pairs_by_student.get(profile.id, [])):
            assessment = evidence_policy.evaluate_skill(
                declared.get((profile.id, skill_id)),
                claims.get((profile.id, skill_id), []),
            )
            skills[skill_id] = rules.CandidateSkill(
                skill_id=skill_id,
                declared_level=assessment.declared_level,
                confirmed_level=assessment.confirmed_level,
                provisional_level=assessment.provisional_level,
                label=assessment.label,
            )
        candidates.append(
            rules.Candidate(
                student_id=profile.id,
                display_name=user.display_name or user.email,
                skills=skills,
                availability_hours=profile.availability_hours,
                availability_from=profile.availability_from,
                availability_to=profile.availability_to,
                availability_timezone=profile.availability_timezone,
                visibility=profile.visibility,
                headline=profile.headline,
                evidence_count=counts.get(profile.id, 0),
            )
        )
    return candidates


def rank_project(
    session: Session,
    project: Project,
    *,
    viewer_role: str = "industry",
    today: date | None = None,
    candidates: list[rules.Candidate] | None = None,
) -> list[rules.MatchResult]:
    """Rank the candidates this viewer is allowed to see."""
    _version, requirements = requirements_for(session, project)
    if not requirements:
        raise Conflict("This challenge has no requirements to match against.")
    if candidates is None:
        candidates = candidate_pool(
            session, visibilities=VISIBLE_TO.get(viewer_role, {"public"})
        )
    return rules.rank_candidates(project_view(project), requirements, candidates, today=today)


def result_for_student(
    session: Session, project: Project, profile_id: int
) -> rules.MatchResult | None:
    _version, requirements = requirements_for(session, project)
    if not requirements:
        return None
    candidates = candidate_pool(session, profile_ids=[profile_id])
    if not candidates:
        return None
    return rules.evaluate_candidate(project_view(project), requirements, candidates[0])


def require_partner_access(user: User, project: Project) -> None:
    if user.role == "admin":
        return
    if user.role != "industry" or project.owner_id != user.id:
        raise PermissionDenied(
            "Only the owning partner or an administrator can run matching for this challenge.",
            target_type="project",
            target_id=project.id,
            reason="matching_not_owner",
        )


def _inputs_hash(
    project: Project,
    version: ProjectVersion,
    requirements: list[Requirement],
    candidates: list[rules.Candidate],
) -> str:
    payload = {
        "project": [
            project.id,
            project.title,
            project.difficulty,
            project.team_size_min,
            project.team_size_max,
            project.start_date.isoformat(),
            project.deadline.isoformat(),
            project.timezone,
            project.updated_at.isoformat() if project.updated_at else "",
        ],
        "version": [version.id, version.version_no, version.snapshot],
        "requirements": [
            [row.skill_id, row.required_level, row.weight, row.is_mandatory]
            for row in requirements
        ],
        "candidates": [
            [
                candidate.student_id,
                sorted(
                    (skill_id, skill.confirmed_level, skill.provisional_level, skill.label)
                    for skill_id, skill in candidate.skills.items()
                ),
            ]
            for candidate in sorted(candidates, key=lambda c: c.student_id)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def latest_snapshot(session: Session, project_id: int, kind: str = "candidates") -> RecommendationSnapshot | None:
    return session.scalar(
        select(RecommendationSnapshot)
        .where(
            RecommendationSnapshot.project_id == project_id,
            RecommendationSnapshot.kind == kind,
        )
        .order_by(RecommendationSnapshot.generated_at.desc(), RecommendationSnapshot.id.desc())
        .limit(1)
    )


def snapshot_output(results: list[rules.MatchResult], *, limit: int = 12) -> dict:
    return {
        "generated_for": "candidate ranking",
        "candidates": [
            {
                "student_id": result.student_id,
                "name": result.display_name,
                "rank": result.rank,
                "score": result.ranking_score,
                "confirmed_score": result.confirmed_score,
                "provisional_score": result.provisional_score,
                "eligibility": result.eligibility,
                "tied": result.tied,
            }
            for result in results[:limit]
        ],
        "count": len(results),
    }


def refresh_snapshots(
    session: Session,
    *,
    project_ids: list[int] | None = None,
    limit: int = 6,
    viewer_role: str = "industry",
) -> int:
    """Record what was recommended, and from which inputs.

    A snapshot is only written when the inputs changed, so re-running the seed
    or reloading a screen does not fill the table with duplicates.
    """
    query = select(Project).where(Project.state == "open").order_by(Project.id)
    if project_ids:
        query = select(Project).where(Project.id.in_(project_ids)).order_by(Project.id)
    written = 0
    for project in session.scalars(query.limit(limit)).all():
        try:
            version, requirements = requirements_for(session, project)
            if version is None or not requirements:
                continue
            candidates = candidate_pool(session, visibilities=VISIBLE_TO[viewer_role])
            results = rules.rank_candidates(project_view(project), requirements, candidates)
            rows = project_service.version_requirements(session, version.id)
            digest = _inputs_hash(project, version, rows, candidates)
            existing = latest_snapshot(session, project.id)
            if existing is not None and existing.inputs_hash == digest:
                continue
            session.add(
                RecommendationSnapshot(
                    project_id=project.id,
                    kind="candidates",
                    inputs_hash=digest,
                    output=snapshot_output(results),
                    version_id=version.id,
                    generated_at=utcnow(),
                )
            )
            written += 1
        except (Conflict, NotFound):
            continue
    return written


def record_snapshot(
    session: Session,
    project: Project,
    version: ProjectVersion,
    *,
    viewer_role: str = "industry",
    generated_at=None,
) -> RecommendationSnapshot | None:
    """Record a ranking for one version, including a superseded one."""
    requirements = requirements_for_version(session, version)
    if not requirements:
        return None
    candidates = candidate_pool(session, visibilities=VISIBLE_TO[viewer_role])
    results = rules.rank_candidates(project_view(project), requirements, candidates)
    rows = project_service.version_requirements(session, version.id)
    snapshot = RecommendationSnapshot(
        project_id=project.id,
        kind="candidates",
        inputs_hash=_inputs_hash(project, version, rows, candidates),
        output={
            **snapshot_output(results),
            "version_no": version.version_no,
            "note": (
                "Ranking recorded against this requirement version. The project's "
                "requirements were edited afterwards; this history is kept."
                if not version.is_current
                else "Ranking recorded against the current requirement version."
            ),
        },
        version_id=version.id,
        generated_at=generated_at or utcnow(),
    )
    session.add(snapshot)
    return snapshot


def get_snapshot(session: Session, project_id: int) -> RecommendationSnapshot | None:
    return latest_snapshot(session, project_id)
