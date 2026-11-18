"""Industry partner screens: challenges, the creation wizard, requirement versions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_db, get_ip, require_roles
from app.middleware import new_form_token
from app.rendering import query_error, render
from core import requirements as req_rules
from data.models import Mentor, Project, ProjectVersion, User
from services import engagement, matching, mentor_assignments, skill_registry
from services import projects as project_service
from services import teams as team_service
from services import whatif
from services.errors import Conflict, NotFound, ValidationError

router = APIRouter(prefix="/industry", tags=["industry"])
IndustryUser = Depends(require_roles("industry", "admin"))


def _own_projects(db: Session, user: User) -> list[Project]:
    query = select(Project).order_by(Project.updated_at.desc())
    if user.role != "admin":
        query = query.where(Project.owner_id == user.id)
    return list(db.scalars(query).all())


@router.get("")
def dashboard(request: Request, user: User = IndustryUser, db: Session = Depends(get_db)):
    rows = _own_projects(db, user)
    by_state: dict[str, int] = {}
    for project in rows:
        by_state[project.state] = by_state.get(project.state, 0) + 1
    return render(
        request,
        "industry/dashboard.html",
        active="/industry",
        projects=rows[:6],
        total=len(rows),
        by_state=by_state,
        token=new_form_token(),
    )


@router.get("/projects")
def projects_list(request: Request, user: User = IndustryUser, db: Session = Depends(get_db)):
    rows = _own_projects(db, user)
    counts = {
        project.id: len(
            [
                version
                for version in db.scalars(
                    select(ProjectVersion).where(ProjectVersion.project_id == project.id)
                ).all()
            ]
        )
        for project in rows
    }
    return render(
        request,
        "industry/projects.html",
        active="/industry/projects",
        projects=rows,
        version_counts=counts,
        token=new_form_token(),
    )


@router.get("/projects/new")
def new_project(request: Request, user: User = IndustryUser, db: Session = Depends(get_db)):
    registry = skill_registry.load(db)
    return render(
        request,
        "industry/new_project.html",
        active="/industry/projects/new",
        registry=registry,
        token=new_form_token(),
        form={},
        errors={},
        rows=[{"skill_id": "", "required_level": "3", "weight": "3", "mandatory": "yes"}],
    )


def _read_requirement_rows(form, registry) -> tuple[list, dict[str, list[str]]]:
    skill_ids = form.getlist("skill_id")
    levels = form.getlist("required_level")
    weights = form.getlist("weight")
    mandatories = form.getlist("mandatory")
    errors: dict[str, list[str]] = {}
    drafts: list[req_rules.RequirementDraft] = []

    if not (len(skill_ids) == len(levels) == len(weights) == len(mandatories)):
        return [], {"requirements": ["The requirement rows could not be read. Reload and try again."]}

    for index, raw_skill in enumerate(skill_ids):
        if not str(raw_skill).strip():
            # A blank row at the end is ignored rather than treated as an error.
            if index == len(skill_ids) - 1:
                continue
            errors[f"requirement_{index}"] = ["Choose a skill for this row."]
            continue
        try:
            skill_id = int(raw_skill)
        except ValueError:
            errors[f"requirement_{index}"] = ["Choose a skill from the registry."]
            continue
        skill = registry.get(skill_id)
        if skill is None:
            errors[f"requirement_{index}"] = ["That skill is not in the registry."]
            continue
        try:
            level = int(levels[index])
            weight = float(weights[index])
        except (TypeError, ValueError):
            errors[f"requirement_{index}"] = ["Enter a level and an importance value."]
            continue
        drafts.append(
            req_rules.RequirementDraft(
                skill_id=skill_id,
                required_level=level,
                weight_raw=weight,
                is_mandatory=str(mandatories[index]).lower() in {"yes", "true", "1", "on"},
            )
        )
    return drafts, errors


@router.post("/projects/new")
async def create_project(
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    form = await request.form()
    registry = skill_registry.load(db)
    drafts, errors = _read_requirement_rows(form, registry)

    raw_form = {
        "title": form.get("title", ""),
        "domain": form.get("domain", ""),
        "description": form.get("description", ""),
        "difficulty": form.get("difficulty", "3"),
        "team_size_min": form.get("team_size_min", "2"),
        "team_size_max": form.get("team_size_max", "4"),
        "start_date": form.get("start_date", ""),
        "deadline": form.get("deadline", ""),
        "visibility": form.get("visibility", "private"),
        "eligibility_note": form.get("eligibility_note", ""),
        "evaluation_criteria": form.get("evaluation_criteria", ""),
        "deliverables_summary": form.get("deliverables_summary", ""),
        "form_token": form.get("form_token", ""),
    }

    def back(current_errors: dict[str, list[str]], message: str):
        rows = [
            {
                "skill_id": str(form.getlist("skill_id")[i]) if i < len(form.getlist("skill_id")) else "",
                "required_level": form.getlist("required_level")[i] if i < len(form.getlist("required_level")) else "3",
                "weight": form.getlist("weight")[i] if i < len(form.getlist("weight")) else "3",
                "mandatory": form.getlist("mandatory")[i] if i < len(form.getlist("mandatory")) else "yes",
            }
            for i in range(max(len(form.getlist("skill_id")), 1))
        ]
        return render(
            request,
            "industry/new_project.html",
            status_code=422,
            active="/industry/projects/new",
            registry=registry,
            token=raw_form["form_token"] or new_form_token(),
            form=raw_form,
            rows=rows,
            errors=current_errors,
            error=message,
        )

    if errors:
        return back(errors, "The requirements need attention.")

    try:
        project = project_service.create_project(
            db,
            owner=user,
            title=raw_form["title"],
            domain=raw_form["domain"],
            description=raw_form["description"],
            difficulty=raw_form["difficulty"],
            team_size_min=raw_form["team_size_min"],
            team_size_max=raw_form["team_size_max"],
            start_date=raw_form["start_date"],
            deadline=raw_form["deadline"],
            visibility=raw_form["visibility"],
            requirement_drafts=drafts,
            actor=user,
            eligibility_note=raw_form["eligibility_note"],
            evaluation_criteria=raw_form["evaluation_criteria"],
            deliverables_summary=raw_form["deliverables_summary"],
            form_token=raw_form["form_token"] or None,
            ip=ip,
        )
    except ValidationError as exc:
        return back(exc.field_errors, exc.message)

    return RedirectResponse(f"/industry/projects/{project.id}?saved=created", status_code=303)


def _load_project(db: Session, project_id: int, user: User, ip: str) -> Project:
    project = project_service.get_project(db, project_id)
    project_service.require_view(user, project)
    return project


@router.get("/projects/{project_id}")
def project_detail(
    project_id: int,
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    project = _load_project(db, project_id, user, ip)
    current = project_service.current_version(db, project.id)
    requirements = project_service.version_requirements(db, current.id) if current else []
    inputs = project_service.to_inputs(db, requirements)
    percentages = req_rules.as_percentages([item.weight for item in inputs])
    versions = list(
        db.scalars(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project.id)
            .order_by(ProjectVersion.version_no.desc())
        ).all()
    )
    return render(
        request,
        "industry/project.html",
        active="/industry/projects",
        project=project,
        version=current,
        requirements=inputs,
        percentages=percentages,
        versions=versions,
        next_states=req_rules.allowed_next_states(project.state),
        registry=skill_registry.load(db),
        token=new_form_token(),
        saved=request.query_params.get("saved", ""),
        stamp=project_service.snapshot_stamp(current),
        error=query_error(
            request,
            {
                "requirements": (
                    "Those requirements were not saved. Record why they changed, keep at least "
                    "one requirement, and give the set a total weight above zero."
                )
            },
        ),
    )


@router.get("/projects/{project_id}/versions/{version_no}")
def project_version(
    project_id: int,
    version_no: int,
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    project = _load_project(db, project_id, user, ip)
    versions = list(
        db.scalars(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project.id)
            .order_by(ProjectVersion.version_no)
        ).all()
    )
    version = next((row for row in versions if row.version_no == version_no), None)
    from services.errors import NotFound

    if version is None:
        raise NotFound("That version does not exist.")
    requirements = project_service.version_requirements(db, version.id)
    inputs = project_service.to_inputs(db, requirements)
    previous = next((row for row in versions if row.version_no == version_no - 1), None)
    return render(
        request,
        "industry/version.html",
        active="/industry/projects",
        project=project,
        version=version,
        requirements=inputs,
        percentages=req_rules.as_percentages([item.weight for item in inputs]),
        diff=project_service.version_diff(previous, version) if previous else None,
        is_current=version.is_current,
        versions=versions,
    )


@router.post("/projects/{project_id}/state")
def change_state(
    project_id: int,
    request: Request,
    target: str = Form(""),
    reason: str = Form(""),
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    project = _load_project(db, project_id, user, ip)
    project_service.transition(db, project=project, target=target, actor=user, reason=reason, ip=ip)
    return RedirectResponse(f"/industry/projects/{project.id}?saved=state", status_code=303)


@router.post("/projects/{project_id}/requirements")
async def edit_requirements(
    project_id: int,
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    """Editing requirements writes a new version; the old one stays readable."""
    project = _load_project(db, project_id, user, ip)
    form = await request.form()
    registry = skill_registry.load(db)
    drafts, errors = _read_requirement_rows(form, registry)
    reason = str(form.get("reason", ""))

    if errors:
        current = project_service.current_version(db, project.id)
        requirements = project_service.version_requirements(db, current.id) if current else []
        inputs = project_service.to_inputs(db, requirements)
        return render(
            request,
            "industry/project.html",
            status_code=422,
            active="/industry/projects",
            project=project,
            version=current,
            requirements=inputs,
            percentages=req_rules.as_percentages([item.weight for item in inputs]),
            versions=list(
                db.scalars(
                    select(ProjectVersion)
                    .where(ProjectVersion.project_id == project.id)
                    .order_by(ProjectVersion.version_no.desc())
                ).all()
            ),
            next_states=req_rules.allowed_next_states(project.state),
            registry=registry,
            token=new_form_token(),
            errors=errors,
            error="The requirements need attention.",
            stamp=project_service.snapshot_stamp(current),
        )

    try:
        project_service.edit_requirements(
            db, project=project, actor=user, requirement_drafts=drafts, reason=reason, ip=ip
        )
    except ValidationError as exc:
        return RedirectResponse(
            f"/industry/projects/{project.id}?error=requirements", status_code=303
        )
    return RedirectResponse(f"/industry/projects/{project.id}?saved=version", status_code=303)


# --- Matching, teams, mentors, What-If and outcomes -----------------------


def _guard_matching(db: Session, project_id: int, user: User) -> Project:
    project = project_service.get_project(db, project_id)
    matching.require_partner_access(user, project)
    return project


@router.get("/projects/{project_id}/candidates")
def candidates(
    project_id: int,
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
):
    project = _guard_matching(db, project_id, user)
    results = matching.rank_project(db, project, viewer_role=user.role)
    snapshot = matching.latest_snapshot(db, project.id)
    visible = [result for result in results if result.eligibility != "not_eligible"]
    return render(
        request,
        "industry/candidates.html",
        active="/industry/projects",
        project=project,
        results=results,
        eligible_count=len(visible),
        tied_count=sum(1 for result in results if result.tied),
        snapshot=snapshot,
        version=matching.requirements_for(db, project)[0],
    )


@router.get("/projects/{project_id}/team")
def team_page(
    project_id: int,
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
):
    project = _guard_matching(db, project_id, user)
    suggestion, ranked = team_service.suggest(db, project, viewer_role=user.role)
    from core import matching as match_rules

    return render(
        request,
        "industry/team.html",
        active="/industry/projects",
        project=project,
        suggestion=suggestion,
        alternatives=match_rules.best_alternatives(ranked, suggestion.members, limit=5),
        teams=team_service.teams_for(db, project.id),
        confirmed=team_service.confirmed_team(db, project.id),
        token=new_form_token(),
        saved=request.query_params.get("saved", ""),
        error=query_error(
            request,
            {
                "members": (
                    "That team breaks a hard constraint, so it was not confirmed. The message on "
                    "this page names the constraint."
                ),
                "exists": "This challenge already has a confirmed team.",
            },
        ),
    )


@router.post("/projects/{project_id}/team")
async def confirm_team(
    project_id: int,
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    project = _guard_matching(db, project_id, user)
    form = await request.form()
    student_ids: list[int] = []
    for raw in form.getlist("member"):
        try:
            student_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    try:
        team_service.confirm(
            db,
            project=project,
            actor=user,
            student_ids=student_ids,
            label=str(form.get("label", "")),
            rationale=str(form.get("rationale", "")),
            ip=ip,
        )
    except Conflict:
        return RedirectResponse(
            f"/industry/projects/{project.id}/team?error=exists", status_code=303
        )
    except ValidationError:
        return RedirectResponse(
            f"/industry/projects/{project.id}/team?error=members", status_code=303
        )
    return RedirectResponse(f"/industry/projects/{project.id}/team?saved=team", status_code=303)


@router.post("/projects/{project_id}/team/dissolve")
def dissolve_team(
    project_id: int,
    request: Request,
    reason: str = Form(""),
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    project = _guard_matching(db, project_id, user)
    team = team_service.confirmed_team(db, project.id)
    if team is None:
        raise NotFound("This challenge has no confirmed team to dissolve.")
    try:
        team_service.dissolve(db, team=team, actor=user, reason=reason, ip=ip)
    except ValidationError:
        return RedirectResponse(f"/industry/projects/{project.id}/team?error=members", status_code=303)
    return RedirectResponse(f"/industry/projects/{project.id}/team?saved=dissolved", status_code=303)


@router.get("/projects/{project_id}/mentor")
def mentor_page(
    project_id: int,
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
):
    project = _guard_matching(db, project_id, user)
    fits = mentor_assignments.fit_for_project(
        db, project, high_pct=request.app.state.settings.mentor_high_utilization_pct
    )
    assignments = mentor_assignments.assignments_for_project(db, project.id)
    mentor_names = {
        mentor.id: mentor.user.display_name if mentor.user else f"Mentor {mentor.id}"
        for mentor in db.scalars(select(Mentor)).all()
    }
    return render(
        request,
        "industry/mentor.html",
        active="/industry/projects",
        project=project,
        fits=fits,
        assignments=assignments,
        mentor_names=mentor_names,
        confirmed=mentor_assignments.confirmed_for_project(db, project.id),
        saved=request.query_params.get("saved", ""),
        error=query_error(
            request,
            {
                "request": (
                    "That mentor could not be requested. They may already have a record for this "
                    "challenge, be unavailable for the window, or the slot may be taken."
                )
            },
        ),
    )


@router.post("/projects/{project_id}/mentor")
def request_mentor(
    project_id: int,
    request: Request,
    mentor_id: str = Form(""),
    reason: str = Form(""),
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    project = _guard_matching(db, project_id, user)
    try:
        mentor = db.get(Mentor, int(mentor_id))
    except (TypeError, ValueError):
        mentor = None
    if mentor is None:
        raise NotFound("That mentor does not exist.")
    try:
        mentor_assignments.request(
            db, project=project, mentor=mentor, actor=user, reason=reason, ip=ip
        )
    except Conflict:
        return RedirectResponse(
            f"/industry/projects/{project.id}/mentor?error=request", status_code=303
        )
    return RedirectResponse(f"/industry/projects/{project.id}/mentor?saved=requested", status_code=303)


@router.get("/projects/{project_id}/what-if")
def what_if_page(
    project_id: int,
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
):
    project = _guard_matching(db, project_id, user)
    _version, requirements = matching.requirements_for(db, project)
    scenario_id = request.query_params.get("scenario", "")
    scenario = None
    outcome = None
    if scenario_id:
        scenario = whatif.get(db, int(scenario_id)) if scenario_id.isdigit() else None
        if scenario is not None and (
            scenario.created_by == user.id or user.role == "admin"
        ):
            outcome = scenario.results
    return render(
        request,
        "industry/whatif.html",
        active="/industry/projects",
        project=project,
        requirements=requirements,
        outcome=outcome,
        scenario=scenario,
        history=whatif.recent(db, project.id),
        error=query_error(
            request,
            {
                "changes": (
                    "That simulation was not applied. Check the level, the requirement and the "
                    "team size, then try again."
                )
            },
        ),
    )


@router.post("/projects/{project_id}/what-if")
async def run_what_if(
    project_id: int,
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    project = _guard_matching(db, project_id, user)
    form = await request.form()
    try:
        outcome = whatif.run(
            db,
            project=project,
            actor=user,
            changes={key: form.get(key, "") for key in ("skill_id", "new_level", "mandatory", "team_size_max", "exclude_student_id")},
            label=str(form.get("label", "")),
            ip=ip,
        )
    except (ValidationError, Conflict):
        return RedirectResponse(
            f"/industry/projects/{project.id}/what-if?error=changes", status_code=303
        )
    return RedirectResponse(
        f"/industry/projects/{project.id}/what-if?scenario={outcome.scenario.id}",
        status_code=303,
    )


@router.get("/projects/{project_id}/review")
def review_page(
    project_id: int,
    request: Request,
    user: User = IndustryUser,
    db: Session = Depends(get_db),
):
    project = _guard_matching(db, project_id, user)
    members = engagement.team_members_for_project(db, project.id)
    return render(
        request,
        "industry/review.html",
        active="/industry/projects",
        project=project,
        members=members,
        evaluations=engagement.evaluations_for(db, user, project.id),
        feedback=engagement.feedback_feed(db, user),
        saved=request.query_params.get("saved", ""),
        error=query_error(
            request,
            {
                "evaluation": "The evaluation needs a summary and ratings from 1 to 5.",
                "feedback": "The feedback needs a comment and a rating from 1 to 5.",
            },
        ),
    )


@router.post("/projects/{project_id}/review/evaluation")
def record_evaluation(
    project_id: int,
    request: Request,
    summary: str = Form(""),
    outcome: str = Form("met"),
    confidential: str = Form("yes"),
    delivery: str = Form(""),
    quality: str = Form(""),
    communication: str = Form(""),
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    project = _guard_matching(db, project_id, user)
    try:
        engagement.record_evaluation(
            db,
            reviewer=user,
            project_id=project.id,
            scores={"delivery": delivery, "quality": quality, "communication": communication},
            summary=summary,
            outcome=outcome,
            confidential=confidential == "yes",
            ip=ip,
        )
    except ValidationError:
        return RedirectResponse(
            f"/industry/projects/{project.id}/review?error=evaluation", status_code=303
        )
    return RedirectResponse(f"/industry/projects/{project.id}/review?saved=evaluation", status_code=303)


@router.post("/projects/{project_id}/review/feedback")
def record_feedback(
    project_id: int,
    request: Request,
    subject_user_id: str = Form(""),
    comments: str = Form(""),
    clarity: str = Form(""),
    reliability: str = Form(""),
    collaboration: str = Form(""),
    confidential: str = Form("yes"),
    user: User = IndustryUser,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    project = _guard_matching(db, project_id, user)
    try:
        engagement.submit_feedback(
            db,
            author=user,
            project_id=project.id,
            subject_user_id=int(subject_user_id) if subject_user_id.isdigit() else None,
            ratings={"clarity": clarity, "reliability": reliability, "collaboration": collaboration},
            comments=comments,
            confidential=confidential == "yes",
            ip=ip,
        )
    except ValidationError:
        return RedirectResponse(
            f"/industry/projects/{project.id}/review?error=feedback", status_code=303
        )
    return RedirectResponse(f"/industry/projects/{project.id}/review?saved=feedback", status_code=303)
