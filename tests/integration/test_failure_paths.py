"""Failure paths a person can actually reach from a screen.

Three regressions are covered here, each found by walking the running site:

* an illegal state transition must answer 409 with an explanation, not 500;
* an unexpected crash must still render the branded error page, even though the
  request session has already been rolled back and closed by then;
* a refused change that answers with a redirect must carry its reason back to
  the page, instead of failing silently.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from data.models import Skill, StudentProfile, User
from services import authentication

PASSWORD = "CorrectHorse9"

FAILURE_NAMES = {
    "student": "Naina Kapoor",
    "mentor": "Ankit Mehra",
    "industry": "Nikhil Walia",
    "admin": "Vikram Chauhan",
}


def _skill(session):
    """The migrated test database starts empty, so requirements need a skill."""
    skill = session.scalar(select(Skill).limit(1))
    if skill is None:
        skill = Skill(canonical_name="Failure Path Skill", category="software")
        session.add(skill)
        session.flush()
    return skill


def _user(session, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=authentication.hash_password(PASSWORD, rounds=4),
        role=role,
        status="active",
        display_name=FAILURE_NAMES[role],
    )
    session.add(user)
    session.flush()
    return user


@pytest.fixture
def industry_with_project(database):
    """A partner owning a challenge that is already awaiting approval."""
    from core import requirements as req_rules
    from services import projects as project_service

    with database.session() as session:
        owner = _user(session, "failure.partner@test.demo", "industry")
        skill = _skill(session)
        project = project_service.create_project(
            session,
            owner=owner,
            title="Failure path challenge",
            domain="Data",
            description="Used to prove illegal transitions are refused politely.",
            difficulty=3,
            team_size_min=2,
            team_size_max=3,
            start_date=date.today(),
            deadline=date.today() + timedelta(days=30),
            visibility="private",
            requirement_drafts=[
                req_rules.RequirementDraft(
                    skill_id=skill.id, required_level=3, weight_raw=1, is_mandatory=True
                )
            ],
            actor=owner,
        )
        project_service.transition(
            session, project=project, target="pending_approval", actor=owner, reason="Ready"
        )
        return project.id


class TestIllegalTransition:
    def test_owner_gets_a_conflict_not_a_server_error(self, browser, industry_with_project):
        browser.sign_in("failure.partner@test.demo", PASSWORD)
        response = browser.post(
            f"/industry/projects/{industry_with_project}/state",
            {"target": "completed", "reason": "jump the queue"},
        )
        assert response.status_code == 409, response.status_code
        body = response.text
        assert "pending approval" in body
        assert "approved" in body  # names the legal next state
        assert "Something went wrong on our side" not in body

    def test_state_is_unchanged_after_a_refused_transition(self, browser, database, industry_with_project):
        browser.sign_in("failure.partner@test.demo", PASSWORD)
        browser.post(
            f"/industry/projects/{industry_with_project}/state",
            {"target": "completed", "reason": "jump the queue"},
        )
        with database.engine.connect() as conn:
            from sqlalchemy import text

            state = conn.execute(
                text("SELECT state FROM projects WHERE id = :i"), {"i": industry_with_project}
            ).scalar()
        assert state == "pending_approval"


class TestUnhandledErrorPage:
    def test_crash_renders_the_branded_page(self, settings, database, monkeypatch):
        """The 500 page must render after the session is gone, not crash again."""
        from app.main import create_app
        from services import student_profiles

        with database.session() as session:
            student = _user(session, "crash@test.demo", "student")
            session.add(StudentProfile(user_id=student.id))

        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated crash")

        monkeypatch.setattr(student_profiles, "get_or_create", boom)

        application = create_app(settings)
        try:
            with TestClient(application, raise_server_exceptions=False) as client:
                client.get("/sign-in")
                token = client.cookies.get("indulink_csrf", "")
                client.post(
                    "/sign-in",
                    data={
                        "email": "crash@test.demo",
                        "password": PASSWORD,
                        "csrf_token": token.split(".")[0],
                    },
                    follow_redirects=False,
                )
                response = client.get("/student", follow_redirects=False)
        finally:
            application.state.db.engine.dispose()

        assert response.status_code == 500
        assert "Something went wrong on our side" in response.text
        # The shared chrome still rendered, which is what the detached-session
        # snapshot exists for.
        assert "InduLink" in response.text
        assert 'class="skip-link"' in response.text


class TestRefusalsExplainThemselves:
    def test_requirement_edit_without_a_reason_shows_the_reason(self, browser, database):
        from core import requirements as req_rules
        from services import projects as project_service

        with database.session() as session:
            owner = _user(session, "explain.partner@test.demo", "industry")
            skill = _skill(session)
            project = project_service.create_project(
                session,
                owner=owner,
                title="Explain the refusal",
                domain="Data",
                description="Editing without a reason must be explained.",
                difficulty=2,
                team_size_min=1,
                team_size_max=2,
                start_date=date.today(),
                deadline=date.today() + timedelta(days=20),
                visibility="private",
                requirement_drafts=[
                    req_rules.RequirementDraft(
                        skill_id=skill.id, required_level=3, weight_raw=1, is_mandatory=True
                    )
                ],
                actor=owner,
            )
            project_id = project.id

        browser.sign_in("explain.partner@test.demo", PASSWORD)
        response = browser.post(
            f"/industry/projects/{project_id}/requirements",
            {
                "reason": "",
                "skill_id": [str(skill.id)],
                "required_level": ["3"],
                "weight": ["1"],
                "mandatory": ["yes"],
            },
        )
        assert response.status_code == 303
        assert "error=requirements" in response.headers["location"]

        page = browser.get(f"/industry/projects/{project_id}?error=requirements")
        assert page.status_code == 200
        assert "Record why they changed" in page.text

        with database.engine.connect() as conn:
            from sqlalchemy import text

            versions = conn.execute(
                text("SELECT COUNT(*) FROM project_versions WHERE project_id = :i"),
                {"i": project_id},
            ).scalar()
        assert versions == 1, "a refused edit must not create a version"
