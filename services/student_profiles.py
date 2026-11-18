"""Student profile and skill intelligence (brief section 7.3).

Declared levels are stored in ``student_skills``; assessed levels are derived
from ``evidence``. They are never merged into one field, and the profile keeps
its own visibility setting (private by default).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import evidence_policy, freshness, skills as skill_rules
from data.models import (
    PROFILE_VISIBILITIES,
    Evidence,
    StudentProfile,
    StudentSkill,
    User,
    utcnow,
)
from services import audit_log
from services.errors import NotFound, PermissionDenied, ValidationError

MAX_WEEKLY_AVAILABILITY = 80


def get_or_create(session: Session, user: User) -> StudentProfile:
    profile = session.scalar(select(StudentProfile).where(StudentProfile.user_id == user.id))
    if profile is not None:
        return profile
    if user.role != "student":
        raise PermissionDenied("Only a student account has a student profile.")
    profile = StudentProfile(user_id=user.id, visibility="private")
    session.add(profile)
    session.flush()
    return profile


def get_by_id(session: Session, profile_id: int) -> StudentProfile:
    profile = session.get(StudentProfile, profile_id)
    if profile is None:
        raise NotFound("That student profile does not exist.")
    return profile


def can_view(viewer: User | None, profile: StudentProfile) -> bool:
    """Server-side visibility rule. Hiding a link is not access control."""
    if viewer is None:
        return False
    if viewer.role == "admin" or viewer.id == profile.user_id:
        return True
    if viewer.role == "mentor":
        return profile.visibility in {"university", "public"}
    if viewer.role == "industry":
        return profile.visibility == "public"
    # Another student: only an explicitly public profile.
    return profile.visibility == "public"


def require_view(viewer: User | None, profile: StudentProfile) -> None:
    """Raise unless the viewer may open this profile.

    The 403 is audited by the application's denial handler, which writes it in
    its own transaction, so the record survives the rolled-back request.
    """
    if can_view(viewer, profile):
        return
    raise PermissionDenied(
        "This student profile is private.",
        target_type="student_profile",
        target_id=profile.id,
        reason="profile_visibility",
    )


def update_profile(
    session: Session,
    *,
    profile: StudentProfile,
    actor: User,
    headline: str = "",
    interests: str = "",
    coursework: str = "",
    experience: str = "",
    preferred_roles: str = "",
    visibility: str = "private",
    availability_hours: int | None = None,
    availability_from: date | None = None,
    availability_to: date | None = None,
    ip: str = "",
) -> StudentProfile:
    if actor.id != profile.user_id and actor.role != "admin":
        raise PermissionDenied("You can only edit your own profile.")

    errors: dict[str, list[str]] = {}
    if visibility not in PROFILE_VISIBILITIES:
        errors["visibility"] = ["Choose who can see your profile."]
    if availability_hours is not None:
        if availability_hours < 0 or availability_hours > MAX_WEEKLY_AVAILABILITY:
            errors["availability_hours"] = [
                f"Enter weekly hours between 0 and {MAX_WEEKLY_AVAILABILITY}."
            ]
    if availability_from and availability_to and availability_to < availability_from:
        errors["availability_to"] = ["The end of the window must be after the start."]
    if len(headline) > 160:
        errors["headline"] = ["Keep the headline under 160 characters."]
    if errors:
        raise ValidationError("Some profile details need attention.", field_errors=errors)

    availability_changed = (
        availability_hours != profile.availability_hours
        or availability_from != profile.availability_from
        or availability_to != profile.availability_to
    )

    before = {
        "visibility": profile.visibility,
        "availability_hours": profile.availability_hours,
    }
    profile.headline = (headline or "").strip()
    profile.interests = (interests or "").strip()
    profile.coursework = (coursework or "").strip()
    profile.experience = (experience or "").strip()
    profile.preferred_roles = (preferred_roles or "").strip()
    profile.visibility = visibility
    profile.availability_hours = availability_hours
    profile.availability_from = availability_from
    profile.availability_to = availability_to
    if availability_changed:
        profile.availability_updated_at = utcnow()

    audit_log.record(
        session,
        actor=actor,
        action="student_profile.updated",
        target_type="student_profile",
        target_id=profile.id,
        detail={"before": before},
        after_ref=visibility,
        ip=ip,
    )
    return profile


def set_declared_level(
    session: Session,
    *,
    profile: StudentProfile,
    skill_id: int,
    level: int | None,
    actor: User,
    ip: str = "",
) -> StudentSkill | None:
    """Set or clear a declared level. Assessed level is untouched."""
    if actor.id != profile.user_id and actor.role != "admin":
        raise PermissionDenied("You can only edit your own skills.")
    row = session.scalar(
        select(StudentSkill).where(
            StudentSkill.student_id == profile.id, StudentSkill.skill_id == skill_id
        )
    )

    if level is None:
        if row is not None:
            session.delete(row)
            audit_log.record(
                session,
                actor=actor,
                action="student_skill.removed",
                target_type="student_skill",
                target_id=skill_id,
                ip=ip,
            )
        return None

    if not skill_rules.is_valid_level(level):
        raise ValidationError(
            "Choose a level from 1 to 5.",
            field_errors={"declared_level": ["Choose a level from 1 to 5."]},
        )

    if row is None:
        row = StudentSkill(student_id=profile.id, skill_id=skill_id, declared_level=level)
        session.add(row)
    else:
        row.declared_level = level

    audit_log.record(
        session,
        actor=actor,
        action="student_skill.declared",
        target_type="student_skill",
        target_id=f"{profile.id}:{skill_id}",
        after_ref=str(level),
        ip=ip,
    )
    return row


@dataclass
class SkillView:
    skill_id: int
    skill_name: str
    category: str
    declared_level: int | None
    assessment: evidence_policy.SkillAssessment
    evidence_count: int
    latest_evidence_date: date | None


def declared_skills(session: Session, profile: StudentProfile) -> list[StudentSkill]:
    return list(
        session.scalars(
            select(StudentSkill)
            .where(StudentSkill.student_id == profile.id)
            .order_by(StudentSkill.skill_id)
        ).all()
    )


def evidence_claims(session: Session, profile: StudentProfile, skill_id: int) -> list[Evidence]:
    return list(
        session.scalars(
            select(Evidence)
            .where(Evidence.student_id == profile.id, Evidence.skill_id == skill_id)
            .order_by(Evidence.evidence_date.desc())
        ).all()
    )


def to_claim(row: Evidence) -> evidence_policy.EvidenceClaim:
    return evidence_policy.EvidenceClaim(
        evidence_id=row.id,
        category=row.category,
        status=row.status,
        demonstrated_level=row.demonstrated_level,
        evidence_date=row.evidence_date,
        expiry_date=row.expiry_date,
        reviewer_id=row.reviewer_id,
        source=row.source,
    )


def assess_skill(session: Session, profile: StudentProfile, skill_id: int) -> evidence_policy.SkillAssessment:
    declared = session.scalar(
        select(StudentSkill.declared_level).where(
            StudentSkill.student_id == profile.id, StudentSkill.skill_id == skill_id
        )
    )
    claims = [to_claim(row) for row in evidence_claims(session, profile, skill_id)]
    return evidence_policy.evaluate_skill(declared, claims)


def skill_views(session: Session, profile: StudentProfile) -> list[SkillView]:
    """Every skill the student has declared or evidenced, with its six fields."""
    from data.models import Skill

    declared = {row.skill_id: row for row in declared_skills(session, profile)}
    evidenced = {
        row.skill_id
        for row in session.scalars(
            select(Evidence).where(Evidence.student_id == profile.id)
        ).all()
    }
    skill_ids = sorted(set(declared) | evidenced)
    if not skill_ids:
        return []

    names = {
        skill.id: skill
        for skill in session.scalars(select(Skill).where(Skill.id.in_(skill_ids))).all()
    }
    views: list[SkillView] = []
    for skill_id in skill_ids:
        skill = names.get(skill_id)
        claims_rows = evidence_claims(session, profile, skill_id)
        assessment = evidence_policy.evaluate_skill(
            declared[skill_id].declared_level if skill_id in declared else None,
            [to_claim(row) for row in claims_rows],
        )
        latest = max((row.evidence_date for row in claims_rows if row.evidence_date), default=None)
        views.append(
            SkillView(
                skill_id=skill_id,
                skill_name=skill.canonical_name if skill else f"Skill {skill_id}",
                category=skill.category if skill else "",
                declared_level=assessment.declared_level,
                assessment=assessment,
                evidence_count=len(claims_rows),
                latest_evidence_date=latest,
            )
        )
    return views


def availability_stamp(profile: StudentProfile, *, now=None) -> dict:
    return freshness.stamp(
        profile.availability_updated_at,
        now=now,
        threshold_days=freshness.AVAILABILITY_THRESHOLD_DAYS,
        subject="Availability",
    )
