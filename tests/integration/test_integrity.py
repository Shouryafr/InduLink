"""Schema integrity: pragmas, constraints, append-only history, versioning.

These are the "integrity constraints pass" half of the Phase 1 exit gate.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from core import requirements as req_rules
from data.models import (
    AuditEvent,
    Project,
    ProjectVersion,
    Requirement,
    Skill,
    StudentProfile,
    StudentSkill,
    User,
)
from services import audit_log, projects as project_service
from services.audit_log import AuditImmutableError


def make_user(session, email: str, role: str = "student", name: str = "Karan Gill") -> User:
    user = User(email=email, password_hash="x", role=role, status="active", display_name=name)
    session.add(user)
    session.flush()
    return user


class TestPragmas:
    def test_foreign_keys_on_every_connection(self, database):
        """A second connection must also enforce foreign keys, not just the first."""
        with database.engine.connect() as first:
            assert first.execute(text("PRAGMA foreign_keys")).scalar() == 1
        with database.engine.connect() as second:
            assert second.execute(text("PRAGMA foreign_keys")).scalar() == 1

    def test_wal_mode(self, database):
        with database.engine.connect() as conn:
            assert str(conn.execute(text("PRAGMA journal_mode")).scalar()).lower() == "wal"

    def test_foreign_key_violation_rejected(self, database):
        with database.engine.begin() as conn:
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO student_skills (student_id, skill_id, declared_level, "
                        "created_at, updated_at) VALUES (9999, 9999, 3, datetime('now'), datetime('now'))"
                    )
                )


class TestConstraints:
    def test_level_out_of_range_rejected_by_database(self, session):
        user = make_user(session, "level@test.demo")
        profile = StudentProfile(user_id=user.id)
        skill = Skill(canonical_name="Constraint Check", category="software")
        session.add_all([profile, skill])
        session.flush()
        session.add(StudentSkill(student_id=profile.id, skill_id=skill.id, declared_level=9))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_duplicate_canonical_skill_rejected(self, session):
        session.add(Skill(canonical_name="Unique Skill", category="software"))
        session.flush()
        session.add(Skill(canonical_name="Unique Skill", category="data"))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_one_declared_level_per_student_and_skill(self, session):
        user = make_user(session, "dupe@test.demo")
        profile = StudentProfile(user_id=user.id)
        skill = Skill(canonical_name="Duplicate Pair", category="software")
        session.add_all([profile, skill])
        session.flush()
        session.add(StudentSkill(student_id=profile.id, skill_id=skill.id, declared_level=2))
        session.flush()
        session.add(StudentSkill(student_id=profile.id, skill_id=skill.id, declared_level=4))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_requirement_weight_constraint(self, session):
        owner = make_user(session, "owner@test.demo", role="industry")
        project = Project(
            owner_id=owner.id,
            title="Constraint project",
            start_date=date.today(),
            deadline=date.today() + timedelta(days=30),
        )
        session.add(project)
        session.flush()
        version = ProjectVersion(project_id=project.id, version_no=1, created_by=owner.id)
        skill = Skill(canonical_name="Weighted Skill", category="software")
        session.add_all([version, skill])
        session.flush()
        session.add(
            Requirement(
                version_id=version.id, skill_id=skill.id, required_level=3, weight=1.5
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_team_size_range_constraint(self, session):
        owner = make_user(session, "range@test.demo", role="industry")
        session.add(
            Project(
                owner_id=owner.id,
                title="Bad range",
                team_size_min=5,
                team_size_max=2,
                start_date=date.today(),
                deadline=date.today() + timedelta(days=10),
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


class TestAuditAppendOnly:
    def test_orm_update_is_refused(self, session):
        user = make_user(session, "auditor@test.demo", role="admin")
        event = audit_log.record(session, actor=user, action="test.event")
        session.flush()
        event.action = "rewritten"
        with pytest.raises(AuditImmutableError):
            session.flush()
        session.rollback()

    def test_orm_delete_is_refused(self, session):
        user = make_user(session, "auditor2@test.demo", role="admin")
        event = audit_log.record(session, actor=user, action="test.event")
        session.flush()
        session.delete(event)
        with pytest.raises(AuditImmutableError):
            session.flush()
        session.rollback()

    def test_raw_sql_update_is_refused(self, database, session):
        user = make_user(session, "auditor3@test.demo", role="admin")
        audit_log.record(session, actor=user, action="test.raw_event")
        session.commit()
        # SQLite reports a trigger's RAISE(ABORT) as a constraint failure.
        with pytest.raises(IntegrityError):
            with database.engine.begin() as conn:
                conn.execute(text("UPDATE audit_events SET action = 'rewritten'"))
        with database.engine.connect() as conn:
            actions = [row[0] for row in conn.execute(text("SELECT action FROM audit_events"))]
        assert "test.raw_event" in actions
        assert "rewritten" not in actions

    def test_raw_sql_delete_is_refused(self, database, session):
        user = make_user(session, "auditor4@test.demo", role="admin")
        audit_log.record(session, actor=user, action="test.keep_me")
        session.commit()
        with pytest.raises(IntegrityError):
            with database.engine.begin() as conn:
                conn.execute(text("DELETE FROM audit_events"))
        with database.engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM audit_events")).scalar()
        assert count >= 1

    def test_audit_requires_an_action(self, session):
        with pytest.raises(ValueError):
            audit_log.record(session, actor=None, action="  ")


class TestNoHardDeletes:
    def test_user_delete_is_refused_by_trigger(self, database, session):
        user = make_user(session, "nodelete@test.demo")
        session.commit()
        with pytest.raises(IntegrityError):
            with database.engine.begin() as conn:
                conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
        with database.engine.connect() as conn:
            assert conn.execute(
                text("SELECT COUNT(*) FROM users WHERE id = :id"), {"id": user.id}
            ).scalar() == 1


class TestTransactionRollback:
    def test_failed_unit_of_work_leaves_no_partial_state(self, database):
        """A failure inside one transaction must not persist half an operation."""
        with pytest.raises(RuntimeError):
            with database.session() as session:
                make_user(session, "partial@test.demo")
                session.flush()
                raise RuntimeError("simulated failure mid-update")

        with database.engine.connect() as conn:
            remaining = conn.execute(
                text("SELECT COUNT(*) FROM users WHERE email = 'partial@test.demo'")
            ).scalar()
        assert remaining == 0


class TestProjectVersioning:
    def _create(self, session, owner, skill, level=3, mandatory=True):
        drafts = [
            req_rules.RequirementDraft(
                skill_id=skill.id,
                required_level=level,
                weight_raw=2,
                is_mandatory=mandatory,
            )
        ]
        return project_service.create_project(
            session,
            owner=owner,
            title="Versioned challenge",
            domain="Data",
            description="A challenge used to prove versioning.",
            difficulty=3,
            team_size_min=2,
            team_size_max=3,
            start_date=date.today(),
            deadline=date.today() + timedelta(days=30),
            visibility="private",
            requirement_drafts=drafts,
            actor=owner,
        )

    def test_edit_creates_a_new_version_and_keeps_the_old(self, session):
        owner = make_user(session, "versioner@test.demo", role="industry")
        skill = Skill(canonical_name="Version Skill", category="software")
        session.add(skill)
        session.flush()

        project = self._create(session, owner, skill)
        first = project_service.current_version(session, project.id)
        assert first.version_no == 1

        version = project_service.edit_requirements(
            session,
            project=project,
            actor=owner,
            requirement_drafts=[
                req_rules.RequirementDraft(
                    skill_id=skill.id, required_level=4, weight_raw=1, is_mandatory=True
                )
            ],
            reason="Raised the required level after partner feedback",
        )
        assert version.version_no == 2

        versions = list(
            session.scalars(
                select(ProjectVersion)
                .where(ProjectVersion.project_id == project.id)
                .order_by(ProjectVersion.version_no)
            ).all()
        )
        assert [row.version_no for row in versions] == [1, 2]
        # The earlier version is still readable, with its original level.
        old_requirements = project_service.version_requirements(session, versions[0].id)
        assert old_requirements[0].required_level == 3
        new_requirements = project_service.version_requirements(session, versions[1].id)
        assert new_requirements[0].required_level == 4

    def test_exactly_one_current_version(self, session):
        owner = make_user(session, "current@test.demo", role="industry")
        skill = Skill(canonical_name="Current Skill", category="software")
        session.add(skill)
        session.flush()
        project = self._create(session, owner, skill)
        project_service.edit_requirements(
            session,
            project=project,
            actor=owner,
            requirement_drafts=[
                req_rules.RequirementDraft(
                    skill_id=skill.id, required_level=2, weight_raw=1, is_mandatory=False
                )
            ],
            reason="Relaxed the requirement",
        )
        current_count = session.scalar(
            select(func.count(ProjectVersion.id)).where(
                ProjectVersion.project_id == project.id, ProjectVersion.is_current.is_(True)
            )
        )
        assert current_count == 1

    def test_weights_are_normalised_on_write(self, session):
        owner = make_user(session, "weights@test.demo", role="industry")
        skill_a = Skill(canonical_name="Weight A", category="software")
        skill_b = Skill(canonical_name="Weight B", category="data")
        session.add_all([skill_a, skill_b])
        session.flush()
        project = project_service.create_project(
            session,
            owner=owner,
            title="Weighted challenge",
            domain="Data",
            description="x",
            difficulty=2,
            team_size_min=1,
            team_size_max=2,
            start_date=date.today(),
            deadline=date.today() + timedelta(days=20),
            visibility="private",
            requirement_drafts=[
                req_rules.RequirementDraft(
                    skill_id=skill_a.id, required_level=3, weight_raw=3, is_mandatory=True
                ),
                req_rules.RequirementDraft(
                    skill_id=skill_b.id, required_level=2, weight_raw=1, is_mandatory=False
                ),
            ],
            actor=owner,
        )
        version = project_service.current_version(session, project.id)
        rows = project_service.version_requirements(session, version.id)
        assert round(sum(row.weight for row in rows), 3) == 1.0
        weights = {row.skill_id: row.weight for row in rows}
        assert weights[skill_a.id] == 0.75
        assert weights[skill_b.id] == 0.25

    def test_illegal_transition_is_refused(self, session):
        from services.errors import DomainError

        owner = make_user(session, "trans@test.demo", role="industry")
        skill = Skill(canonical_name="Transition Skill", category="software")
        session.add(skill)
        session.flush()
        project = self._create(session, owner, skill)
        with pytest.raises((ValueError, DomainError)):
            project_service.transition(
                session, project=project, target="completed", actor=owner, reason="skip ahead"
            )

    def test_duplicate_submission_creates_one_project(self, session):
        owner = make_user(session, "idem@test.demo", role="industry")
        skill = Skill(canonical_name="Idempotent Skill", category="software")
        session.add(skill)
        session.flush()
        drafts = [
            req_rules.RequirementDraft(
                skill_id=skill.id, required_level=3, weight_raw=1, is_mandatory=True
            )
        ]
        kwargs = dict(
            owner=owner,
            title="Double clicked challenge",
            domain="Data",
            description="x",
            difficulty=2,
            team_size_min=1,
            team_size_max=2,
            start_date=date.today(),
            deadline=date.today() + timedelta(days=20),
            visibility="private",
            requirement_drafts=drafts,
            actor=owner,
            form_token="a-token-used-twice",
        )
        first = project_service.create_project(session, **kwargs)
        second = project_service.create_project(session, **kwargs)
        assert first.id == second.id
        count = session.scalar(
            select(func.count(Project.id)).where(Project.title == "Double clicked challenge")
        )
        assert count == 1
