"""Skill passports: project-scoped, evidence-backed records a student can publish.

A passport entry is created only from a confirmed team place, so it cannot be
invented by the student. Publishing is an explicit action and is audited; a
published entry is readable by another signed-in person, an unpublished one is
private to its owner and administrators.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.models import PassportEntry, Project, StudentProfile, Team, TeamMember, User, utcnow
from services import audit_log
from services.errors import Conflict, NotFound, PermissionDenied, ValidationError

MAX_ITEMS = 8


def for_student(session: Session, profile: StudentProfile) -> list[PassportEntry]:
    return list(
        session.scalars(
            select(PassportEntry)
            .where(PassportEntry.student_id == profile.id)
            .order_by(PassportEntry.created_at.desc())
        ).all()
    )


def visible_for(viewer: User | None, owner_profile: StudentProfile, entries: list[PassportEntry]) -> list[PassportEntry]:
    if viewer is None:
        return []
    if viewer.role == "admin" or viewer.id == owner_profile.user_id:
        return entries
    return [entry for entry in entries if entry.publication_state == "approved"]


def get(session: Session, entry_id: int) -> PassportEntry:
    row = session.get(PassportEntry, entry_id)
    if row is None:
        raise NotFound("That passport entry does not exist.")
    return row


def require_owner(user: User, entry: PassportEntry) -> StudentProfile:
    profile = entry.student
    if profile is None:
        profile = None
    owner_id = profile.user_id if profile is not None else None
    if user.role != "admin" and user.id != owner_id:
        raise PermissionDenied(
            "This passport belongs to another student.",
            target_type="passport_entry",
            target_id=entry.id,
            reason="not_owner",
        )
    return profile


def _clean_list(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        text = (value or "").strip()
        if text and text not in cleaned:
            cleaned.append(text[:80])
    return cleaned[:MAX_ITEMS]


def create(
    session: Session,
    *,
    profile: StudentProfile,
    actor: User,
    project_id: int,
    role: str,
    technologies: list[str],
    competencies: list[str],
    verified_scope: str,
    ip: str = "",
) -> PassportEntry:
    if actor.id != profile.user_id:
        raise PermissionDenied("You can only create a passport for your own profile.")
    project = session.get(Project, project_id)
    if project is None:
        raise NotFound("That project does not exist.")
    place = session.scalar(
        select(TeamMember)
        .join(Team, Team.id == TeamMember.team_id)
        .where(
            Team.project_id == project_id,
            Team.state == "confirmed",
            TeamMember.student_id == profile.id,
        )
    )
    if place is None:
        raise ValidationError(
            "A passport entry needs a confirmed place on the project team.",
            field_errors={"project_id": ["You are not on a confirmed team for that project."]},
        )
    existing = session.scalar(
        select(PassportEntry).where(
            PassportEntry.student_id == profile.id,
            PassportEntry.project_id == project_id,
        )
    )
    if existing is not None:
        raise Conflict("You already have a passport entry for that project.")

    entry = PassportEntry(
        student_id=profile.id,
        project_id=project_id,
        role=(role or place.role or "contributor")[:80],
        technologies=_clean_list(technologies),
        competencies=_clean_list(competencies),
        verified_scope=(verified_scope or "").strip()[:2000],
        publication_state="unpublished",
        shared_fields=["role", "technologies", "competencies", "verified_scope"],
    )
    session.add(entry)
    session.flush()
    audit_log.record(
        session,
        actor=actor,
        action="passport.created",
        target_type="passport_entry",
        target_id=entry.id,
        detail={"project_id": project_id, "role": entry.role},
        ip=ip,
    )
    return entry


def set_publication(
    session: Session, *, entry: PassportEntry, actor: User, publish: bool, ip: str = ""
) -> PassportEntry:
    require_owner(actor, entry)
    if publish and not entry.verified_scope.strip():
        raise ValidationError(
            "Describe the verified scope before publishing.",
            field_errors={"verified_scope": ["Say what the mentor confirmed."]},
        )
    before = entry.publication_state
    entry.publication_state = "approved" if publish else "unpublished"
    if publish:
        entry.approver_id = actor.id
    audit_log.record(
        session,
        actor=actor,
        action="passport.published" if publish else "passport.unpublished",
        target_type="passport_entry",
        target_id=entry.id,
        before_ref=before,
        after_ref=entry.publication_state,
        ip=ip,
    )
    return entry


def export_payload(entry: PassportEntry) -> dict:
    """The export a student can download. Deliberately small and checkable."""
    project = entry.project
    return {
        "passport": {
            "id": entry.id,
            "student": entry.student.user.display_name if entry.student and entry.student.user else "",
            "project": project.title if project else "",
            "domain": project.domain if project else "",
            "role": entry.role,
            "technologies": entry.technologies,
            "competencies": entry.competencies,
            "verified_scope": entry.verified_scope,
            "publication_state": entry.publication_state,
            "shared_fields": entry.shared_fields,
            "exported_on": date.today().isoformat(),
            "note": (
                "Synthetic demo record. A passport describes verified project work; it is not a "
                "certification, a guarantee of ability or a prediction of performance."
            ),
        }
    }


def export_json(entry: PassportEntry) -> str:
    return json.dumps(export_payload(entry), indent=2, ensure_ascii=False)
