"""Authorization matrix and object-level access.

The Phase 1 exit gate requires the permission matrix to pass. Every protected
route is exercised for each role, and object-level checks are tested separately
from role checks, because a role guard alone is not access control.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select, text

from data.models import Project, StudentProfile, User
from services import authentication, projects as project_service, student_profiles
from services.errors import PermissionDenied

PASSWORD = "CorrectHorse9"

ROUTES = {
    "/student": {"student": 200, "mentor": 403, "industry": 403, "admin": 403},
    "/student/profile": {"student": 200, "mentor": 403, "industry": 403, "admin": 403},
    "/student/evidence": {"student": 200, "mentor": 403, "industry": 403, "admin": 403},
    "/mentor": {"student": 403, "mentor": 200, "industry": 403, "admin": 403},
    "/mentor/registry": {"student": 403, "mentor": 200, "industry": 403, "admin": 403},
    "/mentor/evidence": {"student": 403, "mentor": 200, "industry": 403, "admin": 403},
    "/industry": {"student": 403, "mentor": 403, "industry": 200, "admin": 200},
    "/industry/projects": {"student": 403, "mentor": 403, "industry": 200, "admin": 200},
    "/industry/projects/new": {"student": 403, "mentor": 403, "industry": 200, "admin": 200},
    "/admin": {"student": 403, "mentor": 403, "industry": 403, "admin": 200},
    "/admin/users": {"student": 403, "mentor": 403, "industry": 403, "admin": 200},
    "/admin/skills": {"student": 403, "mentor": 403, "industry": 403, "admin": 200},
    "/admin/evidence": {"student": 403, "mentor": 403, "industry": 403, "admin": 200},
    "/admin/projects": {"student": 403, "mentor": 403, "industry": 403, "admin": 200},
    "/admin/audit": {"student": 403, "mentor": 403, "industry": 403, "admin": 200},
    "/search": {"student": 200, "mentor": 200, "industry": 200, "admin": 200},
}

ROLE_NAMES = {
    "student": "Diya Arora",
    "mentor": "Ankit Mehra",
    "industry": "Nikhil Walia",
    "admin": "Vikram Chauhan",
}

ROLE_EMAILS = {
    "student": "student@matrix.demo",
    "mentor": "mentor@matrix.demo",
    "industry": "industry@matrix.demo",
    "admin": "admin@matrix.demo",
}


@pytest.fixture
def accounts(database) -> dict[str, int]:
    """One active account per role, plus the student profile the routes need."""
    ids: dict[str, int] = {}
    with database.session() as session:
        for role, email in ROLE_EMAILS.items():
            user = User(
                email=email,
                password_hash=authentication.hash_password(PASSWORD, rounds=4),
                role=role,
                status="active",
                display_name=ROLE_NAMES[role],
            )
            session.add(user)
            session.flush()
            ids[role] = user.id
            if role == "student":
                session.add(StudentProfile(user_id=user.id, visibility="private"))
        session.flush()
    return ids


@pytest.mark.parametrize("path", sorted(ROUTES))
@pytest.mark.parametrize("role", sorted(ROLE_EMAILS))
def test_route_matrix(browser, accounts, path, role):
    browser.sign_in(ROLE_EMAILS[role], PASSWORD)
    response = browser.get(path)
    expected = ROUTES[path][role]
    assert response.status_code == expected, (
        f"{role} on {path} returned {response.status_code}, expected {expected}"
    )


@pytest.mark.parametrize("path", sorted(ROUTES))
def test_anonymous_is_redirected_to_sign_in(browser, path):
    response = browser.get(path)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/sign-in")


def test_denial_is_audited(browser, accounts, database):
    browser.sign_in(ROLE_EMAILS["student"], PASSWORD)
    assert browser.get("/admin").status_code == 403
    with database.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT target_type, target_id FROM audit_events WHERE action = 'authz.denied'")
        ).all()
    assert rows, "a 403 must leave an audit entry"
    assert any(row[0] == "route" and "/admin" in row[1] for row in rows)


class TestProjectVisibility:
    def _project(self, database, owner_email: str, visibility: str, state: str) -> int:
        with database.session() as session:
            owner = session.scalar(select(User).where(User.email == owner_email))
            project = Project(
                owner_id=owner.id,
                title=f"{visibility} {state} challenge",
                start_date=date.today(),
                deadline=date.today() + timedelta(days=30),
                visibility=visibility,
                state=state,
            )
            session.add(project)
            session.flush()
            return project.id

    def test_private_project_is_hidden_from_another_partner(self, browser, database, accounts):
        project_id = self._project(database, ROLE_EMAILS["industry"], "private", "open")
        with database.session() as session:
            other = User(
                email="other.partner@matrix.demo",
                password_hash=authentication.hash_password(PASSWORD, rounds=4),
                role="industry",
                status="active",
                display_name="Kavya Yadav",
            )
            session.add(other)

        browser.sign_in("other.partner@matrix.demo", PASSWORD)
        response = browser.get(f"/industry/projects/{project_id}")
        assert response.status_code == 403
        assert "private" in response.text

    def test_private_project_denies_a_student_at_the_service_layer(self, database, accounts):
        project_id = self._project(database, ROLE_EMAILS["industry"], "private", "open")
        with database.session() as session:
            student = session.scalar(select(User).where(User.email == ROLE_EMAILS["student"]))
            project = session.get(Project, project_id)
            assert project_service.can_view(student, project) is False
            with pytest.raises(PermissionDenied):
                project_service.require_view(student, project)
            session.rollback()

    def test_private_project_is_visible_to_its_owner(self, browser, database, accounts):
        project_id = self._project(database, ROLE_EMAILS["industry"], "private", "open")
        browser.sign_in(ROLE_EMAILS["industry"], PASSWORD)
        assert browser.get(f"/industry/projects/{project_id}").status_code == 200

    def test_draft_project_is_not_visible_to_students(self, database, accounts):
        project_id = self._project(database, ROLE_EMAILS["industry"], "public", "draft")
        with database.session() as session:
            student = session.scalar(select(User).where(User.email == ROLE_EMAILS["student"]))
            project = session.get(Project, project_id)
            assert project_service.can_view(student, project) is False
            session.rollback()


class TestStudentProfilePrivacy:
    def _private_profile(self, database, accounts) -> int:
        with database.session() as session:
            profile = session.scalar(select(StudentProfile).limit(1))
            return profile.id

    def test_student_cannot_read_another_students_private_profile(self, browser, database, accounts):
        profile_id = self._private_profile(database, accounts)
        browser.sign_in(ROLE_EMAILS["student"], PASSWORD)
        response = browser.get(f"/students/{profile_id}")
        # The only student in this fixture is the owner, so make a second one.
        assert response.status_code == 200

        with database.session() as session:
            second = User(
                email="second.student@matrix.demo",
                password_hash=authentication.hash_password(PASSWORD, rounds=4),
                role="student",
                status="active",
                display_name="Priya Verma",
            )
            session.add(second)
            session.flush()
            other_profile = StudentProfile(user_id=second.id, visibility="private")
            session.add(other_profile)
            session.flush()
            other_id = other_profile.id

        response = browser.get(f"/students/{other_id}")
        assert response.status_code == 403
        with database.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT target_type, reason FROM audit_events "
                    "WHERE action = 'authz.denied' AND target_type = 'student_profile'"
                )
            ).all()
        assert any(row[1] == "profile_visibility" for row in rows)

    def test_mentor_cannot_read_a_private_profile(self, browser, database, accounts):
        profile_id = self._private_profile(database, accounts)
        browser.sign_in(ROLE_EMAILS["mentor"], PASSWORD)
        assert browser.get(f"/students/{profile_id}").status_code == 403

    def test_mentor_can_read_an_institution_profile(self, browser, database, accounts):
        profile_id = self._private_profile(database, accounts)
        with database.session() as session:
            session.get(StudentProfile, profile_id).visibility = "university"
        browser.sign_in(ROLE_EMAILS["mentor"], PASSWORD)
        assert browser.get(f"/students/{profile_id}").status_code == 200

    def test_industry_cannot_read_an_institution_profile(self, browser, database, accounts):
        profile_id = self._private_profile(database, accounts)
        with database.session() as session:
            session.get(StudentProfile, profile_id).visibility = "university"
        browser.sign_in(ROLE_EMAILS["industry"], PASSWORD)
        assert browser.get(f"/students/{profile_id}").status_code == 403

    def test_admin_can_read_any_profile(self, browser, database, accounts):
        profile_id = self._private_profile(database, accounts)
        browser.sign_in(ROLE_EMAILS["admin"], PASSWORD)
        assert browser.get(f"/students/{profile_id}").status_code == 200

    def test_owner_can_read_their_own_private_profile(self, browser, database, accounts):
        profile_id = self._private_profile(database, accounts)
        browser.sign_in(ROLE_EMAILS["student"], PASSWORD)
        assert browser.get(f"/students/{profile_id}").status_code == 200


class TestEvidenceReviewPermissions:
    def test_a_student_cannot_review_evidence(self, database, accounts):
        from data.models import Evidence, Skill

        with database.session() as session:
            student = session.scalar(select(User).where(User.email == ROLE_EMAILS["student"]))
            profile = session.scalar(select(StudentProfile).limit(1))
            skill = Skill(canonical_name="Review Guard", category="software")
            session.add(skill)
            session.flush()
            evidence = Evidence(
                student_id=profile.id,
                skill_id=skill.id,
                category="practical_task",
                source="Synthetic source",
                evidence_date=date.today(),
                demonstrated_level=3,
                status="submitted",
            )
            session.add(evidence)
            session.flush()

            from services import evidence_registry
            from services.errors import PermissionDenied as Denied

            with pytest.raises(Denied):
                evidence_registry.review(
                    session,
                    evidence=evidence,
                    reviewer=student,
                    decision="reviewed",
                    assessed_level=3,
                    notes="A note long enough",
                )
            session.rollback()

    def test_a_reviewer_cannot_review_their_own_evidence(self, database, accounts):
        from data.models import Evidence, Mentor, Skill

        with database.session() as session:
            mentor_user = session.scalar(select(User).where(User.email == ROLE_EMAILS["mentor"]))
            mentor_user.role = "mentor"
            profile = StudentProfile(user_id=mentor_user.id)
            session.add(profile)
            skill = Skill(canonical_name="Self Review", category="software")
            session.add(skill)
            session.flush()
            evidence = Evidence(
                student_id=profile.id,
                skill_id=skill.id,
                category="practical_task",
                source="Self submitted",
                evidence_date=date.today(),
                demonstrated_level=3,
                status="submitted",
            )
            session.add(evidence)
            session.flush()

            from services import evidence_registry
            from services.errors import PermissionDenied as Denied

            with pytest.raises(Denied):
                evidence_registry.review(
                    session,
                    evidence=evidence,
                    reviewer=mentor_user,
                    decision="reviewed",
                    assessed_level=3,
                    notes="I checked my own work",
                )
            session.rollback()
