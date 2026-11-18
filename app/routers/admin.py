"""University administrator screens: oversight, registry, approvals, audit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_db, get_ip, require_roles
from app.rendering import query_error, render
from core import evidence_policy
from data.models import (
    Appeal,
    Evidence,
    Mentor,
    MentorAssignment,
    PassportEntry,
    Project,
    Skill,
    SkillAlias,
    SkillProposal,
    StudentProfile,
    User,
)
from services import audit_log, authentication, demo, engagement, evidence_registry
from services import projects as project_service
from services import passports, skill_registry
from services.errors import Conflict, DomainError, NotFound, PermissionDenied, ValidationError

router = APIRouter(prefix="/admin", tags=["admin"])
AdminUser = Depends(require_roles("admin"))


@router.get("")
def dashboard(request: Request, user: User = AdminUser, db: Session = Depends(get_db)):
    users_by_role = dict(
        db.execute(select(User.role, func.count(User.id)).group_by(User.role)).all()
    )
    users_by_status = dict(
        db.execute(select(User.status, func.count(User.id)).group_by(User.status)).all()
    )
    projects_by_state = dict(
        db.execute(select(Project.state, func.count(Project.id)).group_by(Project.state)).all()
    )
    evidence_by_status = dict(
        db.execute(select(Evidence.status, func.count(Evidence.id)).group_by(Evidence.status)).all()
    )
    pending_projects = list(
        db.scalars(select(Project).where(Project.state == "pending_approval")).all()
    )
    integrity = request.app.state.db.integrity_check()
    pragmas = request.app.state.db.pragma_report()
    return render(
        request,
        "admin/dashboard.html",
        active="/admin",
        users_by_role=users_by_role,
        users_by_status=users_by_status,
        projects_by_state=projects_by_state,
        evidence_by_status=evidence_by_status,
        pending_projects=pending_projects,
        students=db.scalar(select(func.count(StudentProfile.id))) or 0,
        mentors=db.scalar(select(func.count(Mentor.id))) or 0,
        confirmed_mentors=db.scalar(
            select(func.count(MentorAssignment.id)).where(MentorAssignment.state == "confirmed")
        )
        or 0,
        skills=db.scalar(select(func.count(Skill.id))) or 0,
        integrity=integrity,
        pragmas=pragmas,
        open_proposals=db.scalar(
            select(func.count(SkillProposal.id)).where(SkillProposal.status == "open")
        )
        or 0,
    )


@router.get("/users")
def users(request: Request, user: User = AdminUser, db: Session = Depends(get_db), role: str = ""):
    query = select(User).order_by(User.created_at.desc()).limit(200)
    if role:
        query = select(User).where(User.role == role).order_by(User.created_at.desc()).limit(200)
    rows = list(db.scalars(query).all())
    return render(
        request,
        "admin/users.html",
        active="/admin/users",
        users=rows,
        filter_role=role,
        saved=request.query_params.get("saved", ""),
    )


@router.post("/users/{user_id}/status")
def set_status(
    user_id: int,
    request: Request,
    status: str = Form(""),
    reason: str = Form(""),
    user: User = AdminUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    from services.errors import NotFound, PermissionDenied

    target = db.get(User, user_id)
    if target is None:
        raise NotFound("That account does not exist.")
    if target.id == user.id:
        raise PermissionDenied("You cannot change the status of your own account.")
    if status not in {"active", "suspended", "pending"}:
        raise ValidationError("Choose a valid account status.")
    if not reason.strip():
        raise ValidationError(
            "Record a reason for the status change.",
            field_errors={"reason": ["Record a reason for the status change."]},
        )
    before = target.status
    target.status = status
    audit_log.record(
        db,
        actor=user,
        action="user.status_changed",
        target_type="user",
        target_id=target.id,
        reason=reason.strip()[:200],
        before_ref=before,
        after_ref=status,
        ip=ip,
    )
    return RedirectResponse("/admin/users?saved=status", status_code=303)


@router.get("/skills")
def skills(request: Request, user: User = AdminUser, db: Session = Depends(get_db)):
    registry = skill_registry.load(db)
    aliases = list(db.scalars(select(SkillAlias).order_by(SkillAlias.alias)).all())
    proposals = list(
        db.scalars(
            select(SkillProposal)
            .where(SkillProposal.status == "open")
            .order_by(SkillProposal.created_at.desc())
        ).all()
    )
    return render(
        request,
        "admin/skills.html",
        active="/admin/skills",
        registry=registry,
        aliases=aliases,
        proposals=proposals,
        saved=request.query_params.get("saved", ""),
        errors={},
        error=query_error(
            request,
            {
                "alias": (
                    "That alias was not added. It may already point at another skill, or repeat "
                    "an existing name."
                ),
                "proposal": (
                    "That proposal was not resolved. The name may already exist in the registry."
                ),
            },
        ),
    )


@router.post("/skills")
def create_skill(
    request: Request,
    name: str = Form(""),
    category: str = Form("software"),
    aliases: str = Form(""),
    user: User = AdminUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    alias_list = [part.strip() for part in aliases.split(",") if part.strip()]
    try:
        skill_registry.create_skill(
            db, name=name, category=category, actor=user, aliases=alias_list, ip=ip
        )
    except DomainError as exc:
        return render(
            request,
            "admin/skills.html",
            status_code=exc.status_code,
            active="/admin/skills",
            registry=skill_registry.load(db),
            aliases=list(db.scalars(select(SkillAlias)).all()),
            proposals=list(
                db.scalars(select(SkillProposal).where(SkillProposal.status == "open")).all()
            ),
            errors=exc.field_errors or {"name": [exc.message]},
            error=exc.message,
        )
    return RedirectResponse("/admin/skills?saved=skill", status_code=303)


@router.post("/skills/{skill_id}/aliases")
def add_alias(
    skill_id: int,
    request: Request,
    alias: str = Form(""),
    user: User = AdminUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    skill = skill_registry.get_skill_or_404(db, skill_id)
    try:
        skill_registry.add_alias(db, skill=skill, alias=alias, actor=user, ip=ip)
    except DomainError:
        return RedirectResponse("/admin/skills?error=alias", status_code=303)
    return RedirectResponse("/admin/skills?saved=alias", status_code=303)


@router.post("/proposals/{proposal_id}/resolve")
def resolve_proposal(
    proposal_id: int,
    request: Request,
    decision: str = Form("accept"),
    user: User = AdminUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    from services.errors import NotFound

    proposal = db.get(SkillProposal, proposal_id)
    if proposal is None:
        raise NotFound("That proposal does not exist.")
    if proposal.status != "open":
        return RedirectResponse("/admin/skills?saved=proposal", status_code=303)

    if decision == "accept":
        try:
            skill_registry.create_skill(
                db,
                name=proposal.proposed_name,
                category=proposal.category,
                actor=user,
                ip=ip,
            )
        except DomainError:
            return RedirectResponse("/admin/skills?error=proposal", status_code=303)
        proposal.status = "accepted"
    else:
        proposal.status = "rejected"
    proposal.resolved_by = user.id
    audit_log.record(
        db,
        actor=user,
        action=f"skill_proposal.{proposal.status}",
        target_type="skill_proposal",
        target_id=proposal.id,
        after_ref=proposal.status,
        ip=ip,
    )
    return RedirectResponse("/admin/skills?saved=proposal", status_code=303)


@router.get("/evidence")
def evidence_oversight(request: Request, user: User = AdminUser, db: Session = Depends(get_db)):
    rows = list(
        db.scalars(select(Evidence).order_by(Evidence.id.desc()).limit(200)).all()
    )
    registry = skill_registry.load(db)
    items = []
    for row in rows:
        context = evidence_registry.context_for(row)
        context["skill_name"] = registry.name_of(row.skill_id)
        context["student_name"] = row.student.user.display_name if row.student and row.student.user else "—"
        context["reviewer_name"] = row.reviewer.display_name if row.reviewer else ""
        items.append(context)
    return render(
        request,
        "admin/evidence.html",
        active="/admin/evidence",
        items=items,
        review_queue=evidence_registry.review_queue(db, limit=20),
        revalidation=evidence_registry.revalidation_queue(db, limit=20),
        categories=evidence_policy.EVIDENCE_CATEGORIES,
    )


@router.get("/projects")
def project_approvals(request: Request, user: User = AdminUser, db: Session = Depends(get_db)):
    rows = list(
        db.scalars(select(Project).order_by(Project.updated_at.desc()).limit(100)).all()
    )
    return render(
        request,
        "admin/projects.html",
        active="/admin/projects",
        projects=rows,
        saved=request.query_params.get("saved", ""),
    )


@router.post("/projects/{project_id}/transition")
def transition_project(
    project_id: int,
    request: Request,
    target: str = Form(""),
    reason: str = Form(""),
    user: User = AdminUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    project = project_service.get_project(db, project_id)
    project_service.transition(db, project=project, target=target, actor=user, reason=reason, ip=ip)
    return RedirectResponse("/admin/projects?saved=state", status_code=303)


@router.get("/audit")
def audit(request: Request, user: User = AdminUser, db: Session = Depends(get_db), q: str = ""):
    events = audit_log.recent(db, limit=200)
    if q:
        needle = q.lower()
        events = [
            event
            for event in events
            if needle in event.action.lower()
            or needle in event.target_type.lower()
            or needle in event.reason.lower()
        ]
    return render(request, "admin/audit.html", active="/admin/audit", events=events, query=q)


# --- Appeals, passports and the demo controls ----------------------------


@router.get("/appeals")
def appeals_page(
    request: Request,
    user: User = AdminUser,
    db: Session = Depends(get_db),
    status: str = "",
):
    rows = engagement.appeals(db, status=status)
    return render(
        request,
        "admin/appeals.html",
        active="/admin/appeals",
        appeals=rows,
        filter_status=status,
        saved=request.query_params.get("saved", ""),
        error=query_error(
            request,
            {"resolution": "A closed appeal needs a written resolution."},
        ),
    )


@router.post("/appeals/{appeal_id}")
def resolve_appeal(
    appeal_id: int,
    request: Request,
    status: str = Form("under_review"),
    resolution: str = Form(""),
    user: User = AdminUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    appeal = db.get(Appeal, appeal_id)
    if appeal is None:
        raise NotFound("That appeal does not exist.")
    try:
        engagement.resolve_appeal(
            db,
            appeal=appeal,
            actor=user,
            status=status,
            resolution=resolution,
            ip=ip,
        )
    except ValidationError:
        return RedirectResponse("/admin/appeals?error=resolution", status_code=303)
    return RedirectResponse("/admin/appeals?saved=resolved", status_code=303)


@router.get("/passports")
def passports_page(
    request: Request,
    user: User = AdminUser,
    db: Session = Depends(get_db),
):
    rows = list(
        db.scalars(
            select(PassportEntry).order_by(PassportEntry.created_at.desc()).limit(200)
        ).all()
    )
    return render(
        request,
        "admin/passports.html",
        active="/admin/passports",
        rows=rows,
        published=sum(1 for row in rows if row.publication_state == "approved"),
    )


@router.get("/demo")
def demo_page(
    request: Request,
    user: User = AdminUser,
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    resets = [
        event
        for event in audit_log.recent(db, limit=200)
        if event.action == "demo.reset"
    ][:5]
    return render(
        request,
        "admin/demo.html",
        active="/admin/demo",
        available=demo.is_available(settings),
        demo_mode=settings.demo_mode,
        resets=resets,
        reset_done=request.query_params.get("reset") == "1",
        counts={
            "students": db.scalar(select(func.count(StudentProfile.id))) or 0,
            "projects": db.scalar(select(func.count(Project.id))) or 0,
            "evidence": db.scalar(select(func.count(Evidence.id))) or 0,
            "passports": db.scalar(select(func.count(PassportEntry.id))) or 0,
        },
    )


@router.post("/demo/reset")
def reset_demo(
    request: Request,
    user: User = AdminUser,
    ip: str = Depends(get_ip),
):
    settings = request.app.state.settings
    if not demo.is_available(settings):
        raise PermissionDenied(
            "Demo reset is only available in demo mode with a SQLite demo database."
        )
    outcome, new_database = demo.reset(settings)
    demo.swap(request.app, outcome, new_database)

    # The reset replaced the database, so the administrator's session row is gone.
    # A fresh session is issued on the new database so the demo continues without
    # a surprise sign-out, and the reset itself is written to the new audit log.
    with new_database.session() as session:
        admin_user = session.scalar(select(User).where(User.email == user.email))
        if admin_user is None:  # pragma: no cover - the seed always creates this account
            return RedirectResponse("/sign-in?reset=1", status_code=303)
        token = authentication.create_session(
            session,
            user=admin_user,
            settings=settings,
            ip=ip,
            user_agent=request.headers.get("user-agent", ""),
        )
        audit_log.record(
            session,
            actor=admin_user,
            action="demo.reset",
            target_type="database",
            target_id="demo",
            reason=f"Restored the seeded dataset in {outcome.seconds}s",
            detail={
                "seconds": outcome.seconds,
                "counts": outcome.counts,
                "removed_old_file": outcome.removed_old,
            },
            ip=ip,
        )
    response = RedirectResponse("/admin/demo?reset=1", status_code=303)
    response.set_cookie(
        authentication.SESSION_COOKIE,
        token,
        max_age=int(settings.session_absolute_timeout.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return response
