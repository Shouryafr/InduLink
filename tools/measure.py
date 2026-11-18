"""Measure the targets in section 9 of the brief, and print them as a table.

Run it on a seeded database::

    .venv/Scripts/python.exe tools/measure.py

Every number here is measured on this machine, not estimated. The page figure is
the server-side response time through the WSGI/ASGI test transport, so it
includes routing, database work and template rendering but not network time.
"""

from __future__ import annotations

import statistics
import tempfile
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from config import Settings
from core import matching as rules
from data.database import Database
from data.demo_personas import DEMO_PASSWORD
from data.models import User
from data.seed_synthetic import seed
from services import matching as match_service

ROOT = Path(__file__).resolve().parent.parent
RUNS = 5

GET_ROUTES: dict[str, list[str]] = {
    "aarohi.sharma@indulink.example.test": [
        "/student",
        "/student/profile",
        "/student/evidence",
        "/student/projects",
        "/student/passport",
        "/student/appeals",
        "/student/feedback",
        "/notifications",
    ],
    "harpreet.singh@indulink.example.test": [
        "/mentor",
        "/mentor/requests",
        "/mentor/registry",
        "/mentor/evidence",
        "/notifications",
    ],
    "kabir.malhotra@indulink.example.test": [
        "/industry",
        "/industry/projects",
        "/industry/projects/1",
        "/industry/projects/1/candidates",
        "/industry/projects/1/team",
        "/industry/projects/1/mentor",
        "/industry/projects/1/what-if",
        "/industry/projects/1/review",
        "/notifications",
    ],
    "meera.joshi@indulink.example.test": [
        "/admin",
        "/admin/users",
        "/admin/skills",
        "/admin/evidence",
        "/admin/projects",
        "/admin/audit",
        "/admin/appeals",
        "/admin/passports",
        "/admin/demo",
        "/notifications",
    ],
}

PUBLIC = ["/", "/how-it-works", "/how-recommendations-work", "/privacy", "/terms", "/sign-in"]


@dataclass
class Result:
    measure: str
    value: float
    unit: str
    target: str
    detail: str = ""


def fresh_settings(directory: Path) -> tuple[Settings, Database, Path]:
    path = directory / "measure.db"
    settings = Settings(
        app_env="test",
        secret_key="measure-secret-key-with-enough-length-0123456789",
        database_url=f"sqlite+pysqlite:///{path.as_posix()}",
        bcrypt_rounds=4,
        demo_mode=True,
    )
    database = Database(settings)
    return settings, database, path


def seed_database(settings: Settings, database: Database) -> float:
    from alembic import command
    from alembic.config import Config

    import os

    os.environ["SECRET_KEY"] = settings.secret_key
    os.environ["APP_ENV"] = settings.app_env
    os.environ["DATABASE_URL"] = settings.database_url
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")

    started = time.perf_counter()
    with database.session() as session:
        seed(session, settings=settings)
    return time.perf_counter() - started


def page_percentiles(client: TestClient) -> tuple[float, float, int]:
    timings: list[float] = []
    errors: list[str] = []
    for path in PUBLIC:
        for _ in range(RUNS):
            started = time.perf_counter()
            response = client.get(path)
            timings.append((time.perf_counter() - started) * 1000)
            if response.status_code != 200:
                errors.append(f"{path}: {response.status_code}")
    for email, paths in GET_ROUTES.items():
        client.get("/sign-in")
        client.post(
            "/sign-in",
            data={
                "email": email,
                "password": DEMO_PASSWORD,
                "csrf_token": client.cookies.get("indulink_csrf", "").split(".")[0],
            },
            follow_redirects=False,
        )
        for path in paths:
            for _ in range(RUNS):
                started = time.perf_counter()
                response = client.get(path)
                timings.append((time.perf_counter() - started) * 1000)
                if response.status_code != 200:
                    errors.append(f"{path}: {response.status_code}")
        client.post(
            "/sign-out",
            data={"csrf_token": client.cookies.get("indulink_csrf", "").split(".")[0]},
            follow_redirects=False,
        )
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    p50 = statistics.median(timings)
    if errors:
        raise SystemExit("page crawl failures:\n" + "\n".join(errors))
    return p95, p50, len(timings)


def matching_time(database: Database, students: int) -> float:
    """Score one project against `students` candidates, using the real engine."""
    with database.session() as session:
        project = session.scalar(select(match_service.Project).order_by(match_service.Project.id))
        candidates = match_service.candidate_pool(session, visibilities={"public", "university", "private"})
        assert candidates, "the seed needs candidates"
        project_view = match_service.project_view(project)
        _version, requirements = match_service.requirements_for(session, project)

        expanded: list[rules.Candidate] = []
        base = candidates[0]
        for index in range(students):
            source = candidates[index % len(candidates)]
            expanded.append(
                rules.Candidate(
                    student_id=10_000 + index,
                    display_name=f"Candidate {index}",
                    skills=source.skills,
                    availability_hours=source.availability_hours,
                    availability_from=source.availability_from,
                    availability_to=source.availability_to,
                    availability_timezone=source.availability_timezone,
                    visibility=source.visibility,
                    headline=source.headline,
                    evidence_count=source.evidence_count,
                )
            )
        started = time.perf_counter()
        rules.rank_candidates(project_view, requirements, expanded)
        return time.perf_counter() - started


def reset_time(settings: Settings, database: Database) -> float:
    from services import demo

    started = time.perf_counter()
    outcome, new_database = demo.reset(settings)
    elapsed = time.perf_counter() - started
    new_database.engine.dispose()
    assert outcome.counts.get("students", 0) > 0
    return elapsed


def main() -> None:
    from app.main import create_app

    results: list[Result] = []
    # Windows keeps the reset database open until the process exits, so the
    # temporary directory may not delete cleanly; that does not affect the numbers.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        settings, database, _path = fresh_settings(Path(directory))
        seed_seconds = seed_database(settings, database)
        results.append(
            Result("Seed run", seed_seconds, "s", "under 60 s", "migrations excluded")
        )

        application = create_app(settings)
        with TestClient(application) as client:
            p95, p50, samples = page_percentiles(client)
        results.append(
            Result(
                "Page response, server side",
                p95,
                "ms p95",
                "under 500 ms p95",
                f"{samples} requests, median {p50:.0f} ms",
            )
        )

        for students in (500, 2000):
            seconds = matching_time(database, students)
            target = "under 3 s" if students == 500 else "under 10 s"
            results.append(
                Result(f"Matching, one project, {students} students", seconds, "s", target)
            )

        results.append(
            Result("Demo reset", reset_time(settings, database), "s", "under 10 s")
        )
        database.engine.dispose()

    print("| Measure | Measured | Target | Notes |")
    print("| --- | --- | --- | --- |")
    for item in results:
        print(
            f"| {item.measure} | {item.value:.2f} {item.unit} | {item.target} | {item.detail} |"
        )


if __name__ == "__main__":
    main()
