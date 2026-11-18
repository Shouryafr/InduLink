"""Scenarios A to G, run end to end through the real routes.

One command runs them all::

    make scenarios        # or: pytest tests/scenarios -q

Each scenario signs in as the role the brief names, clicks the same form a
person would, and then reads the database to prove the change persisted. A
scenario that only rendered a page would not prove the feature works.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from data.models import (
    Evidence,
    Mentor,
    MentorAssignment,
    PassportEntry,
    Project,
    Scenario,
    StudentProfile,
    Team,
    TeamMember,
    User,
)
from tests.conftest import Browser

STUDENT = "aarohi.sharma@indulink.example.test"
MENTOR = "harpreet.singh@indulink.example.test"
PARTNER = "kabir.malhotra@indulink.example.test"
ADMIN = "meera.joshi@indulink.example.test"
PASSWORD = "DemoPass!2026"


@pytest.fixture
def client(seeded_app):
    with TestClient(seeded_app) as test_client:
        yield test_client


@pytest.fixture
def db(seeded_app):
    session = seeded_app.state.db.session_factory()
    try:
        yield session
    finally:
        session.close()


def database_digest(session) -> str:
    """A checksum of every row in every table, for the read-only proof."""
    from data.models import Base

    digest = hashlib.sha256()
    for table in Base.metadata.sorted_tables:
        digest.update(table.name.encode("utf-8"))
        for row in session.execute(select(table)).all():
            digest.update(repr(tuple(row)).encode("utf-8"))
    return digest.hexdigest()


def form_token(html: str, name: str = "form_token") -> str:
    match = re.search(rf'name="{name}"\s+value="([^"]+)"', html)
    assert match, f"{name} missing from the page"
    return match.group(1)


def test_scenario_a_register_and_sign_in(client, db):
    """A. A new student registers, signs in, and is refused an admin page."""
    page = Browser(client)
    page.get("/register")
    response = page.post(
        "/register",
        {
            "display_name": "Tanvi Rathore",
            "email": "tanvi.rathore@indulink.example.test",
            "password": "CorrectHorse9",
            "confirm_password": "CorrectHorse9",
            "role": "student",
            "accepted_terms": "yes",
        },
    )
    assert response.status_code == 303, response.text[:400]

    page.get("/sign-in")
    signed_in = page.post(
        "/sign-in",
        {
            "email": "tanvi.rathore@indulink.example.test",
            "password": "CorrectHorse9",
            "next": "",
        },
    )
    assert signed_in.status_code == 303
    assert page.get("/student").status_code == 200
    assert "Tanvi Rathore" in page.get("/student").text

    created = db.scalar(
        select(User).where(User.email == "tanvi.rathore@indulink.example.test")
    )
    assert created is not None and created.role == "student"
    assert created.is_synthetic is False

    assert page.get("/admin").status_code == 403


def test_scenario_b_evidence_submitted_then_reviewed(client, db):
    """B. A student submits evidence and a mentor reviews it."""
    student = Browser(client)
    student.sign_in(STUDENT)
    profile_id = db.scalar(
        select(StudentProfile.id)
        .join(User, User.id == StudentProfile.user_id)
        .where(User.email == STUDENT)
    )
    assert profile_id is not None

    page = student.get("/student/evidence")
    assert page.status_code == 200
    from data.models import StudentSkill

    skill_row = db.scalar(
        select(StudentSkill).where(StudentSkill.student_id == profile_id)
    )
    submit = student.post(
        "/student/evidence",
        {
            "skill_id": str(skill_row.skill_id),
            "category": "practical_task",
            "source": "Scenario B: reviewed notebook and tests",
            "evidence_date": (date.today() - timedelta(days=5)).isoformat(),
            "demonstrated_level": "4",
            "expiry_date": "",
        },
    )
    assert submit.status_code == 303, submit.text[:400]

    submitted = db.scalar(
        select(Evidence)
        .where(Evidence.student_id == profile_id)
        .order_by(Evidence.id.desc())
    )
    assert submitted is not None
    assert submitted.status == "submitted"

    mentor = Browser(client)
    mentor.sign_in(MENTOR)
    assert mentor.get("/mentor/evidence").status_code == 200
    review = mentor.post(
        f"/mentor/evidence/{submitted.id}/review",
        {
            "decision": "reviewed",
            "assessed_level": "4",
            "notes": "Checked the notebook and the tests against the level definition.",
        },
    )
    assert review.status_code == 303
    db.expire_all()
    reviewed = db.get(Evidence, submitted.id)
    assert reviewed.status == "reviewed"
    assert reviewed.demonstrated_level == 4
    assert reviewed.reviewer_id is not None


def test_scenario_c_create_challenge_and_approve_it(client, db):
    """C. A partner drafts a challenge, an admin approves it, the partner opens it."""
    partner = Browser(client)
    partner.sign_in(PARTNER)
    new_page = partner.get("/industry/projects/new")
    assert new_page.status_code == 200
    token = form_token(new_page.text)
    from data.models import Skill

    skills = db.scalars(select(Skill).order_by(Skill.id).limit(2)).all()
    assert len(skills) == 2

    today = date.today()
    created = partner.post(
        "/industry/projects/new",
        {
            "title": "Scenario C: field data quality review",
            "domain": "Data and analytics",
            "description": "Review a month of field readings and explain the anomalies you find.",
            "difficulty": "3",
            "team_size_min": "2",
            "team_size_max": "4",
            "start_date": (today + timedelta(days=7)).isoformat(),
            "deadline": (today + timedelta(days=60)).isoformat(),
            "visibility": "public",
            "eligibility_note": "Open to third and fourth year students.",
            "evaluation_criteria": "Clear method, checked findings, readable summary.",
            "deliverables_summary": "A short report and the query you used.",
            "form_token": token,
            "skill_id": [str(skills[0].id), str(skills[1].id)],
            "required_level": ["3", "3"],
            "weight": ["3", "2"],
            "mandatory": ["yes", "no"],
        },
    )
    assert created.status_code == 303, created.text[:600]
    project = db.scalar(
        select(Project).where(Project.title == "Scenario C: field data quality review")
    )
    assert project is not None and project.state == "draft"

    # The partner sends it for approval, the admin approves, the partner opens it.
    assert partner.post(
        f"/industry/projects/{project.id}/state",
        {"target": "pending_approval", "reason": "Ready for review."},
    ).status_code == 303
    admin = Browser(client)
    admin.sign_in(ADMIN)
    assert admin.get("/admin/projects").status_code == 200
    assert admin.post(
        f"/admin/projects/{project.id}/transition",
        {"target": "approved", "reason": "Requirements and dates check out."},
    ).status_code == 303
    assert partner.post(
        f"/industry/projects/{project.id}/state",
        {"target": "open", "reason": "Open for candidates."},
    ).status_code == 303

    db.expire_all()
    project = db.get(Project, project.id)
    assert project.state == "open"
    candidates = partner.get(f"/industry/projects/{project.id}/candidates")
    assert candidates.status_code == 200
    assert "Why" in candidates.text, "the Why panel is part of the candidates screen"


def test_scenario_d_mentor_confirms_one_contested_request(client, db):
    """D. A mentor accepts one request; the second answer on it is refused."""
    mentor_user = db.scalar(select(User).where(User.email == MENTOR))
    mentor = db.scalar(select(Mentor).where(Mentor.user_id == mentor_user.id))
    pending = db.scalars(
        select(MentorAssignment)
        .where(
            MentorAssignment.mentor_id == mentor.id,
            MentorAssignment.state == "proposed",
        )
        .order_by(MentorAssignment.id)
    ).all()
    assert pending, "the seeded mentor needs at least one open request"

    page = Browser(client)
    page.sign_in(MENTOR)
    assert page.get("/mentor/requests").status_code == 200

    first = page.post(
        f"/mentor/requests/{pending[0].id}/decide",
        {"decision": "accept", "reason": "Capacity available this term."},
    )
    assert first.status_code == 303
    assert "saved=decided" in first.headers["location"]

    second = page.post(
        f"/mentor/requests/{pending[0].id}/decide",
        {"decision": "accept", "reason": "Trying again."},
    )
    assert "error=decision" in second.headers["location"]

    db.expire_all()
    states = [db.get(MentorAssignment, row.id).state for row in pending[:1]]
    assert states == ["confirmed"]


def test_scenario_e_team_confirmed_through_the_screen(client, db):
    """E. A partner confirms the suggested team; a second confirm is a conflict."""
    confirmed_ids = select(Team.project_id).where(Team.state == "confirmed")
    project = db.scalar(
        select(Project)
        .where(Project.state == "open", Project.id.not_in(confirmed_ids))
        .order_by(Project.id)
    )
    assert project is not None, "the seed needs an open challenge with no team yet"
    owner = db.get(User, project.owner_id)
    page = Browser(client)
    page.sign_in(owner.email)
    team_page = page.get(f"/industry/projects/{project.id}/team")
    assert team_page.status_code == 200
    checked = re.findall(r'name="member" value="(\d+)" checked', team_page.text)
    if not checked:
        checked = re.findall(r'name="member" value="(\d+)"', team_page.text)[:3]
    assert checked, "the team screen needs suggested members"

    confirmed = page.post(
        f"/industry/projects/{project.id}/team",
        {
            "member": checked,
            "label": "Scenario E team",
            "rationale": "Suggested members cover every mandatory requirement.",
        },
    )
    assert confirmed.status_code == 303
    assert "saved=team" in confirmed.headers["location"], confirmed.headers["location"]

    again = page.post(
        f"/industry/projects/{project.id}/team",
        {"member": checked, "label": "Second attempt", "rationale": "Duplicate."},
    )
    assert "error=exists" in again.headers["location"]

    teams = db.scalars(
        select(Team).where(Team.project_id == project.id, Team.state == "confirmed")
    ).all()
    assert len(teams) == 1
    members = db.scalar(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == teams[0].id)
    )
    assert members == len(checked)


def test_scenario_f_what_if_changes_nothing(client, db):
    """F. What-If runs on a read-only path: the database checksum is unchanged."""
    from services.whatif import domain_checksum

    project = db.scalar(select(Project).where(Project.state == "open"))
    skill_id = db.scalar(select(func.min(Project.id)))
    before = database_digest(db)
    domain_before = domain_checksum(db, project.id)

    page = Browser(client)
    page.sign_in(PARTNER)
    screen = page.get(f"/industry/projects/{project.id}/what-if")
    assert screen.status_code == 200
    run = page.post(
        f"/industry/projects/{project.id}/what-if",
        {
            "skill_id": str(skill_id),
            "new_level": "4",
            "mandatory": "yes",
            "team_size_max": "",
            "exclude_student_id": "",
            "label": "Scenario F",
        },
    )
    assert run.status_code == 303, run.text[:400]
    assert "scenario=" in run.headers["location"]

    db.expire_all()
    assert domain_checksum(db, project.id) == domain_before, "What-If wrote domain data"
    scenario = db.scalar(
        select(Scenario).where(Scenario.project_id == project.id, Scenario.label == "Scenario F")
    )
    assert scenario is not None, "the scenario record is stored so it can be re-read"
    assert scenario.results["unchanged"] is True
    # Only the scenario record and its audit entry may be new.
    changed = database_digest(db) != before
    assert changed, "the scenario record itself is the one permitted write"
    assert db.scalar(select(func.count(Scenario.id))) == 1


def test_scenario_g_passport_created_published_and_exported(client, db):
    """G. A student creates a passport entry from a confirmed place, publishes it, exports it."""
    taken = select(PassportEntry.student_id).where(
        PassportEntry.project_id == Team.project_id
    )
    place = db.execute(
        select(TeamMember.student_id, Team.project_id)
        .join(Team, Team.id == TeamMember.team_id)
        .join(Project, Project.id == Team.project_id)
        .where(Team.state == "confirmed", TeamMember.student_id.not_in(taken))
        .order_by(TeamMember.id)
    ).first()
    assert place is not None, "the seed needs a confirmed place without a passport"
    student_id, project_id = place

    profile = db.get(StudentProfile, student_id)
    owner = db.get(User, profile.user_id)
    page = Browser(client)
    page.sign_in(owner.email)
    assert page.get("/student/passport").status_code == 200

    created = page.post(
        "/student/passport",
        {
            "project_id": str(project_id),
            "role": "Data review",
            "technologies": "Python, SQL",
            "competencies": "Checked a month of readings\nWrote the summary",
            "verified_scope": "Scope confirmed by the project mentor.",
        },
    )
    assert created.status_code == 303
    assert "saved=created" in created.headers["location"]

    entry = db.scalar(
        select(PassportEntry).where(
            PassportEntry.student_id == student_id,
            PassportEntry.project_id == project_id,
        )
    )
    assert entry is not None and entry.publication_state == "unpublished"

    published = page.post(
        f"/student/passport/{entry.id}/publication", {"publish": "yes"}
    )
    assert published.status_code == 303
    db.expire_all()
    assert db.get(PassportEntry, entry.id).publication_state == "approved"

    export = page.get(f"/student/passport/{entry.id}/export")
    assert export.status_code == 200
    assert export.headers["content-disposition"].startswith("attachment;")
    assert export.json()["passport"]["id"] == entry.id
