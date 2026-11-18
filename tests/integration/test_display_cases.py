"""The master blueprint's live display cases (sections 16 and 18).

The run of show depends on one challenge whose numbers are copied from the
guide, and on four candidates that always exist: a full match, a high soft
score with a mandatory failure, an all-declared profile with no evidence, and a
profile the partner must not see at all. The mentor step needs a genuine fit on
that challenge and one challenge nobody fully covers.

These assertions go through the real services and routes rather than the seed
constants, so a change in the matcher cannot leave the demo quietly wrong.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from config import Settings
from data.demo_personas import DEMO_LOGINS
from data.models import Project, ProjectVersion, StudentProfile, User
from services import matching, mentor_assignments

CANONICAL_TITLE = "Secure Financial Transaction Monitoring System"
#: Also owned by the demo partner, so the case is reachable with the same sign-in.
NO_MENTOR_FIT_TITLE = "Security operations triage workbook"
DEMO_PARTNER = "kabir.malhotra@indulink.example.test"

#: Section 16 of the blueprint, exactly: level, weight and mandatory flag.
EXPECTED_REQUIREMENTS = [
    ("Python", 4, 0.30, True),
    ("Database Management", 3, 0.25, True),
    ("Networking", 3, 0.20, True),
    ("Cybersecurity", 4, 0.25, True),
]


@pytest.fixture
def seeded_session(seeded_db_path):
    from data.database import Database

    settings = Settings(
        app_env="test",
        secret_key="test-secret-key-with-enough-length-0123456789",
        database_url=f"sqlite+pysqlite:///{seeded_db_path.as_posix()}",
        bcrypt_rounds=4,
        demo_mode=True,
    )
    database = Database(settings)
    session = database.session_factory()
    try:
        yield session
    finally:
        session.close()
        database.engine.dispose()


@pytest.fixture
def project(seeded_session):
    row = seeded_session.scalar(select(Project).where(Project.title == CANONICAL_TITLE))
    assert row is not None, "the section 16 challenge is missing from the seed"
    return row


@pytest.fixture
def ranked(seeded_session, project):
    return {result.display_name: result for result in matching.rank_project(seeded_session, project)}


class TestCanonicalChallenge:
    def test_requirements_match_the_blueprint(self, seeded_session, project):
        version, rows = matching.requirements_for(seeded_session, project)
        assert version is not None
        found = [
            (row.skill_name, row.required_level, round(row.weight, 2), row.is_mandatory)
            for row in rows
        ]
        assert sorted(found) == sorted(EXPECTED_REQUIREMENTS)

    def test_weights_are_stored_normalised(self, seeded_session, project):
        _version, rows = matching.requirements_for(seeded_session, project)
        assert round(sum(row.weight for row in rows), 4) == 1.0

    def test_the_demo_partner_owns_it_and_can_run_the_show(self, seeded_session, project):
        owner = seeded_session.get(User, project.owner_id)
        assert owner is not None
        assert owner.email == DEMO_PARTNER
        assert project.visibility == "public"
        assert project.state == "open"
        current = seeded_session.scalar(
            select(ProjectVersion).where(
                ProjectVersion.project_id == project.id, ProjectVersion.is_current.is_(True)
            )
        )
        assert current is not None

    def test_the_editorial_fields_from_the_blueprint_are_present(self, seeded_session, project):
        assert project.eligibility_note
        assert project.evaluation_criteria
        assert project.deliverables_summary
        assert "synthetic" in project.description.casefold()


class TestSuccessfulMatch:
    def test_a_candidate_meets_every_mandatory_requirement(self, ranked):
        result = ranked["Anjali Mehra"]
        assert result.eligibility == "eligible"
        assert result.mandatory_failures == []
        assert all(row.met for row in result.rows)
        assert result.confirmed_score == pytest.approx(100.0)

    def test_she_is_visible_to_the_partner_on_the_matching_screen(
        self, seeded_browser, seeded_session, project
    ):
        assert seeded_browser.sign_in(DEMO_PARTNER).status_code == 303
        page = seeded_browser.get(f"/industry/projects/{project.id}/candidates")
        assert page.status_code == 200
        assert "Anjali Mehra" in page.text
        assert "Eligible" in page.text


class TestMandatoryFailure:
    def test_a_high_soft_score_does_not_rescue_a_mandatory_failure(self, ranked):
        result = ranked["Dev Rathore"]
        assert result.confirmed_score >= 80, "the case only works if the soft score is attractive"
        assert result.eligibility == "not_eligible"
        failed = [row.skill_name for row in result.mandatory_failures]
        assert failed == ["Networking"]

    def test_the_page_explains_the_blocker(self, seeded_browser, seeded_session, project):
        seeded_browser.sign_in(DEMO_PARTNER)
        page = seeded_browser.get(f"/industry/projects/{project.id}/candidates")
        assert page.status_code == 200
        assert "Dev Rathore" in page.text
        assert "Below the required level" in page.text


class TestIncompleteEvidence:
    def test_declared_only_is_provisional_not_failed(self, ranked):
        result = ranked["Sakshi Chopra"]
        assert result.eligibility == "provisionally_eligible"
        assert result.confirmed_score == pytest.approx(0.0)
        assert result.provisional_score == pytest.approx(100.0)
        assert all(row.outcome == "provisional" for row in result.rows)

    def test_the_gap_is_labelled_evidence_needed(self, ranked):
        result = ranked["Sakshi Chopra"]
        assert all(row.outcome_text == "Evidence needed" for row in result.rows)


class TestPrivateProfile:
    def test_a_private_profile_is_outside_the_partner_pool(self, seeded_session, project):
        names = {
            result.display_name
            for result in matching.rank_project(seeded_session, project, viewer_role="industry")
        }
        assert "Neha Khanna" not in names

    def test_only_an_administrator_sees_it(self, seeded_session, project):
        for role in ("mentor", "industry"):
            names = {
                result.display_name
                for result in matching.rank_project(seeded_session, project, viewer_role=role)
            }
            assert "Neha Khanna" not in names, role
        admin_names = {
            result.display_name
            for result in matching.rank_project(seeded_session, project, viewer_role="admin")
        }
        assert "Neha Khanna" in admin_names

    def test_the_profile_really_is_private(self, seeded_session):
        profile = seeded_session.scalar(
            select(StudentProfile)
            .join(User, User.id == StudentProfile.user_id)
            .where(User.display_name == "Neha Khanna")
        )
        assert profile is not None
        assert profile.visibility == "private"


class TestMentorStep:
    def test_the_canonical_challenge_has_a_mentor_who_covers_every_requirement(
        self, seeded_session, project
    ):
        fits = mentor_assignments.fit_for_project(seeded_session, project)
        complete = [fit for fit in fits if fit.eligible and not fit.gaps]
        assert complete, "the mentor step needs at least one genuine fit"
        best = complete[0]
        assert best.score == pytest.approx(100.0)

    def test_another_challenge_has_no_complete_fit(self, seeded_session):
        project = seeded_session.scalar(
            select(Project).where(Project.title == NO_MENTOR_FIT_TITLE)
        )
        assert project is not None
        assert project.owner_id == seeded_session.scalar(
            select(User.id).where(User.email == DEMO_PARTNER)
        )
        fits = mentor_assignments.fit_for_project(seeded_session, project)
        assert fits, "mentors should still be listed, with their gaps"
        assert not [fit for fit in fits if fit.eligible and not fit.gaps]
        assert all(fit.gaps for fit in fits if fit.eligible)


class TestDemoSignInPanel:
    def test_every_listed_account_can_actually_sign_in(self, seeded_browser):
        page = seeded_browser.get("/sign-in")
        assert page.status_code == 200
        for persona in DEMO_LOGINS:
            assert persona.email in page.text
            response = seeded_browser.sign_in(persona.email)
            assert response.status_code == 303, persona.email
            assert response.headers["location"] != "/sign-in"
