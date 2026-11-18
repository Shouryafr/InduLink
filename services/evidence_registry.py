"""Evidence submission and review (brief sections 7.4, 10.2).

A reviewed item sets an *assessed* level in ``evidence``. It never writes to
``student_skills.declared_level``; the two stay separate so the UI can always
show what the student claimed next to what a reviewer confirmed.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import evidence_policy
from data.models import Evidence, StudentProfile, User, utcnow
from services import audit_log
from services.errors import NotFound, PermissionDenied, ValidationError

REVIEWER_ROLES = frozenset({"mentor", "admin"})
DECISIONS = frozenset({"reviewed", "rejected", "verified"})
MIN_NOTE_LENGTH = 5


def submit(
    session: Session,
    *,
    profile: StudentProfile,
    skill_id: int,
    category: str,
    source: str,
    evidence_date: date,
    demonstrated_level: int | None,
    actor: User,
    expiry_date: date | None = None,
    ip: str = "",
) -> Evidence:
    if actor.id != profile.user_id:
        raise PermissionDenied("You can only submit evidence for your own profile.")

    errors: dict[str, list[str]] = {}
    if category not in evidence_policy.EVIDENCE_CATEGORIES:
        errors["category"] = ["Choose an evidence type."]
    if not (source or "").strip():
        errors["source"] = ["Describe the source: a link, a repository, a certificate reference."]
    elif len(source.strip()) < 3:
        errors["source"] = ["Add a little more detail so a reviewer can check it."]
    if evidence_date is None:
        errors["evidence_date"] = ["Enter the date the evidence relates to."]
    elif evidence_date > date.today():
        errors["evidence_date"] = ["The evidence date cannot be in the future."]
    if demonstrated_level is not None and not (1 <= demonstrated_level <= 5):
        errors["demonstrated_level"] = ["Choose a level from 1 to 5."]
    if expiry_date is not None and evidence_date is not None and expiry_date < evidence_date:
        errors["expiry_date"] = ["An expiry date cannot be before the evidence date."]
    if errors:
        raise ValidationError("Some evidence details need attention.", field_errors=errors)

    row = Evidence(
        student_id=profile.id,
        skill_id=skill_id,
        category=category,
        source=source.strip()[:300],
        evidence_date=evidence_date,
        expiry_date=expiry_date,
        demonstrated_level=demonstrated_level,
        status="submitted",
    )
    session.add(row)
    session.flush()
    audit_log.record(
        session,
        actor=actor,
        action="evidence.submitted",
        target_type="evidence",
        target_id=row.id,
        detail={"skill_id": skill_id, "category": category},
        ip=ip,
    )
    return row


def get(session: Session, evidence_id: int) -> Evidence:
    row = session.get(Evidence, evidence_id)
    if row is None:
        raise NotFound("That evidence item does not exist.")
    return row


def claim_for_review(session: Session, *, evidence: Evidence, reviewer: User, ip: str = "") -> Evidence:
    _require_reviewer(evidence, reviewer)
    if evidence.status not in {"submitted", "under_review"}:
        raise ValidationError("This item has already been decided.")
    evidence.status = "under_review"
    evidence.reviewer_id = reviewer.id
    audit_log.record(
        session,
        actor=reviewer,
        action="evidence.claimed",
        target_type="evidence",
        target_id=evidence.id,
        ip=ip,
    )
    return evidence


def _require_reviewer(evidence: Evidence, reviewer: User) -> None:
    if reviewer.role not in REVIEWER_ROLES:
        raise PermissionDenied("Only a mentor or administrator can review evidence.")
    profile_user_id = evidence.student.user_id if evidence.student is not None else None
    if profile_user_id is not None and reviewer.id == profile_user_id:
        raise PermissionDenied("You cannot review your own evidence.")


def review(
    session: Session,
    *,
    evidence: Evidence,
    reviewer: User,
    decision: str,
    assessed_level: int | None,
    notes: str,
    ip: str = "",
) -> Evidence:
    _require_reviewer(evidence, reviewer)

    errors: dict[str, list[str]] = {}
    if decision not in DECISIONS:
        errors["decision"] = ["Choose reviewed, verified or rejected."]
    if len((notes or "").strip()) < MIN_NOTE_LENGTH:
        errors["notes"] = [
            "A review decision needs a note explaining what you checked."
        ]
    if decision in {"reviewed", "verified"} and assessed_level is None:
        errors["assessed_level"] = ["Set the level you were able to confirm."]
    if assessed_level is not None and not (1 <= assessed_level <= 5):
        errors["assessed_level"] = ["Assessed level must be 1 to 5."]
    if errors:
        raise ValidationError("The review needs a note and a level.", field_errors=errors)

    previous_status = evidence.status
    evidence.status = decision
    evidence.reviewer_id = reviewer.id
    evidence.review_notes = notes.strip()[:2000]
    evidence.reviewed_at = utcnow()
    if decision == "rejected":
        # A rejected item keeps its claimed level for the record but is ignored
        # by the evidence policy.
        pass
    else:
        evidence.demonstrated_level = assessed_level

    detail = {"from": previous_status, "to": decision, "level": assessed_level}
    if evidence.category == "certificate" and decision in {"reviewed", "verified"}:
        detail["note"] = (
            "Certificate-backed evidence supports a learning claim; it does not confirm "
            "a practical level."
        )

    audit_log.record(
        session,
        actor=reviewer,
        action=f"evidence.{decision}",
        target_type="evidence",
        target_id=evidence.id,
        reason=notes.strip()[:200],
        before_ref=previous_status,
        after_ref=decision,
        detail=detail,
        ip=ip,
    )
    return evidence


def review_queue(session: Session, *, limit: int = 100) -> list[Evidence]:
    return list(
        session.scalars(
            select(Evidence)
            .where(Evidence.status.in_({"submitted", "under_review"}))
            .order_by(Evidence.evidence_date.asc())
            .limit(limit)
        ).all()
    )


def revalidation_queue(session: Session, *, limit: int = 100, today: date | None = None) -> list[Evidence]:
    """Items whose practical evidence is old enough to warrant re-review."""
    today = today or date.today()
    cutoff = today - timedelta(days=evidence_policy.REVIEW_PROMPT_AFTER_DAYS)
    return list(
        session.scalars(
            select(Evidence)
            .where(
                Evidence.status.in_({"reviewed", "verified"}),
                Evidence.evidence_date < cutoff,
            )
            .order_by(Evidence.evidence_date.asc())
            .limit(limit)
        ).all()
    )


def for_student(session: Session, profile: StudentProfile) -> list[Evidence]:
    return list(
        session.scalars(
            select(Evidence)
            .where(Evidence.student_id == profile.id)
            .order_by(Evidence.evidence_date.desc(), Evidence.id.desc())
        ).all()
    )


def can_view_notes(viewer: User, evidence: Evidence) -> bool:
    if viewer.role == "admin":
        return True
    if viewer.id == getattr(evidence.student, "user_id", None):
        return True
    return evidence.reviewer_id == viewer.id


def context_for(row: Evidence, *, today: date | None = None) -> dict:
    """Template view of one evidence item, with its neutral age badge."""
    today = today or date.today()
    days = evidence_policy.evidence_age_days(row.evidence_date, today)
    age_label, age_tone = evidence_policy.age_badge(days)
    return {
        "id": row.id,
        "category": row.category,
        "category_label": evidence_policy.CATEGORY_LABELS.get(row.category, row.category),
        "source": row.source,
        "evidence_date": row.evidence_date,
        "expiry_date": row.expiry_date,
        "expired": evidence_policy.is_expired(row.expiry_date, today),
        "status": row.status,
        "status_label": evidence_policy.STATUS_LABELS.get(row.status, row.status),
        "review_notes": row.review_notes,
        "reviewed_at": row.reviewed_at,
        "demonstrated_level": row.demonstrated_level,
        "age_days": days,
        "age_label": age_label,
        "age_tone": age_tone,
        "needs_revalidation": evidence_policy.needs_revalidation(row.evidence_date, today),
        "skill_id": row.skill_id,
    }
