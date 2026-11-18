"""Application factory, middleware wiring and error handling."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.middleware import (
    CSRFMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.rendering import render
from app.routers import admin, industry, mentor, notifications, profiles, public, student
from config import Settings
from data.database import Database
from services import audit_log
from services.errors import AuthRequired, DomainError

logger = logging.getLogger("indulink")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    logging.basicConfig(
        level=logging.INFO if settings.is_production else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = FastAPI(
        title="InduLink",
        description=(
            "Explainable academia-industry matching. Decision support, not a guarantee "
            "of ability, success or employability."
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.db = Database(settings)

    # Innermost first: the last middleware added is the outermost wrapper.
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)
    app.add_middleware(
        CSRFMiddleware,
        secret_key=settings.secret_key,
        cookie_secure=settings.cookie_secure,
    )
    app.add_middleware(RequestContextMiddleware, database=app.state.db, settings=settings)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(public.router)
    app.include_router(profiles.router)
    app.include_router(notifications.router)
    app.include_router(student.router)
    app.include_router(mentor.router)
    app.include_router(industry.router)
    app.include_router(admin.router)

    _register_error_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        report = app.state.db.pragma_report()
        return JSONResponse({"status": "ok", "pragmas": report})

    @app.on_event("startup")
    def _startup_report() -> None:
        try:
            report = app.state.db.pragma_report()
            logger.warning(
                "InduLink started. foreign_keys=%s journal_mode=%s demo_mode=%s",
                report["foreign_keys"],
                report["journal_mode"],
                settings.demo_mode,
            )
        except Exception:  # pragma: no cover - a missing schema is reported by make demo
            logger.warning("Could not read database pragmas; has the schema been migrated?")

    return app


class _ActorRef:
    """Identity captured before the request transaction is discarded."""

    __slots__ = ("id", "role")

    def __init__(self, actor_id: int | None, role: str) -> None:
        self.id = actor_id
        self.role = role


def _audit_denial(request: Request, exc: DomainError) -> None:
    """Record a denial, discarding the refused request's partial work first.

    The denied request already holds SQLite's single writer slot, so a second
    connection would answer ``database is locked``. Rolling the request session
    back both drops any half-finished work and frees the write lock, letting the
    security event be committed on the same connection. Identity is read before
    the rollback, because that expires the loaded user row.
    """
    session: Session | None = getattr(request.state, "db", None)
    if session is None:  # pragma: no cover - middleware always provides one
        return
    user = getattr(request.state, "user", None)
    if user is None and exc.status_code == 401:
        # A signed-out request for a private URL is simply redirected. There is
        # no actor to hold accountable, and writing a row per bounce would let
        # anonymous traffic flood the single-writer database.
        return
    actor = _ActorRef(getattr(user, "id", None), getattr(user, "role", None) or "anonymous")
    try:
        session.rollback()
        audit_log.record(
            session,
            actor=actor,
            action=getattr(exc, "action", "authz.denied"),
            target_type=exc.target_type or "route",
            target_id=exc.target_id or request.url.path,
            reason=exc.reason or exc.message[:200],
            ip=getattr(request.state, "ip", ""),
        )
        session.commit()
    except Exception:  # pragma: no cover - never mask the original response
        session.rollback()
        logger.exception("Could not record denial audit event")


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthRequired)
    def _auth_required(request: Request, exc: AuthRequired):
        _audit_denial(request, exc)
        target = request.url.path
        suffix = f"?next={target}" if target not in {"/", ""} else ""
        return RedirectResponse(f"/sign-in{suffix}", status_code=303)

    @app.exception_handler(DomainError)
    def _domain_error(request: Request, exc: DomainError):
        if exc.status_code in {401, 403}:
            _audit_denial(request, exc)
        template = "errors/error.html"
        return render(
            request,
            template,
            status_code=exc.status_code,
            code=exc.status_code,
            title=exc.title,
            message=exc.message,
            field_errors=exc.field_errors,
        )

    @app.exception_handler(RequestValidationError)
    def _validation_error(request: Request, exc: RequestValidationError):
        return render(
            request,
            "errors/error.html",
            status_code=422,
            code=422,
            title="Check the request",
            message="Some of the submitted values were not in an expected format.",
            field_errors={},
        )

    @app.exception_handler(StarletteHTTPException)
    def _http_error(request: Request, exc: StarletteHTTPException):
        titles = {
            403: "You do not have access to this",
            404: "We could not find that page",
            405: "That action is not available here",
        }
        messages = {
            403: "Your account does not have permission to open this record.",
            404: "The page may have moved, or the link may be out of date.",
            405: "The page was opened with the wrong kind of request.",
        }
        return render(
            request,
            "errors/error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            title=titles.get(exc.status_code, "Something went wrong"),
            message=messages.get(exc.status_code, "Please try again."),
        )

    @app.exception_handler(Exception)
    def _unhandled(request: Request, exc: Exception):  # pragma: no cover - last resort
        logger.exception("Unhandled error on %s", request.url.path)
        return render(
            request,
            "errors/error.html",
            status_code=500,
            code=500,
            title="Something went wrong on our side",
            message=(
                "The action did not complete and nothing was saved. If it keeps happening, "
                "an administrator can check the audit log."
            ),
        )


app = create_app()
