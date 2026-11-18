"""The demo dataset: North Indian names, required entities, deliberate edge cases.

The name audit is the important one: it loads every person name from the seeded
database and fails if a first name or surname is outside the pools, a full name
repeats, a name is blocklisted, or an address leaves the reserved domain.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from config import Settings
from data import seed_synthetic
from data.demo_personas import (
    ALL_POOL_FIRST_NAMES,
    ALL_POOL_SURNAMES,
    EMAIL_DOMAIN,
    FIRST_NAMES_MEN,
    FIRST_NAMES_WOMEN,
    SIKH_MEN,
    SIKH_WOMEN,
)
from data.models import (
    Appeal,
    Evaluation,
    Evidence,
    Feedback,
    Mentor,
    MentorAssignment,
    MentorUnavailability,
    Notification,
    PassportEntry,
    Project,
    ProjectVersion,
    RecommendationSnapshot,
    StudentProfile,
    StudentSkill,
    Team,
    TeamMember,
    User,
)
from data.seed_synthetic import MINIMUMS, SeedRefused

TITLES = {"dr", "dr.", "prof", "prof.", "professor"}


def cleaned_name(display_name: str) -> str:
    """Person name without a title or the demo company suffix."""
    name = (display_name or "").split("·")[0].strip()
    parts = [part for part in name.split() if part.lower() not in TITLES]
    return " ".join(parts)


def digest_of(session) -> str:
    parts: list[str] = []
    for email, name, role in session.execute(
        select(User.email, User.display_name, User.role).order_by(User.email)
    ).all():
        parts.append(f"{email}|{name}|{role}")
    for student_id, skill_id, level in session.execute(
        select(StudentSkill.student_id, StudentSkill.skill_id, StudentSkill.declared_level).order_by(
            StudentSkill.student_id, StudentSkill.skill_id
        )
    ).all():
        parts.append(f"{student_id}:{skill_id}:{level}")
    for row in session.execute(
        select(Evidence.student_id, Evidence.skill_id, Evidence.status, Evidence.demonstrated_level).order_by(
            Evidence.student_id, Evidence.skill_id, Evidence.id
        )
    ).all():
        parts.append("|".join(str(value) for value in row))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


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


class TestMinimums:
    @pytest.mark.parametrize("key", sorted(MINIMUMS))
    def test_every_minimum_is_met(self, seeded_session, key):
        counts = {
            "students": func.count(StudentProfile.id),
            "mentors": None,
            "faculty": None,
            "industry": None,
            "admins": None,
            "projects": func.count(Project.id),
            "teams": func.count(Team.id),
            "evidence": func.count(Evidence.id),
            "passports": func.count(PassportEntry.id),
            "engagement": None,
        }[key]
        if key == "mentors":
            value = seeded_session.scalar(
                select(func.count(Mentor.id)).where(Mentor.domains.not_like("Faculty reviewer%"))
            )
        elif key == "faculty":
            value = seeded_session.scalar(
                select(func.count(Mentor.id)).where(Mentor.domains.like("Faculty reviewer%"))
            )
        elif key == "industry":
            value = seeded_session.scalar(
                select(func.count(User.id)).where(User.role == "industry")
            )
        elif key == "admins":
            value = seeded_session.scalar(select(func.count(User.id)).where(User.role == "admin"))
        elif key == "engagement":
            value = (
                (seeded_session.scalar(select(func.count(Evaluation.id))) or 0)
                + (seeded_session.scalar(select(func.count(Feedback.id))) or 0)
                + (seeded_session.scalar(select(func.count(Appeal.id))) or 0)
            )
        else:
            value = seeded_session.scalar(counts)
        assert value >= MINIMUMS[key], f"{key}: {value} < {MINIMUMS[key]}"

    def test_students_span_four_departments_and_years(self, seeded_session):
        headlines = [row for (row,) in seeded_session.execute(select(StudentProfile.headline)).all()]
        for fragment in ["Computer Science", "Electronics", "Mechanical", "Management"]:
            assert any(fragment in headline for headline in headlines), fragment
        for year in ["1st-year", "2nd-year", "3rd-year", "4th-year"]:
            assert any(year in headline for headline in headlines), year


class TestDeliberateEdgeCases:
    def test_a_student_with_no_evidence(self, seeded_session):
        rows = seeded_session.execute(
            select(StudentProfile.id)
            .where(
                ~StudentProfile.id.in_(select(Evidence.student_id)),
                StudentProfile.id.in_(select(StudentSkill.student_id)),
            )
        ).all()
        assert rows, "expected a student with declared skills and no evidence"

    def test_a_student_with_no_availability(self, seeded_session):
        row = seeded_session.scalar(
            select(StudentProfile).where(StudentProfile.availability_hours.is_(None))
        )
        assert row is not None

    def test_a_student_whose_window_does_not_overlap_any_live_project(self, seeded_session):
        today = date.today()
        row = seeded_session.scalar(
            select(StudentProfile).where(StudentProfile.availability_to < today)
        )
        assert row is not None

    def test_a_student_with_an_out_of_region_timezone(self, seeded_session):
        zones = {
            zone
            for (zone,) in seeded_session.execute(select(StudentProfile.availability_timezone)).all()
        }
        assert len(zones) > 1

    def test_an_expired_evidence_item(self, seeded_session):
        row = seeded_session.scalar(
            select(Evidence).where(Evidence.expiry_date < date.today())
        )
        assert row is not None

    def test_a_rejected_upload(self, seeded_session):
        assert seeded_session.scalar(
            select(func.count(Evidence.id)).where(Evidence.status == "rejected")
        )

    def test_a_mentor_with_no_availability_for_any_project(self, seeded_session):
        rows = seeded_session.scalars(select(MentorUnavailability)).all()
        assert any((row.to_date - row.from_date).days > 180 for row in rows)

    def test_two_students_competing_for_one_mentor_slot(self, seeded_session):
        rows = seeded_session.execute(
            select(
                MentorAssignment.project_id,
                func.count(MentorAssignment.id),
            )
            .where(MentorAssignment.state == "proposed")
            .group_by(MentorAssignment.project_id)
            .having(func.count(MentorAssignment.id) >= 2)
        ).all()
        assert rows, "expected a project with two proposed mentors"

    def test_a_project_whose_requirements_changed_after_a_snapshot(self, seeded_session):
        project = seeded_session.scalar(
            select(Project)
            .join(ProjectVersion, ProjectVersion.project_id == Project.id)
            .where(ProjectVersion.version_no == 2)
            .order_by(Project.id)
            .limit(1)
        )
        assert project is not None
        versions = seeded_session.scalars(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project.id)
            .order_by(ProjectVersion.version_no)
        ).all()
        assert [version.version_no for version in versions] == [1, 2]
        assert sum(1 for version in versions if version.is_current) == 1
        assert seeded_session.scalar(
            select(func.count(RecommendationSnapshot.id)).where(
                RecommendationSnapshot.project_id == project.id
            )
        ), "the earlier ranking should still be recorded"

    def test_a_tie_in_scores(self, seeded_session):
        from services import matching

        project = seeded_session.scalar(select(Project).where(Project.state == "open").order_by(Project.id))
        results = matching.rank_project(seeded_session, project, viewer_role="industry")
        assert any(result.tied for result in results), "expected a tie in the candidate scores"

    def test_a_team_that_breaks_a_hard_constraint(self, seeded_session):
        invalid = []
        for team in seeded_session.scalars(select(Team)).all():
            project = seeded_session.get(Project, team.project_id)
            if len(team.members) < project.team_size_min:
                invalid.append(team)
        assert invalid, "expected at least one team that cannot be confirmed"

    def test_a_hard_constraint_failure_never_outranks_a_high_soft_score(self, seeded_session):
        from services import matching

        for project in seeded_session.scalars(
            select(Project).where(Project.state == "open")
        ).all():
            results = matching.rank_project(seeded_session, project, viewer_role="industry")
            ineligible_ranks = [r.rank for r in results if r.eligibility == "not_eligible"]
            eligible_ranks = [r.rank for r in results if r.eligibility == "eligible"]
            if ineligible_ranks and eligible_ranks:
                assert min(ineligible_ranks) > max(eligible_ranks)


class TestNameAudit:
    def test_every_name_is_from_the_pools_and_unique(self, seeded_session):
        users = seeded_session.scalars(select(User).order_by(User.id)).all()
        seen: set[str] = set()
        problems: list[str] = []
        for user in users:
            name = cleaned_name(user.display_name)
            parts = name.split()
            if len(parts) != 2:
                problems.append(f"{user.email}: {user.display_name!r} is not 'First Surname'")
                continue
            first, last = parts
            if first not in ALL_POOL_FIRST_NAMES:
                problems.append(f"{user.email}: first name {first!r} is outside the pools")
            if last not in ALL_POOL_SURNAMES:
                problems.append(f"{user.email}: surname {last!r} is outside the pools")
            if first in SIKH_WOMEN and last != "Kaur":
                problems.append(f"{user.email}: {name} breaks the Kaur convention")
            if first in SIKH_MEN and last != "Singh":
                problems.append(f"{user.email}: {name} breaks the Singh convention")
            if first in FIRST_NAMES_WOMEN and last == "Singh":
                problems.append(f"{user.email}: {name} mixes a women's name with Singh")
            if first in FIRST_NAMES_MEN and last == "Kaur":
                problems.append(f"{user.email}: {name} mixes a men's name with Kaur")
            if name in seen:
                problems.append(f"{user.email}: duplicate full name {name}")
            seen.add(name)
        assert not problems, "\n".join(problems)

    def test_names_are_balanced_between_women_and_men(self, seeded_session):
        women = men = 0
        for (display_name,) in seeded_session.execute(select(User.display_name)).all():
            first = cleaned_name(display_name).split()[0]
            if first in FIRST_NAMES_WOMEN:
                women += 1
            elif first in FIRST_NAMES_MEN:
                men += 1
        assert women >= 20 and men >= 20, (women, men)

    def test_emails_use_the_reserved_domain(self, seeded_session):
        bad = [
            email
            for (email,) in seeded_session.execute(select(User.email)).all()
            if not email.endswith("@" + EMAIL_DOMAIN)
        ]
        assert not bad, bad

    def test_no_blocklisted_name(self, seeded_session):
        blocklist = {
            line.strip().casefold()
            for line in (Path(__file__).resolve().parent.parent / "data" / "name_blocklist.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.startswith("#")
        }
        offenders = [
            cleaned_name(name)
            for (name,) in seeded_session.execute(select(User.display_name)).all()
            if cleaned_name(name).casefold() in blocklist
        ]
        assert not offenders, offenders

    def test_scoring_does_not_read_name_fields(self, seeded_session):
        """Renaming every person must not change the ranking order."""
        from data.models import StudentProfile

        before = [
            result.student_id
            for result in _ranking(seeded_session)
        ]
        for user in seeded_session.scalars(select(User)).all():
            user.display_name = f"Renamed {user.id}"
        seeded_session.flush()
        after = [result.student_id for result in _ranking(seeded_session)]
        assert before == after


def _ranking(session):
    from services import matching

    project = session.scalar(select(Project).where(Project.state == "open").order_by(Project.id))
    return matching.rank_project(session, project, viewer_role="industry")


class TestSyntheticLabelling:
    @pytest.mark.parametrize(
        "model",
        [User, StudentProfile, Evidence, Project, Feedback, Evaluation, Notification],
    )
    def test_every_seeded_row_is_labelled_synthetic(self, seeded_session, model):
        total = seeded_session.scalar(select(func.count(model.id))) or 0
        labelled = (
            seeded_session.scalar(
                select(func.count(model.id)).where(model.is_synthetic.is_(True))
            )
            or 0
        )
        assert total > 0
        assert labelled == total


class TestSeedGuards:
    def test_seed_refuses_when_demo_mode_is_off(self, database):
        settings = Settings(
            app_env="test",
            secret_key="test-secret-key-with-enough-length-0123456789",
            database_url="sqlite+pysqlite://",
            bcrypt_rounds=4,
            demo_mode=False,
        )
        with pytest.raises(SeedRefused):
            seed_synthetic.ensure_seed_allowed(settings)

    def test_seed_refuses_a_production_environment(self):
        settings = Settings(
            app_env="production",
            secret_key="test-secret-key-with-enough-length-0123456789",
            database_url="sqlite+pysqlite://",
            bcrypt_rounds=12,
            demo_mode=True,
        )
        with pytest.raises(SeedRefused):
            seed_synthetic.ensure_seed_allowed(settings)

    def test_seed_is_idempotent(self, seeded_db_path, tmp_path):
        from data.database import Database

        copy = tmp_path / "repeat.db"
        shutil.copy(seeded_db_path, copy)
        settings = Settings(
            app_env="test",
            secret_key="test-secret-key-with-enough-length-0123456789",
            database_url=f"sqlite+pysqlite:///{copy.as_posix()}",
            bcrypt_rounds=4,
            demo_mode=True,
        )
        database = Database(settings)
        with database.session() as session:
            before_counts = seed_synthetic.seed(session, settings=settings)
            before_digest = digest_of(session)
        with database.session() as session:
            after_counts = seed_synthetic.seed(session, settings=settings)
            after_digest = digest_of(session)
        database.engine.dispose()

        assert before_counts == after_counts
        assert before_digest == after_digest, "re-running the seed changed existing rows"


class TestNamesInText:
    """No stray synthetic names or demo domains in templates, tests or docs."""

    STALE = [
        "ana.silva",
        "bilal haddad",
        "tomas keller",
        "meera rao",
        "ade balogun",
        "sana qureshi",
        "elias berg",
        "inez carvalho",
        "@student.demo",
        "@mentor.demo",
        "@northwind.demo",
        "@harbour.demo",
        "@indulink.demo",
    ]

    def test_no_stale_demo_names_or_domains(self):
        root = Path(__file__).resolve().parent.parent.parent
        offenders: list[str] = []
        patterns = ["app/templates/**/*.html", "tests/**/*.py", "tests/**/*.txt", "*.md", "docs/**/*.md"]
        for pattern in patterns:
            for path in root.glob(pattern):
                if path.resolve() == Path(__file__).resolve():
                    continue
                if ".venv" in path.parts or "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore").casefold()
                for needle in self.STALE:
                    if needle in text:
                        offenders.append(f"{path.relative_to(root)} contains {needle!r}")
        assert not offenders, "\n".join(offenders)

