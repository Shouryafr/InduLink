"""Feedback, evaluations and appeals, with confidentiality applied server-side.

Confidential does not mean hidden from its author: the author and administrators
can read it, and the person it is about cannot. Every list route filters with the
same functions, so a hand-typed URL cannot widen access.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.models import (
    Appeal,
    Contribution,
    Evaluation,
    Feedback,
    MentorAssignment,
    Project,
    StudentProfile,
    Team,
    TeamMember,
    User,
    utcnow,
)
from services import audit_log
from services.errors import NotFound, PermissionDenied, ValidationError

RATING_KEYS = ("clarity", "reliability", "collaboration")
APPEAL_STATUSES = ("open", "under_review", "resolved", "withdrawn")
CLOSED_APPEAL_STATUSES = ("resolved", "withdrawn")


def _touch_project(session: Session, project_id: int | None) -> Project | None:
    if project_id is None:
        return None
    project = session.get(Project, project_id)
    if project is None:
        raise NotFound("That project does not exist.")
    return project


def require_project_participant(session: Session, user: User, project: Project) -> None:
    if user.role == "admin" or project.owner_id == user.id:
        return
    if user.role == "mentor":
        mentor_profile = user.mentor_profile
        if mentor_profile is not None:
            assigned = session.scalar(
                select(MentorAssignment.id).where(
                    MentorAssignment.project_id == project.id,
                    MentorAssignment.mentor_id == mentor_profile.id,
                    MentorAssignment.state.in_({"proposed", "confirmed"}),
                )
            )
            if assigned is not None:
                return
    raise PermissionDenied(
        "Only the partner, the confirmed mentor or an administrator can record this.",
        target_type="project",
        target_id=project.id,
        reason="not_project_participant",
    )


def _clean_ratings(ratings: dict) -> dict[str, int]:
    cleaned: dict[str, int] = {}
    for key, value in ratings.items():
        if key not in RATING_KEYS:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValidationError(
                f"Rating for {key} must be a whole number from 1 to 5.",
                field_errors={key: ["Enter 1 to 5."]},
            )
        if not 1 <= number <= 5:
            raise ValidationError(
                f"Rating for {key} must be 1 to 5.",
                field_errors={key: ["Enter 1 to 5."]},
            )
        cleaned[key] = number
    return cleaned


def submit_feedback(
    session: Session,
    *,
    author: User,
    project_id: int | None,
    subject_user_id: int | None,
    ratings: dict,
    comments: str,
    confidential: bool = True,
    ip: str = "",
) -> Feedback:
    project = _touch_project(session, project_id)
    if project is not None:
        require_project_participant(session, author, project)
    elif author.role not in {"mentor", "industry", "admin"}:
        raise PermissionDenied("Only a reviewer can record feedback.")
    if subject_user_id is not None:
        subject = session.get(User, subject_user_id)
        if subject is None or subject.role != "student":
            raise ValidationError(
                "Choose the student this feedback is about.",
                field_errors={"subject_user_id": ["Choose a student."]},
            )
    if len((comments or "").strip()) < 5:
        raise ValidationError(
            "Add a short comment so the feedback is useful.",
            field_errors={"comments": ["Write at least a few words."]},
        )
    row = Feedback(
        author_id=author.id,
        project_id=project_id,
        subject_user_id=subject_user_id,
        subject_role="student",
        structured_ratings=_clean_ratings(ratings),
        comments=comments.strip()[:4000],
        confidential=bool(confidential),
        is_synthetic=True,
    )
    session.add(row)
    session.flush()
    audit_log.record(
        session,
        actor=author,
        action="feedback.recorded",
        target_type="feedback",
        target_id=row.id,
        detail={"project_id": project_id, "confidential": bool(confidential)},
        ip=ip,
    )
    return row


def can_view_feedback(viewer: User, row: Feedback) -> bool:
    if viewer.role == "admin" or viewer.id == row.author_id:
        return True
    if row.confidential:
        return False
    return row.subject_user_id == viewer.id


def feedback_feed(session: Session, viewer: User, *, limit: int = 100) -> list[Feedback]:
    rows = list(
        session.scalars(select(Feedback).order_by(Feedback.created_at.desc()).limit(limit)).all()
    )
    return [row for row in rows if can_view_feedback(viewer, row)]


def record_evaluation(
    session: Session,
    *,
    reviewer: User,
    project_id: int,
    scores: dict,
    summary: str,
    outcome: str = "met",
    confidential: bool = True,
    ip: str = "",
) -> Evaluation:
    project = _touch_project(session, project_id)
    if project is None:
        raise NotFound("That project does not exist.")
    require_project_participant(session, reviewer, project)
    if len((summary or "").strip()) < 5:
        raise ValidationError(
            "Add a short summary of the evaluation.",
            field_errors={"summary": ["Write at least a few words."]},
        )
    row = Evaluation(
        project_id=project.id,
        rubric={"delivery": 40, "quality": 40, "communication": 20},
        scores=_clean_ratings(scores or {}),
        reviewer_id=reviewer.id,
        outcome=(outcome or "met")[:40],
        summary=summary.strip()[:4000],
        confidential=bool(confidential),
        is_synthetic=True,
    )
    session.add(row)
    session.flush()
    audit_log.record(
        session,
        actor=reviewer,
        action="evaluation.recorded",
        target_type="evaluation",
        target_id=row.id,
        detail={"project_id": project.id, "confidential": bool(confidential)},
        ip=ip,
    )
    return row


def can_view_evaluation(viewer: User, row: Evaluation) -> bool:
    if viewer.role == "admin" or viewer.id == row.reviewer_id:
        return True
    return not row.confidential


def evaluations_for(session: Session, viewer: User, project_id: int | None = None, *, limit: int = 50) -> list[Evaluation]:
    query = select(Evaluation).order_by(Evaluation.created_at.desc()).limit(limit)
    if project_id is not None:
        query = (
            select(Evaluation)
            .where(Evaluation.project_id == project_id)
            .order_by(Evaluation.created_at.desc())
            .limit(limit)
        )
    rows = list(session.scalars(query).all())
    return [row for row in rows if can_view_evaluation(viewer, row)]


def submit_appeal(
    session: Session,
    *,
    profile: StudentProfile,
    actor: User,
    statement: str,
    contribution_id: int | None = None,
    ip: str = "",
) -> Appeal:
    if actor.id != profile.user_id:
        raise PermissionDenied("You can only appeal your own record.")
    if len((statement or "").strip()) < 10:
        raise ValidationError(
            "Explain what should be reviewed.",
            field_errors={"statement": ["Write at least a sentence."]},
        )
    if contribution_id is not None:
        contribution = session.get(Contribution, contribution_id)
        if contribution is None or contribution.member_id != profile.id:
            raise NotFound("That contribution record does not exist on your profile.")
    row = Appeal(
        contribution_id=contribution_id,
        student_id=profile.id,
        statement=statement.strip()[:4000],
        status="open",
    )
    session.add(row)
    session.flush()
    audit_log.record(
        session,
        actor=actor,
        action="appeal.submitted",
        target_type="appeal",
        target_id=row.id,
        detail={"contribution_id": contribution_id},
        ip=ip,
    )
    return row


def appeals_for_student(session: Session, profile: StudentProfile) -> list[Appeal]:
    return list(
        session.scalars(
            select(Appeal)
            .where(Appeal.student_id == profile.id)
            .order_by(Appeal.created_at.desc())
        ).all()
    )


def appeals(session: Session, *, status: str = "", limit: int = 100) -> list[Appeal]:
    query = select(Appeal).order_by(Appeal.created_at.desc()).limit(limit)
    if status:
        query = (
            select(Appeal)
            .where(Appeal.status == status)
            .order_by(Appeal.created_at.desc())
            .limit(limit)
        )
    return list(session.scalars(query).all())


def resolve_appeal(
    session: Session,
    *,
    appeal: Appeal,
    actor: User,
    status: str,
    resolution: str,
    ip: str = "",
) -> Appeal:
    if actor.role != "admin":
        raise PermissionDenied("Only an administrator can resolve an appeal.")
    if status not in APPEAL_STATUSES:
        raise ValidationError("Choose a valid appeal status.")
    if status in CLOSED_APPEAL_STATUSES and len((resolution or "").strip()) < 5:
        raise ValidationError(
            "Record the resolution before closing an appeal.",
            field_errors={"resolution": ["Record the resolution."]},
        )
    before = appeal.status
    appeal.status = status
    appeal.resolution = (resolution or "").strip()[:2000]
    if status in CLOSED_APPEAL_STATUSES:
        appeal.resolved_by = actor.id
        appeal.resolved_at = utcnow()
    audit_log.record(
        session,
        actor=actor,
        action="appeal.decided",
        target_type="appeal",
        target_id=appeal.id,
        before_ref=before,
        after_ref=status,
        reason=appeal.resolution[:200],
        ip=ip,
    )
    profile = session.get(StudentProfile, appeal.student_id)
    if profile is not None:
        from services import notifications

        notifications.notify(
            session,
            user_id=profile.user_id,
            kind="appeal",
            title=f"Your appeal is now {status.replace('_', ' ')}",
            body=(appeal.resolution or "Open the appeal to read the decision.")[:400],
            href="/student/appeals",
        )
    return appeal


def team_members_for_project(session: Session, project_id: int) -> list[StudentProfile]:
    rows = session.execute(
        select(TeamMember.student_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(Team.project_id == project_id, Team.state == "confirmed")
    ).all()
    profiles: list[StudentProfile] = []
    for (student_id,) in rows:
        profile = session.get(StudentProfile, student_id)
        if profile is not None:
            profiles.append(profile)
    return profiles


def today() -> date:
    return date.today()
