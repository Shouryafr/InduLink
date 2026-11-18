"""Student screens: dashboard, profile and skills, evidence."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_db, get_ip, require_roles
from app.rendering import query_error, render
from core import evidence_policy, freshness
from data.models import Contribution, Evidence, Project, Team, TeamMember, User
from services import (
    engagement,
    evidence_registry,
    matching,
    pagination,
    passports,
    projects as project_service,
    skill_registry,
    student_profiles,
)
from services.errors import Conflict, NotFound, PermissionDenied, ValidationError

router = APIRouter(prefix="/student", tags=["student"])
StudentUser = Depends(require_roles("student"))


def _next_actions(session: Session, profile) -> list[dict]:
    """Deterministic next-best actions. Rules only, each with its reason."""
    actions: list[dict] = []
    stamp = student_profiles.availability_stamp(profile)
    if profile.availability_hours is None:
        actions.append(
            {
                "title": "Add your weekly availability",
                "body": "Matching cannot check the availability constraint without it.",
                "href": "/student/profile#availability",
                "cta": "Set availability",
                "tone": "warn",
            }
        )
    elif stamp["stale"]:
        actions.append(
            {
                "title": "Refresh your availability",
                "body": f"{stamp['label']}. Recommendations show this date, so keep it current.",
                "href": "/student/profile#availability",
                "cta": "Refresh",
                "tone": "warn",
            }
        )

    views = student_profiles.skill_views(session, profile)
    provisional = [view for view in views if view.assessment.label == evidence_policy.LABEL_PROVISIONAL]
    if provisional:
        names = ", ".join(view.skill_name for view in provisional[:3])
        more = "" if len(provisional) <= 3 else f" and {len(provisional) - 3} more"
        actions.append(
            {
                "title": "Confirm a provisional skill with evidence",
                "body": (
                    f"{names}{more} rely on a declared level. Provisional skills cannot "
                    "satisfy a mandatory requirement on their own."
                ),
                "href": "/student/evidence",
                "cta": "Add evidence",
                "tone": "info",
            }
        )
    if not views:
        actions.append(
            {
                "title": "Add your first skills",
                "body": "Pick from the canonical registry so your skills match project requirements.",
                "href": "/student/profile",
                "cta": "Add skills",
                "tone": "info",
            }
        )

    awaiting = [
        row
        for row in evidence_registry.for_student(session, profile)
        if row.status in {"submitted", "under_review"}
    ]
    if awaiting:
        actions.append(
            {
                "title": f"{len(awaiting)} evidence item{'s' if len(awaiting) > 1 else ''} awaiting review",
                "body": "A reviewer's assessed level replaces the provisional label once reviewed.",
                "href": "/student/evidence",
                "cta": "View status",
                "tone": "muted",
            }
        )
    return actions


@router.get("")
def dashboard(request: Request, user: User = StudentUser, db: Session = Depends(get_db)):
    profile = student_profiles.get_or_create(db, user)
    views = student_profiles.skill_views(db, profile)
    evidence_rows = evidence_registry.for_student(db, profile)
    counts = {
        "verified": sum(1 for v in views if v.assessment.label == evidence_policy.LABEL_VERIFIED),
        "reviewed": sum(1 for v in views if v.assessment.label == evidence_policy.LABEL_REVIEWED),
        "provisional": sum(1 for v in views if v.assessment.label == evidence_policy.LABEL_PROVISIONAL),
        "unknown": sum(1 for v in views if v.assessment.label == evidence_policy.LABEL_UNKNOWN),
    }
    return render(
        request,
        "student/dashboard.html",
        active="/student",
        profile=profile,
        views=views,
        actions=_next_actions(db, profile),
        counts=counts,
        availability=student_profiles.availability_stamp(profile),
        evidence_count=len(evidence_rows),
        pending_count=sum(1 for row in evidence_rows if row.status in {"submitted", "under_review"}),
    )


@router.get("/profile")
def profile_page(request: Request, user: User = StudentUser, db: Session = Depends(get_db)):
    profile = student_profiles.get_or_create(db, user)
    registry = skill_registry.load(db)
    views = student_profiles.skill_views(db, profile)
    return render(
        request,
        "student/profile.html",
        active="/student/profile",
        profile=profile,
        registry=registry,
        views=views,
        availability=student_profiles.availability_stamp(profile),
        saved=request.query_params.get("saved", ""),
        error=query_error(
            request,
            {
                "proposal": (
                    "That skill could not be proposed. Check the spelling: it may already be in "
                    "the registry, where you can add it directly."
                )
            },
        ),
    )


@router.post("/profile")
def save_profile(
    request: Request,
    headline: str = Form(""),
    interests: str = Form(""),
    coursework: str = Form(""),
    experience: str = Form(""),
    preferred_roles: str = Form(""),
    visibility: str = Form("private"),
    availability_hours: str = Form(""),
    availability_from: str = Form(""),
    availability_to: str = Form(""),
    user: User = StudentUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    profile = student_profiles.get_or_create(db, user)
    errors: dict[str, list[str]] = {}

    hours: int | None = None
    if availability_hours.strip():
        try:
            hours = int(availability_hours)
        except ValueError:
            errors["availability_hours"] = ["Enter whole hours between 0 and 80."]

    def parse(value: str, field: str) -> date | None:
        if not value.strip():
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            errors[field] = ["Use the date format YYYY-MM-DD."]
            return None

    start = parse(availability_from, "availability_from")
    end = parse(availability_to, "availability_to")

    try:
        student_profiles.update_profile(
            db,
            profile=profile,
            actor=user,
            headline=headline,
            interests=interests,
            coursework=coursework,
            experience=experience,
            preferred_roles=preferred_roles,
            visibility=visibility,
            availability_hours=hours,
            availability_from=start,
            availability_to=end,
            ip=ip,
        )
    except ValidationError as exc:
        errors.update(exc.field_errors)

    if errors:
        return render(
            request,
            "student/profile.html",
            status_code=422,
            active="/student/profile",
            profile=profile,
            registry=skill_registry.load(db),
            views=student_profiles.skill_views(db, profile),
            availability=student_profiles.availability_stamp(profile),
            errors=errors,
            error="Some profile details need attention.",
        )
    return RedirectResponse("/student/profile?saved=profile", status_code=303)


@router.post("/profile/skills")
def add_skill(
    request: Request,
    skill_id: str = Form(""),
    typed_skill: str = Form(""),
    declared_level: str = Form(""),
    user: User = StudentUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    profile = student_profiles.get_or_create(db, user)
    registry = skill_registry.load(db)
    errors: dict[str, list[str]] = {}

    resolved_id: int | None = None
    if skill_id.strip():
        try:
            resolved_id = int(skill_id)
        except ValueError:
            errors["skill_id"] = ["Choose a skill from the registry."]
    elif typed_skill.strip():
        resolved = registry.resolve(typed_skill)
        if resolved is None:
            errors["typed_skill"] = [
                "That skill is not in the registry yet. Propose it to an administrator below."
            ]
        else:
            resolved_id = resolved.skill_id
    else:
        errors["skill_id"] = ["Choose a skill from the registry."]

    level: int | None = None
    if declared_level.strip():
        try:
            level = int(declared_level)
        except ValueError:
            errors["declared_level"] = ["Choose a level from 1 to 5."]

    if errors:
        return render(
            request,
            "student/profile.html",
            status_code=422,
            active="/student/profile",
            profile=profile,
            registry=registry,
            views=student_profiles.skill_views(db, profile),
            availability=student_profiles.availability_stamp(profile),
            errors=errors,
            error="That skill could not be added.",
        )

    try:
        student_profiles.set_declared_level(
            db, profile=profile, skill_id=resolved_id, level=level, actor=user, ip=ip
        )
    except ValidationError as exc:
        return render(
            request,
            "student/profile.html",
            status_code=422,
            active="/student/profile",
            profile=profile,
            registry=registry,
            views=student_profiles.skill_views(db, profile),
            availability=student_profiles.availability_stamp(profile),
            errors=exc.field_errors,
            error=exc.message,
        )
    return RedirectResponse("/student/profile?saved=skill", status_code=303)


@router.post("/profile/skills/{skill_id}/remove")
def remove_skill(
    skill_id: int,
    request: Request,
    user: User = StudentUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    profile = student_profiles.get_or_create(db, user)
    student_profiles.set_declared_level(
        db, profile=profile, skill_id=skill_id, level=None, actor=user, ip=ip
    )
    return RedirectResponse("/student/profile?saved=removed", status_code=303)


@router.post("/profile/skills/propose")
def propose_skill(
    request: Request,
    name: str = Form(""),
    note: str = Form(""),
    user: User = StudentUser,
    db: Session = Depends(get_db),
):
    try:
        skill_registry.propose_skill(db, actor=user, name=name, category="software", note=note)
    except ValidationError:
        return RedirectResponse("/student/profile?error=proposal", status_code=303)
    return RedirectResponse("/student/profile?saved=proposed", status_code=303)


@router.get("/evidence")
def evidence_page(request: Request, user: User = StudentUser, db: Session = Depends(get_db)):
    profile = student_profiles.get_or_create(db, user)
    registry = skill_registry.load(db)
    rows = evidence_registry.for_student(db, profile)
    views = student_profiles.skill_views(db, profile)
    skill_names = {view.skill_id: view.skill_name for view in views}
    items = []
    for row in rows:
        context = evidence_registry.context_for(row)
        context["skill_name"] = skill_names.get(row.skill_id, registry.name_of(row.skill_id))
        context["can_see_notes"] = evidence_registry.can_view_notes(user, row)
        items.append(context)
    return render(
        request,
        "student/evidence.html",
        active="/student/evidence",
        profile=profile,
        registry=registry,
        items=items,
        declared=[view for view in views if view.declared_level is not None],
        categories=evidence_policy.EVIDENCE_CATEGORIES,
        saved=request.query_params.get("saved", ""),
        errors={},
        form={},
    )


@router.post("/evidence")
def submit_evidence(
    request: Request,
    skill_id: str = Form(""),
    category: str = Form("practical_task"),
    source: str = Form(""),
    evidence_date: str = Form(""),
    demonstrated_level: str = Form(""),
    expiry_date: str = Form(""),
    user: User = StudentUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    profile = student_profiles.get_or_create(db, user)
    registry = skill_registry.load(db)
    errors: dict[str, list[str]] = {}

    try:
        skill_id_int = int(skill_id)
    except ValueError:
        skill_id_int = 0
        errors["skill_id"] = ["Choose the skill this evidence supports."]

    try:
        parsed_date = date.fromisoformat(evidence_date) if evidence_date else None
    except ValueError:
        parsed_date = None
        errors["evidence_date"] = ["Use the date format YYYY-MM-DD."]

    parsed_expiry = None
    if expiry_date.strip():
        try:
            parsed_expiry = date.fromisoformat(expiry_date)
        except ValueError:
            errors["expiry_date"] = ["Use the date format YYYY-MM-DD."]

    level = None
    if demonstrated_level.strip():
        try:
            level = int(demonstrated_level)
        except ValueError:
            errors["demonstrated_level"] = ["Choose a level from 1 to 5."]

    if not errors:
        try:
            evidence_registry.submit(
                db,
                profile=profile,
                skill_id=skill_id_int,
                category=category,
                source=source,
                evidence_date=parsed_date,
                demonstrated_level=level,
                actor=user,
                expiry_date=parsed_expiry,
                ip=ip,
            )
        except ValidationError as exc:
            errors.update(exc.field_errors)

    if errors:
        rows = evidence_registry.for_student(db, profile)
        views = student_profiles.skill_views(db, profile)
        skill_names = {view.skill_id: view.skill_name for view in views}
        items = []
        for row in rows:
            context = evidence_registry.context_for(row)
            context["skill_name"] = skill_names.get(row.skill_id, registry.name_of(row.skill_id))
            context["can_see_notes"] = evidence_registry.can_view_notes(user, row)
            items.append(context)
        return render(
            request,
            "student/evidence.html",
            status_code=422,
            active="/student/evidence",
            profile=profile,
            registry=registry,
            items=items,
            declared=[view for view in views if view.declared_level is not None],
            categories=evidence_policy.EVIDENCE_CATEGORIES,
            errors=errors,
            error="That evidence could not be submitted.",
            form={
                "skill_id": skill_id,
                "category": category,
                "source": source,
                "evidence_date": evidence_date,
                "demonstrated_level": demonstrated_level,
                "expiry_date": expiry_date,
            },
        )
    return RedirectResponse("/student/evidence?saved=submitted", status_code=303)


# --- Challenges and matching ---------------------------------------------


def _visible_challenges(db: Session, user: User) -> list[Project]:
    rows = project_service.visible_projects(db, user, limit=200)
    order = {"open": 0, "active": 1, "approved": 2, "completed": 3}
    return sorted(
        [row for row in rows if row.state in order],
        key=lambda row: (order.get(row.state, 9), row.deadline),
    )


@router.get("/projects")
def challenges(
    request: Request,
    user: User = StudentUser,
    db: Session = Depends(get_db),
    page: str = "1",
):
    profile = student_profiles.get_or_create(db, user)
    projects = _visible_challenges(db, user)
    candidates = matching.candidate_pool(db, profile_ids=[profile.id])
    from core import matching as match_rules

    items = []
    for project in projects:
        _version, requirements = matching.requirements_for(db, project)
        match = None
        if requirements and candidates:
            match = match_rules.evaluate_candidate(
                matching.project_view(project), requirements, candidates[0]
            )
        items.append({"project": project, "match": match})
    paged = pagination.paginate(items, page=page, page_size=12)
    return render(
        request,
        "student/projects.html",
        active="/student/projects",
        profile=profile,
        items=paged.items,
        page_meta=paged,
    )


@router.get("/projects/{project_id}")
def challenge_detail(
    project_id: int,
    request: Request,
    user: User = StudentUser,
    db: Session = Depends(get_db),
):
    project = project_service.get_project(db, project_id)
    project_service.require_view(user, project)
    profile = student_profiles.get_or_create(db, user)
    result = matching.result_for_student(db, project, profile.id)
    if result is None:
        raise NotFound("That challenge has no requirements to match against yet.")
    _version, requirements = matching.requirements_for(db, project)
    team = db.scalar(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(
            Team.project_id == project.id,
            Team.state == "confirmed",
            TeamMember.student_id == profile.id,
        )
    )
    return render(
        request,
        "student/challenge.html",
        active="/student/projects",
        project=project,
        result=result,
        requirements=requirements,
        confirmed_team=team,
        snapshot=matching.latest_snapshot(db, project.id),
    )


# --- Passport -------------------------------------------------------------


def _passport_projects(db: Session, profile) -> list[Project]:
    rows = db.execute(
        select(Project.id)
        .join(Team, Team.project_id == Project.id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(Team.state == "confirmed", TeamMember.student_id == profile.id)
    ).all()
    project_ids = [row[0] for row in rows]
    if not project_ids:
        return []
    return list(
        db.scalars(select(Project).where(Project.id.in_(project_ids)).order_by(Project.title)).all()
    )


@router.get("/passport")
def passport_page(
    request: Request,
    user: User = StudentUser,
    db: Session = Depends(get_db),
):
    profile = student_profiles.get_or_create(db, user)
    entries = passports.for_student(db, profile)
    eligible = _passport_projects(db, profile)
    existing = {entry.project_id for entry in entries}
    return render(
        request,
        "student/passport.html",
        active="/student/passport",
        profile=profile,
        entries=entries,
        eligible=[project for project in eligible if project.id not in existing],
        saved=request.query_params.get("saved", ""),
        error=query_error(
            request,
            {
                "scope": "A passport entry needs the scope a mentor confirmed before it can be published.",
                "duplicate": "You already have a passport entry for that project.",
                "membership": "A passport entry needs a confirmed place on the project team.",
            },
        ),
    )


@router.post("/passport")
def create_passport(
    request: Request,
    project_id: str = Form(""),
    role: str = Form(""),
    technologies: str = Form(""),
    competencies: str = Form(""),
    verified_scope: str = Form(""),
    user: User = StudentUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    profile = student_profiles.get_or_create(db, user)
    try:
        project_id_int = int(project_id)
    except ValueError:
        return RedirectResponse("/student/passport?error=membership", status_code=303)
    try:
        passports.create(
            db,
            profile=profile,
            actor=user,
            project_id=project_id_int,
            role=role,
            technologies=[part.strip() for part in technologies.split(",")],
            competencies=[part.strip() for part in competencies.split("\n")],
            verified_scope=verified_scope,
            ip=ip,
        )
    except ValidationError:
        return RedirectResponse("/student/passport?error=membership", status_code=303)
    except Conflict:
        return RedirectResponse("/student/passport?error=duplicate", status_code=303)
    return RedirectResponse("/student/passport?saved=created", status_code=303)


@router.post("/passport/{entry_id}/publication")
def publish_passport(
    entry_id: int,
    request: Request,
    publish: str = Form("yes"),
    user: User = StudentUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    entry = passports.get(db, entry_id)
    try:
        passports.set_publication(
            db, entry=entry, actor=user, publish=publish == "yes", ip=ip
        )
    except ValidationError:
        return RedirectResponse("/student/passport?error=scope", status_code=303)
    return RedirectResponse("/student/passport?saved=published", status_code=303)


@router.get("/passport/{entry_id}/export")
def export_passport(
    entry_id: int,
    request: Request,
    user: User = StudentUser,
    db: Session = Depends(get_db),
):
    entry = passports.get(db, entry_id)
    passports.require_owner(user, entry)
    payload = passports.export_payload(entry)
    stem = f"indulink-passport-{entry.id}"
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{stem}.json"'},
    )


# --- Appeals and feedback received ---------------------------------------


@router.get("/appeals")
def appeals_page(
    request: Request,
    user: User = StudentUser,
    db: Session = Depends(get_db),
):
    profile = student_profiles.get_or_create(db, user)
    contributions = list(
        db.scalars(
            select(Contribution).where(Contribution.member_id == profile.id).order_by(Contribution.id.desc())
        ).all()
    )
    return render(
        request,
        "student/appeals.html",
        active="/student/appeals",
        profile=profile,
        appeals=engagement.appeals_for_student(db, profile),
        contributions=contributions,
        saved=request.query_params.get("saved", ""),
        error=query_error(
            request,
            {"statement": "Explain what should be reviewed in at least a sentence."},
        ),
    )


@router.post("/appeals")
def submit_appeal(
    request: Request,
    statement: str = Form(""),
    contribution_id: str = Form(""),
    user: User = StudentUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    profile = student_profiles.get_or_create(db, user)
    contribution = None
    if contribution_id.strip():
        try:
            contribution = int(contribution_id)
        except ValueError:
            contribution = None
    try:
        engagement.submit_appeal(
            db,
            profile=profile,
            actor=user,
            statement=statement,
            contribution_id=contribution,
            ip=ip,
        )
    except (ValidationError, NotFound):
        return RedirectResponse("/student/appeals?error=statement", status_code=303)
    return RedirectResponse("/student/appeals?saved=appeal", status_code=303)


@router.get("/feedback")
def feedback_received(
    request: Request,
    user: User = StudentUser,
    db: Session = Depends(get_db),
):
    return render(
        request,
        "student/feedback.html",
        active="/student/feedback",
        rows=engagement.feedback_feed(db, user),
    )
