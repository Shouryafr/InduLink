"""Template environment, shared context and role-aware navigation."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect as sa_inspect

from core import evidence_policy, freshness, requirements, skills
from data.demo_personas import DEMO_LOGINS, DEMO_PASSWORD
from data.models import User

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    """Stamp the newest static file mtime onto asset URLs.

    Without a changing URL, browsers keep serving a stale stylesheet after a
    deploy (static files carry no expiry, so they are cached heuristically).
    """
    newest = 0
    for path in STATIC_DIR.rglob("*"):
        if path.is_file():
            newest = max(newest, int(path.stat().st_mtime))
    return str(newest)


ASSET_VERSION = _asset_version()

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.autoescape = True  # never render user content as raw HTML

ROLE_LABELS = {
    "student": "Student",
    "mentor": "Mentor",
    "industry": "Industry partner",
    "admin": "University administrator",
}

PROFILE_HOME = {
    "student": "/student",
    "mentor": "/mentor",
    "industry": "/industry",
    "admin": "/admin",
}

#: Only routes that exist are listed. Later phases append their screens here.
NAV: dict[str, list[dict]] = {
    "student": [
        {"group": "Workspace", "label": "Overview", "href": "/student", "icon": "layout"},
        {"group": "Workspace", "label": "My profile and skills", "href": "/student/profile", "icon": "user"},
        {"group": "Workspace", "label": "My evidence", "href": "/student/evidence", "icon": "file-text"},
        {"group": "Matching", "label": "Challenges", "href": "/student/projects", "icon": "briefcase"},
        {"group": "Outcomes", "label": "Skill passport", "href": "/student/passport", "icon": "award"},
        {"group": "Outcomes", "label": "Appeals", "href": "/student/appeals", "icon": "alert-triangle"},
        {"group": "Outcomes", "label": "Feedback", "href": "/student/feedback", "icon": "message-square"},
    ],
    "mentor": [
        {"group": "Workspace", "label": "Overview", "href": "/mentor", "icon": "layout"},
        {"group": "Workspace", "label": "Mentor requests", "href": "/mentor/requests", "icon": "inbox"},
        {"group": "Registry", "label": "Expertise registry", "href": "/mentor/registry", "icon": "book"},
        {"group": "Registry", "label": "Evidence review", "href": "/mentor/evidence", "icon": "check-square"},
    ],
    "industry": [
        {"group": "Challenges", "label": "Overview", "href": "/industry", "icon": "layout"},
        {"group": "Challenges", "label": "My challenges", "href": "/industry/projects", "icon": "briefcase"},
        {"group": "Challenges", "label": "New challenge", "href": "/industry/projects/new", "icon": "plus"},
    ],
    "admin": [
        {"group": "Oversight", "label": "Overview", "href": "/admin", "icon": "layout"},
        {"group": "Oversight", "label": "Users", "href": "/admin/users", "icon": "users"},
        {"group": "Registry", "label": "Skill registry", "href": "/admin/skills", "icon": "book"},
        {"group": "Registry", "label": "Evidence oversight", "href": "/admin/evidence", "icon": "check-square"},
        {"group": "Assurance", "label": "Appeals", "href": "/admin/appeals", "icon": "alert-triangle"},
        {"group": "Assurance", "label": "Passports", "href": "/admin/passports", "icon": "award"},
        {"group": "Assurance", "label": "Audit log", "href": "/admin/audit", "icon": "shield"},
        {"group": "Demo", "label": "Demo controls", "href": "/admin/demo", "icon": "refresh"},
    ],
}


def _fmt_date(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d %b %Y").lstrip("0")
    return str(value)


def _fmt_datetime(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %H:%M").lstrip("0")
    return _fmt_date(value)


def _pct(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{round(float(value) * 100)}%"
    except (TypeError, ValueError):
        return "—"


def _level_word(level) -> str:
    if level is None:
        return "No data"
    names = {1: "Awareness", 2: "Assisted", 3: "Independent", 4: "Advanced", 5: "Expert"}
    if level in names:
        return f"{names[level]} · level {level}"
    return str(level)


def _initials(name: str) -> str:
    parts = [p for p in (name or "").replace(".", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


templates.env.filters["datefmt"] = _fmt_date
templates.env.filters["dtfmt"] = _fmt_datetime
templates.env.filters["pct"] = _pct
templates.env.filters["level_word"] = _level_word
templates.env.filters["initials"] = _initials
templates.env.globals.update(
    {
        "asset_v": ASSET_VERSION,
        "ROLE_LABELS": ROLE_LABELS,
        "EVIDENCE_CATEGORY_LABELS": evidence_policy.CATEGORY_LABELS,
        "EVIDENCE_STATUS_LABELS": evidence_policy.STATUS_LABELS,
        "PROJECT_STATES": {state.value: state.value.replace("_", " ").title() for state in requirements.ProjectState},
        "SKILL_CATEGORIES": skills.CATEGORIES,
        "demo_logins": DEMO_LOGINS,
        "demo_password": DEMO_PASSWORD,
    }
)


@dataclass(frozen=True)
class IdentityView:
    """Plain snapshot of the signed-in account, safe to render at any point.

    A 500 is rendered after the request session has been rolled back and closed,
    so the ORM instance can no longer be read. The middleware keeps this
    snapshot, letting the error page render with the same chrome instead of
    raising a second exception.
    """

    id: int | None = None
    role: str = ""
    display_name: str = ""
    email: str = ""


def _renderable_user(request: Request) -> User | IdentityView | None:
    user = getattr(request.state, "user", None)
    if user is None:
        return None
    try:
        if not sa_inspect(user).detached:
            return user
    except Exception:  # pragma: no cover - non-mapped stand-ins
        return user
    snapshot = getattr(request.state, "identity", None)
    return IdentityView(**snapshot) if snapshot else None


def base_context(request: Request, *, active: str = "", **extra) -> dict:
    user = _renderable_user(request)
    settings = getattr(request.app.state, "settings", None)
    nav = NAV.get(user.role, []) if user else []
    unread = 0
    session = getattr(request.state, "db", None)
    if user is not None and session is not None:
        try:
            from services import notifications

            unread = notifications.unread_count(session, user.id)
        except Exception:  # pragma: no cover - error pages render after a rollback
            unread = 0
    context = {
        "request": request,
        "user": user,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "nav": nav,
        "active_path": active or request.url.path,
        "role_label": ROLE_LABELS.get(user.role, "") if user else "",
        "home_path": PROFILE_HOME.get(user.role, "/") if user else "/",
        "demo_mode": bool(getattr(settings, "demo_mode", False)),
        "settings": settings,
        "unread_notifications": unread,
        "now": freshness.utcnow(),
    }
    context.update(extra)
    return context


def query_error(request: Request, messages: dict[str, str]) -> str:
    """Turn a short ``?error=`` code into the banner text for that screen.

    Several actions answer with a redirect so the browser cannot replay them;
    the code in the URL is what carries the failure back to the page.
    """
    return messages.get(request.query_params.get("error", ""), "")


def render(
    request: Request,
    template: str,
    *,
    status_code: int = 200,
    active: str = "",
    headers: dict | None = None,
    **extra,
) -> HTMLResponse:
    """Render a template with the shared context and explicit cache headers.

    Signed-in pages are personalised, so they default to ``no-store``.
    """
    context = base_context(request, active=active, **extra)
    response = templates.TemplateResponse(request, template, context, status_code=status_code)
    response.headers.setdefault("Cache-Control", "no-store")
    if headers:
        for key, value in headers.items():
            response.headers[key] = value
    return response
