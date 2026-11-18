"""Concurrency: two people acting on one slot at the same moment.

The database enforces "one confirmed mentor per project" and "one confirmed team
per project" with partial unique indexes, so the services can promise exactly one
winner without trusting the read that happened a moment earlier. Each round runs
on its own copy of the seeded database, so a round cannot leak rows into the
next one and nothing has to be deleted (deletes are refused by design).
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest
from sqlalchemy import func, select

from data.database import Database
from data.models import Mentor, MentorAssignment, Project, Team, TeamMember, User
from services import matching, mentor_assignments, teams
from services.errors import Conflict, PermissionDenied, ValidationError
from tests.conftest import make_settings

MENTOR_ROUNDS = 100
TEAM_ROUNDS = 20


def _run_together(actions):
    """Start each callable at the same moment and collect their outcomes."""
    barrier = threading.Barrier(len(actions))
    results: list = [None] * len(actions)

    def runner(index: int, action) -> None:
        barrier.wait()
        try:
            results[index] = ("ok", action())
        except (Conflict, PermissionDenied, ValidationError) as exc:
            results[index] = ("refused", exc)

    threads = [
        threading.Thread(target=runner, args=(index, action))
        for index, action in enumerate(actions)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


@pytest.fixture
def round_database(seeded_db_path, tmp_path):
    """Hand out a private copy of the seeded database per round."""
    counter = {"n": 0}

    def make() -> Database:
        counter["n"] += 1
        target = tmp_path / f"round-{counter['n']}.db"
        shutil.copy(seeded_db_path, target)
        return Database(make_settings(target))

    yield make


def test_contested_mentor_slot_yields_exactly_one_confirmation(round_database):
    for _ in range(MENTOR_ROUNDS):
        database = round_database()
        with database.session() as setup:
            project = setup.scalar(
                select(Project)
                .where(
                    Project.state.in_({"open", "approved", "active"}),
                    Project.id.not_in(
                        select(MentorAssignment.project_id).where(
                            MentorAssignment.state == "confirmed"
                        )
                    ),
                )
                .order_by(Project.id)
            )
            assert project is not None, "the seed needs a project without a confirmed mentor"
            mentors = setup.scalars(select(Mentor).order_by(Mentor.id).limit(2)).all()
            assert len(mentors) == 2
            rows = [
                MentorAssignment(
                    project_id=project.id,
                    mentor_id=mentor.id,
                    state="proposed",
                    reason="Concurrency probe",
                )
                for mentor in mentors
            ]
            setup.add_all(rows)
            setup.flush()
            assignment_ids = [row.id for row in rows]
            actor_ids = [mentor.user_id for mentor in mentors]
            project_id = project.id

        def accept(assignment_id: int, user_id: int):
            def action():
                with database.session() as session:
                    assignment = session.get(MentorAssignment, assignment_id)
                    actor = session.get(User, user_id)
                    return mentor_assignments.decide(
                        session, assignment=assignment, actor=actor, accept=True
                    )

            return action

        outcomes = _run_together(
            [accept(assignment_ids[0], actor_ids[0]), accept(assignment_ids[1], actor_ids[1])]
        )

        with database.session() as check:
            confirmed = check.scalar(
                select(func.count(MentorAssignment.id)).where(
                    MentorAssignment.project_id == project_id,
                    MentorAssignment.state == "confirmed",
                )
            )
        winners = [outcome for outcome in outcomes if outcome[0] == "ok"]
        assert confirmed == 1, f"project {project_id}: {confirmed} confirmed mentors"
        assert len(winners) == 1, f"project {project_id}: {len(winners)} winners"
        database.engine.dispose()


def test_double_team_confirmation_leaves_one_team(round_database):
    for _ in range(TEAM_ROUNDS):
        database = round_database()
        with database.session() as setup:
            project = setup.scalar(
                select(Project)
                .where(Project.state == "open", Project.id.not_in(select(Team.project_id)))
                .order_by(Project.id)
            )
            assert project is not None, "the seed needs an open challenge with no team"
            owner_id = project.owner_id
            ranked = matching.rank_project(setup, project)
            members = [
                result.student_id
                for result in ranked
                if result.eligibility == "eligible"
            ][: max(project.team_size_min, 2)]
            assert len(members) >= project.team_size_min, "not enough eligible candidates"
            project_id = project.id

        def confirm():
            with database.session() as session:
                row = session.get(Project, project_id)
                actor = session.get(User, owner_id)
                return teams.confirm(
                    session,
                    project=row,
                    actor=actor,
                    student_ids=members,
                    label="Race",
                    rationale="Two clicks at once.",
                )

        outcomes = _run_together([confirm, confirm])

        with database.session() as check:
            team_rows = check.scalars(
                select(Team).where(Team.project_id == project_id)
            ).all()
        winners = [outcome for outcome in outcomes if outcome[0] == "ok"]
        assert len(team_rows) == 1, f"{len(team_rows)} team rows for project {project_id}"
        assert len(winners) == 1, f"{len(winners)} successful confirmations"
        database.engine.dispose()
