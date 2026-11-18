"""In-app notifications.

Rules generate a short, plain-language record for the person who has to act.
Nothing is sent by e-mail, and nothing here is used for scoring. Confidential
material is never copied into a notification body: the body names the record and
links to it, and the linked screen applies its own permission check.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from data.models import Notification, User, utcnow

MAX_TITLE = 200
MAX_BODY = 600


def notify(
    session: Session,
    *,
    user_id: int | None,
    kind: str,
    title: str,
    body: str = "",
    href: str = "",
) -> Notification | None:
    """Queue one notification. A missing recipient is a no-op, not an error."""
    if not user_id:
        return None
    row = Notification(
        user_id=user_id,
        kind=(kind or "info")[:40],
        title=(title or "Update")[:MAX_TITLE],
        body=(body or "")[:MAX_BODY],
        href=(href or "")[:300],
        is_synthetic=True,
    )
    session.add(row)
    return row


def notify_many(
    session: Session,
    *,
    user_ids: list[int],
    kind: str,
    title: str,
    body: str = "",
    href: str = "",
) -> int:
    created = 0
    for user_id in sorted(set(user_ids)):
        if notify(session, user_id=user_id, kind=kind, title=title, body=body, href=href):
            created += 1
    return created


def for_user(session: Session, user_id: int, *, limit: int = 50) -> list[Notification]:
    return list(
        session.scalars(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.read_at.is_(None).desc(), Notification.created_at.desc())
            .limit(limit)
        ).all()
    )


def unread_count(session: Session, user_id: int) -> int:
    return int(
        session.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
        or 0
    )


def mark_read(session: Session, *, user_id: int, notification_id: int) -> bool:
    row = session.get(Notification, notification_id)
    if row is None or row.user_id != user_id:
        return False
    if row.read_at is None:
        row.read_at = utcnow()
    return True


def mark_all_read(session: Session, *, user_id: int) -> int:
    result = session.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    return int(result.rowcount or 0)


def recipients_for_project(session: Session, project_id: int) -> list[int]:
    """Owner plus every confirmed team member, for project-wide events."""
    from data.models import Project, Team, TeamMember

    project = session.get(Project, project_id)
    ids: list[int] = []
    if project is not None:
        ids.append(project.owner_id)
    rows = session.execute(
        select(TeamMember.student_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(Team.project_id == project_id, Team.state == "confirmed")
    ).all()
    for (student_id,) in rows:
        from data.models import StudentProfile

        profile = session.get(StudentProfile, student_id)
        if profile is not None:
            ids.append(profile.user_id)
    return ids


def user_id_of(session: Session, user: User | None) -> int | None:
    return user.id if user is not None else None
