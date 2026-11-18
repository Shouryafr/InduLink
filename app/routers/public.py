"""Signed-out screens: landing, how it works, terms, privacy, sign-in, register."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_db, get_ip, require_user
from app.rendering import PROFILE_HOME, render
from data.models import User
from services import audit_log, authentication
from services.errors import Conflict, ValidationError

router = APIRouter()


def _safe_next(next_url: str | None) -> str | None:
    """Only allow an internal path as a post-sign-in destination."""
    if not next_url:
        return None
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc or not next_url.startswith("/"):
        return None
    if next_url.startswith("//"):
        return None
    return next_url


def home_for(user: User) -> str:
    return PROFILE_HOME.get(user.role, "/")


@router.get("/")
def landing(request: Request):
    user = getattr(request.state, "user", None)
    return render(request, "public/landing.html", active="/")


@router.get("/how-it-works")
def how_it_works(request: Request):
    return render(request, "public/how_it_works.html", active="/how-it-works")


@router.get("/how-recommendations-work")
def how_recommendations_work(request: Request):
    return render(request, "public/how_it_works.html", active="/how-it-works")


@router.get("/privacy")
def privacy(request: Request):
    return render(request, "public/privacy.html", active="/privacy")


@router.get("/terms")
def terms(request: Request):
    return render(request, "public/terms.html", active="/terms")


@router.get("/search")
def search(
    request: Request,
    q: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Role-aware search.

    Results are filtered by the same visibility functions the detail pages use, so
    a search can never surface a record the viewer would be refused on opening.
    """
    from sqlalchemy import select

    from data.models import Project, Skill, StudentProfile
    from services import projects as project_service
    from services import skill_registry, student_profiles

    needle = (q or "").strip()
    projects: list = []
    people: list = []
    skills: list = []

    if needle:
        lowered = needle.lower()
        registry = skill_registry.load(db)
        skills = [
            skill
            for skill in registry.items
            if lowered in skill.canonical_name.lower()
        ][:8]

        for project in db.scalars(select(Project).limit(400)).all():
            if not project_service.can_view(user, project):
                continue
            haystack = f"{project.title} {project.domain} {project.description}".lower()
            if lowered in haystack:
                projects.append(project)
        projects = projects[:12]

        if user is not None and user.role in {"admin", "mentor", "industry"}:
            for profile in db.scalars(select(StudentProfile).limit(400)).all():
                if not student_profiles.can_view(user, profile):
                    continue
                name = profile.user.display_name if profile.user else ""
                haystack = f"{name} {profile.headline} {profile.preferred_roles}".lower()
                if lowered in haystack:
                    people.append(profile)
            people = people[:8]

    return render(
        request,
        "public/search.html",
        active="/search",
        query=needle,
        projects=projects,
        people=people,
        skills=skills,
    )


@router.get("/sign-in")
def sign_in_form(request: Request, next: str | None = None):
    user = getattr(request.state, "user", None)
    if user is not None:
        return RedirectResponse(home_for(user), status_code=303)
    return render(request, "public/sign_in.html", active="/sign-in", next_url=_safe_next(next) or "", email="")


@router.post("/sign-in")
def sign_in_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    settings = request.app.state.settings
    result = authentication.authenticate(
        db, email=email, password=password, settings=settings, ip=ip
    )
    if not result.ok:
        return render(
            request,
            "public/sign_in.html",
            status_code=400,
            active="/sign-in",
            error=result.message,
            # Never echo the attempted address: an unknown account and a wrong
            # password must look identical in the response body.
            email="",
            next_url=_safe_next(next) or "",
        )

    token = authentication.create_session(
        db,
        user=result.user,
        settings=settings,
        ip=ip,
        user_agent=request.headers.get("user-agent", ""),
    )
    destination = _safe_next(next) or home_for(result.user)
    response = RedirectResponse(destination, status_code=303)
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


@router.post("/sign-out")
def sign_out(
    request: Request,
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    row = getattr(request.state, "session_row", None)
    user = getattr(request.state, "user", None)
    if row is not None:
        authentication.revoke_session(db, row, reason="logout")
        audit_log.record(
            db,
            actor=user,
            action="auth.sign_out",
            target_type="user",
            target_id=getattr(user, "id", ""),
            ip=ip,
        )
    response = RedirectResponse("/sign-in", status_code=303)
    response.delete_cookie(authentication.SESSION_COOKIE, path="/")
    return response


@router.get("/register")
def register_form(request: Request, role: str = "student"):
    user = getattr(request.state, "user", None)
    if user is not None:
        return RedirectResponse(home_for(user), status_code=303)
    if role not in {"student", "mentor", "industry"}:
        role = "student"
    return render(request, "public/register.html", active="/register", form_role=role, form={})


@router.post("/register")
def register_submit(
    request: Request,
    display_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    role: str = Form("student"),
    accepted_terms: str = Form(""),
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    settings = request.app.state.settings
    form = {"display_name": display_name, "email": email, "role": role}

    def back(errors: dict[str, list[str]], message: str, status: int = 422):
        return render(
            request,
            "public/register.html",
            status_code=status,
            active="/register",
            form_role=role if role in {"student", "mentor", "industry"} else "student",
            form=form,
            errors=errors,
            error=message,
        )

    if password != confirm_password:
        return back({"confirm_password": ["The two passwords do not match."]}, "Check the highlighted fields.")
    if not accepted_terms:
        return back(
            {"accepted_terms": ["Accept the terms to create an account."]},
            "Check the highlighted fields.",
        )

    try:
        user = authentication.register_user(
            db,
            email=email,
            password=password,
            role=role,
            display_name=display_name,
            settings=settings,
            ip=ip,
        )
    except ValidationError as exc:
        return back(exc.field_errors, exc.message, 422)
    except Conflict as exc:
        return back(exc.field_errors, exc.message, 409)

    # Create the profile row the role needs, so the next screen is never empty.
    if user.role == "student":
        from services import student_profiles

        student_profiles.get_or_create(db, user)
    elif user.role == "mentor":
        from services import mentor_profiles

        mentor_profiles.get_or_create(db, user)

    token = authentication.create_session(
        db,
        user=user,
        settings=settings,
        ip=ip,
        user_agent=request.headers.get("user-agent", ""),
    )
    response = RedirectResponse(home_for(user), status_code=303)
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


@router.get("/forgot-password")
def forgot_password_form(request: Request):
    return render(request, "public/forgot_password.html", active="/sign-in", sent=False)


@router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    email: str = Form(""),
    db: Session = Depends(get_db),
    ip: str = Depends(get_ip),
):
    """Record the request without revealing whether the address exists.

    No e-mail is sent in this build; delivery is listed as a residual risk.
    """
    audit_log.record(
        db,
        actor=None,
        action="auth.password_reset_requested",
        target_type="email",
        target_id=email.strip().lower()[:254],
        ip=ip,
    )
    return render(request, "public/forgot_password.html", active="/sign-in", sent=True, email=email)
