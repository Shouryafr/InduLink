"""Mentor screens: workload, expertise registry, evidence review queue."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_db, get_ip, require_roles
from app.rendering import query_error, render
from core import evidence_policy
from data.models import Evidence, Mentor, MentorSkill, MentorUnavailability, Project, User
from services import evidence_registry, mentor_assignments, mentor_profiles, skill_registry
from services.errors import Conflict, ValidationError

router = APIRouter(prefix="/mentor", tags=["mentor"])
MentorUser = Depends(require_roles("mentor"))


def _profile(db: Session, user: User) -> Mentor:
    return mentor_profiles.get_or_create(db, user)


@router.get("")
def dashboard(request: Request, user: User = MentorUser, db: Session = Depends(get_db)):
    mentor = _profile(db, user)
    workload = mentor_profiles.workload(
        db, mentor, high_pct=request.app.state.settings.mentor_high_utilization_pct
    )
    queue = evidence_registry.review_queue(db, limit=8)
    unavailable = list(
        db.scalars(
            select(MentorUnavailability)
            .where(MentorUnavailability.mentor_id == mentor.id)
            .order_by(MentorUnavailability.from_date)
        ).all()
    )
    skills = mentor_profiles.skill_levels(db, mentor)
    return render(
        request,
        "mentor/dashboard.html",
        active="/mentor",
        mentor=mentor,
        workload=workload,
        queue=queue,
        unavailable=unavailable,
        skill_count=len(skills),
    )


@router.get("/registry")
def registry_page(request: Request, user: User = MentorUser, db: Session = Depends(get_db)):
    mentor = _profile(db, user)
    registry = skill_registry.load(db)
    entries = {
        row.skill_id: row
        for row in db.scalars(
            select(MentorSkill).where(MentorSkill.mentor_id == mentor.id)
        ).all()
    }
    unavailable = list(
        db.scalars(
            select(MentorUnavailability)
            .where(MentorUnavailability.mentor_id == mentor.id)
            .order_by(MentorUnavailability.from_date)
        ).all()
    )
    return render(
        request,
        "mentor/registry.html",
        active="/mentor/registry",
        mentor=mentor,
        registry=registry,
        entries=entries,
        unavailable=unavailable,
        workload=mentor_profiles.workload(
            db, mentor, high_pct=request.app.state.settings.mentor_high_utilization_pct
        ),
        saved=request.query_params.get("saved", ""),
        errors={},
        error=query_error(
            request,
            {
                "skill": (
                    "That expertise entry could not be saved. Pick a skill from the registry and "
                    "a level from 1 to 5."
                ),
                "dates": (
                    "That unavailability window was not saved. The start date must be on or "
                    "before the end date."
                ),
            },
        ),
    )


@router.post("/registry")
def save_registry(
    request: Request,
    domains: str = Form(""),
    experience: str = Form(""),
    mentoring_experience_years: str = Form(""),
    mentoring_projects_count: str = Form(""),
    max_active_projects: str = Form("3"),
    weekly_hours_cap: str = Form("10"),
    hours_per_project: str = Form("2"),
    preferred_levels: str = Form(""),
    user: User = MentorUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    mentor = _profile(db, user)
    errors: dict[str, list[str]] = {}

    def to_int(value: str, field: str) -> int | None:
        if not value.strip():
            return None
        try:
            return int(value)
        except ValueError:
            errors[field] = ["Enter a whole number."]
            return None

    years = to_int(mentoring_experience_years, "mentoring_experience_years")
    count = to_int(mentoring_projects_count, "mentoring_projects_count")
    max_projects = to_int(max_active_projects, "max_active_projects") or 0
    weekly_cap = to_int(weekly_hours_cap, "weekly_hours_cap") or 0
    per_project = to_int(hours_per_project, "hours_per_project")

    if not errors:
        try:
            mentor_profiles.update_registry(
                db,
                mentor=mentor,
                actor=user,
                domains=domains,
                experience=experience,
                mentoring_experience_years=years,
                mentoring_projects_count=count,
                max_active_projects=max_projects,
                weekly_hours_cap=weekly_cap,
                hours_per_project=per_project,
                preferred_levels=preferred_levels,
                ip=ip,
            )
        except ValidationError as exc:
            errors.update(exc.field_errors)

    if errors:
        entries = {
            row.skill_id: row
            for row in db.scalars(
                select(MentorSkill).where(MentorSkill.mentor_id == mentor.id)
            ).all()
        }
        return render(
            request,
            "mentor/registry.html",
            status_code=422,
            active="/mentor/registry",
            mentor=mentor,
            registry=skill_registry.load(db),
            entries=entries,
            unavailable=list(
                db.scalars(
                    select(MentorUnavailability).where(
                        MentorUnavailability.mentor_id == mentor.id
                    )
                ).all()
            ),
            workload=mentor_profiles.workload(
                db, mentor, high_pct=request.app.state.settings.mentor_high_utilization_pct
            ),
            errors=errors,
            error="Some registry details need attention.",
        )
    return RedirectResponse("/mentor/registry?saved=registry", status_code=303)


@router.post("/registry/skills")
def set_skill(
    request: Request,
    skill_id: str = Form(""),
    level: str = Form(""),
    evidence_basis: str = Form(""),
    user: User = MentorUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    mentor = _profile(db, user)
    if level.strip() == "remove":
        mentor_profiles.set_skill(
            db, mentor=mentor, skill_id=int(skill_id), level=None, actor=user, ip=ip
        )
        return RedirectResponse("/mentor/registry?saved=skill", status_code=303)
    try:
        mentor_profiles.set_skill(
            db,
            mentor=mentor,
            skill_id=int(skill_id),
            level=int(level),
            evidence_basis=evidence_basis,
            actor=user,
            ip=ip,
        )
    except (ValidationError, ValueError):
        return RedirectResponse("/mentor/registry?error=skill", status_code=303)
    return RedirectResponse("/mentor/registry?saved=skill", status_code=303)


@router.post("/registry/unavailability")
def add_unavailability(
    request: Request,
    from_date: str = Form(""),
    to_date: str = Form(""),
    reason: str = Form(""),
    user: User = MentorUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    mentor = _profile(db, user)
    try:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
    except ValueError:
        return RedirectResponse("/mentor/registry?error=dates", status_code=303)
    try:
        mentor_profiles.add_unavailability(
            db,
            mentor=mentor,
            from_date=start,
            to_date=end,
            reason=reason,
            actor=user,
            ip=ip,
        )
    except ValidationError:
        return RedirectResponse("/mentor/registry?error=dates", status_code=303)
    return RedirectResponse("/mentor/registry?saved=unavailable", status_code=303)


@router.get("/evidence")
def evidence_queue(
    request: Request,
    user: User = MentorUser,
    db: Session = Depends(get_db),
    status: str = "open",
):
    mentor = _profile(db, user)
    if status == "mine":
        rows = list(
            db.scalars(
                select(Evidence)
                .where(Evidence.reviewer_id == user.id)
                .order_by(Evidence.reviewed_at.desc().nullslast(), Evidence.id.desc())
                .limit(100)
            ).all()
        )
    else:
        rows = evidence_registry.review_queue(db, limit=100)

    registry = skill_registry.load(db)
    items = []
    for row in rows:
        context = evidence_registry.context_for(row)
        context["skill_name"] = registry.name_of(row.skill_id)
        context["student_name"] = row.student.user.display_name if row.student and row.student.user else "—"
        context["own_item"] = row.student.user_id == user.id if row.student else False
        items.append(context)

    return render(
        request,
        "mentor/evidence.html",
        active="/mentor/evidence",
        mentor=mentor,
        items=items,
        tab=status,
        categories=evidence_policy.EVIDENCE_CATEGORIES,
        saved=request.query_params.get("saved", ""),
        error=query_error(
            request,
            {
                "level": "The assessed level must be a whole number from 1 to 5.",
                "review": (
                    "That review was not saved. You cannot review your own evidence or "
                    "re-review a closed item."
                ),
            },
        ),
    )


@router.post("/evidence/{evidence_id}/review")
def review_evidence(
    evidence_id: int,
    request: Request,
    decision: str = Form("reviewed"),
    assessed_level: str = Form(""),
    notes: str = Form(""),
    user: User = MentorUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    row = evidence_registry.get(db, evidence_id)
    level = None
    if assessed_level.strip():
        try:
            level = int(assessed_level)
        except ValueError:
            return RedirectResponse("/mentor/evidence?error=level", status_code=303)
    try:
        evidence_registry.review(
            db,
            evidence=row,
            reviewer=user,
            decision=decision,
            assessed_level=level,
            notes=notes,
            ip=ip,
        )
    except ValidationError:
        return RedirectResponse("/mentor/evidence?error=review", status_code=303)
    return RedirectResponse("/mentor/evidence?saved=reviewed", status_code=303)


@router.get("/requests")
def mentor_requests(
    request: Request,
    user: User = MentorUser,
    db: Session = Depends(get_db),
):
    mentor = _profile(db, user)
    rows = mentor_assignments.requests_for_mentor(db, mentor.id)
    projects = {
        project.id: project
        for project in db.scalars(
            select(Project).where(Project.id.in_([row.project_id for row in rows]))
        ).all()
    } if rows else {}
    return render(
        request,
        "mentor/requests.html",
        active="/mentor/requests",
        mentor=mentor,
        assignments=rows,
        projects=projects,
        workload=mentor_profiles.workload(
            db, mentor, high_pct=request.app.state.settings.mentor_high_utilization_pct
        ),
        saved=request.query_params.get("saved", ""),
        error=query_error(
            request,
            {
                "decision": (
                    "That decision was not saved. The request may already be closed, or confirming "
                    "would take you beyond your stated capacity."
                )
            },
        ),
    )


@router.post("/requests/{assignment_id}/decide")
def decide_request(
    assignment_id: int,
    request: Request,
    decision: str = Form("accept"),
    reason: str = Form(""),
    user: User = MentorUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    assignment = mentor_assignments.get(db, assignment_id)
    try:
        mentor_assignments.decide(
            db,
            assignment=assignment,
            actor=user,
            accept=decision == "accept",
            reason=reason,
            ip=ip,
        )
    except Conflict:
        return RedirectResponse("/mentor/requests?error=decision", status_code=303)
    return RedirectResponse("/mentor/requests?saved=decided", status_code=303)
