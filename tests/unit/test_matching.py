"""Matching rules: hard constraints first, explainable scores, honest ties.

These tests use the pure core module only, so they are fast and they state the
business rule rather than an implementation detail.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given, settings as hypothesis_settings, strategies as st

from core import availability
from core import matching as rules

TODAY = date(2026, 10, 30)


def project(**overrides) -> rules.ProjectView:
    values = {
        "project_id": 1,
        "title": "Synthetic challenge",
        "start_date": TODAY,
        "deadline": TODAY + timedelta(days=30),
        "timezone": "Asia/Kolkata",
        "team_size_min": 2,
        "team_size_max": 4,
    }
    values.update(overrides)
    return rules.ProjectView(**values)


def requirement(
    skill_id: int = 1,
    level: int = 3,
    weight: float = 1.0,
    mandatory: bool = True,
    name: str = "Python",
) -> rules.RequirementView:
    return rules.RequirementView(
        skill_id=skill_id,
        skill_name=name,
        required_level=level,
        weight=weight,
        is_mandatory=mandatory,
    )


def candidate(
    student_id: int = 1,
    name: str = "Aarohi Sharma",
    *,
    levels: dict[int, tuple[int | None, int | None, int | None]] | None = None,
    available_from: date | None = TODAY,
    available_to: date | None = TODAY + timedelta(days=30),
    timezone: str = "Asia/Kolkata",
    evidence_count: int = 3,
) -> rules.Candidate:
    skills = {}
    for skill_id, (declared, confirmed, provisional) in (levels or {}).items():
        label = (
            "verified"
            if confirmed is not None
            else "provisional"
            if provisional is not None
            else "unknown"
        )
        skills[skill_id] = rules.CandidateSkill(
            skill_id=skill_id,
            declared_level=declared,
            confirmed_level=confirmed,
            provisional_level=provisional,
            label=label,
        )
    return rules.Candidate(
        student_id=student_id,
        display_name=name,
        skills=skills,
        availability_hours=10,
        availability_from=available_from,
        availability_to=available_to,
        availability_timezone=timezone,
        visibility="public",
        evidence_count=evidence_count,
    )


class TestHardConstraints:
    def test_confirmed_meeting_the_level_is_eligible(self):
        result = rules.evaluate_candidate(
            project(), [requirement(level=3)], candidate(levels={1: (4, 4, 4)})
        )
        assert result.eligibility == rules.ELIGIBLE
        assert result.rows[0].outcome == "met"
        assert result.confirmed_score == 100.0

    def test_declared_only_is_provisional_and_not_eligible(self):
        result = rules.evaluate_candidate(
            project(), [requirement(level=3)], candidate(levels={1: (4, None, 4)})
        )
        assert result.eligibility == rules.PROVISIONALLY_ELIGIBLE
        assert result.rows[0].outcome == "provisional"
        assert result.confirmed_score == 0.0
        assert result.provisional_score > 0

    def test_missing_skill_is_no_data_not_zero(self):
        result = rules.evaluate_candidate(project(), [requirement(level=3)], candidate())
        assert result.rows[0].outcome == "no_data"
        assert result.rows[0].level is None
        assert result.rows[0].points == 0
        assert "no data" in result.rows[0].note.lower()

    def test_failed_mandatory_is_not_eligible_even_with_a_high_score(self):
        requirements = [requirement(1, 4, 0.5), requirement(2, 1, 0.5, name="SQL")]
        weak = candidate(1, "Aarohi Sharma", levels={1: (2, 2, 2), 2: (5, 5, 5)})
        result = rules.evaluate_candidate(project(), requirements, weak)
        assert result.eligibility == rules.NOT_ELIGIBLE
        assert result.mandatory_failures[0].skill_name == "Python"
        assert "cannot outrank" in result.constraints[0].detail

    def test_availability_outside_the_project_is_a_hard_failure(self):
        result = rules.evaluate_candidate(
            project(),
            [requirement()],
            candidate(
                levels={1: (4, 4, 4)},
                available_from=TODAY - timedelta(days=200),
                available_to=TODAY - timedelta(days=100),
            ),
        )
        assert result.eligibility == rules.NOT_ELIGIBLE
        assert any(row.key == "availability" and row.status == "failed" for row in result.constraints)

    def test_missing_availability_is_provisional_not_zero(self):
        result = rules.evaluate_candidate(
            project(),
            [requirement()],
            candidate(levels={1: (4, 4, 4)}, available_from=None, available_to=None),
        )
        assert result.eligibility == rules.PROVISIONALLY_ELIGIBLE
        assert any(row.key == "availability" and row.status == "unknown" for row in result.constraints)

    def test_partial_overlap_is_a_caveat_not_a_refusal(self):
        result = rules.evaluate_candidate(
            project(),
            [requirement()],
            candidate(
                levels={1: (4, 4, 4)},
                available_from=TODAY + timedelta(days=20),
                available_to=TODAY + timedelta(days=40),
            ),
        )
        assert result.eligibility == rules.ELIGIBLE
        assert any(row.key == "availability" and row.status == "caveat" for row in result.constraints)
        assert result.caveats

    def test_timezone_mismatch_is_explained(self):
        result = rules.evaluate_candidate(
            project(timezone="Europe/London"),
            [requirement()],
            candidate(levels={1: (4, 4, 4)}, timezone="Asia/Kolkata"),
        )
        assert any("Asia/Kolkata" in caveat for caveat in result.caveats)


class TestRanking:
    def test_eligible_always_outranks_a_higher_scoring_ineligible(self):
        requirements = [requirement(1, 4, 0.5), requirement(2, 2, 0.5, name="SQL")]
        eligible = candidate(1, "Aarohi Sharma", levels={1: (4, 4, 4), 2: (2, 2, 2)})
        ineligible = candidate(2, "Karan Gill", levels={1: (3, 3, 3), 2: (5, 5, 5)})
        ranked = rules.rank_candidates(project(), requirements, [ineligible, eligible])
        assert [result.student_id for result in ranked] == [1, 2]
        assert ranked[1].eligibility == rules.NOT_ELIGIBLE

    def test_equal_scores_are_labelled_as_ties_and_ordered_by_record_id(self):
        requirements = [requirement()]
        first = candidate(2, "Karan Gill", levels={1: (4, 4, 4)})
        second = candidate(1, "Aarohi Sharma", levels={1: (4, 4, 4)})
        ranked = rules.rank_candidates(project(), requirements, [first, second])
        assert [result.student_id for result in ranked] == [1, 2]
        assert all(result.tied for result in ranked)

    def test_renaming_changes_no_ranking(self):
        requirements = [requirement()]
        first = candidate(1, "Aarohi Sharma", levels={1: (4, 4, 4)})
        second = candidate(2, "Karan Gill", levels={1: (4, 4, 4)})
        before = [result.student_id for result in rules.rank_candidates(project(), requirements, [first, second])]
        renamed = [
            candidate(1, "Renamed One", levels={1: (4, 4, 4)}),
            candidate(2, "Renamed Two", levels={1: (4, 4, 4)}),
        ]
        after = [result.student_id for result in rules.rank_candidates(project(), requirements, renamed)]
        assert before == after

    def test_no_requirements_produces_no_rows_and_no_points(self):
        results = rules.rank_candidates(project(), [], [candidate()])
        assert results[0].rows == []
        assert results[0].ranking_score == 0.0


class TestTeamOptimisation:
    def _ranked(self):
        requirements = [
            requirement(1, 3, 0.6, True, "Python"),
            requirement(2, 3, 0.4, False, "SQL"),
        ]
        candidates = [
            candidate(1, "Aarohi Sharma", levels={1: (4, 4, 4), 2: (4, 4, 4)}),
            candidate(2, "Diya Arora", levels={1: (3, 3, 3)}),
            candidate(3, "Karan Gill", levels={1: (3, 3, 3)}),
            candidate(4, "Naina Kapoor", levels={1: (2, 2, 2)}),
        ]
        return rules.rank_candidates(project(), requirements, candidates)

    def test_team_is_filled_to_the_minimum_even_when_coverage_is_flat(self):
        ranked = self._ranked()
        suggestion = rules.optimise_team(project(), [requirement(1, 3, 1.0)], ranked, team_size_min=3, team_size_max=4)
        assert len(suggestion.members) == 3
        assert suggestion.valid, suggestion.violations

    def test_an_undersized_team_reports_the_violation(self):
        violations = rules.validate_team(minimum=3, maximum=4, members=self._ranked()[:1])
        assert any("at least 3" in violation for violation in violations)

    def test_a_not_eligible_member_invalidates_the_team(self):
        requirements = [requirement(1, 5, 1.0)]
        ranked = rules.rank_candidates(
            project(),
            requirements,
            [candidate(1, "Aarohi Sharma", levels={1: (2, 2, 2)})],
        )
        violations = rules.validate_team(minimum=1, maximum=2, members=ranked)
        assert any("Not eligible" in violation for violation in violations)

    def test_duplicate_members_are_refused(self):
        ranked = self._ranked()
        violations = rules.validate_team(minimum=1, maximum=4, members=[ranked[0], ranked[0]])
        assert any("more than once" in violation for violation in violations)


@hypothesis_settings(max_examples=60, deadline=None)
@given(
    required=st.integers(min_value=2, max_value=5),
    required_sql=st.integers(min_value=1, max_value=5),
    weight=st.floats(min_value=0.05, max_value=0.95),
    soft_points=st.floats(min_value=0.0, max_value=100.0),
)
def test_property_hard_constraint_dominates_any_soft_score(
    required, required_sql, weight, soft_points
):
    """No soft score can ever lift a candidate above an eligible one."""
    other_weight = round(1.0 - weight, 3)
    if other_weight <= 0:
        other_weight = 0.001
    requirements = [
        requirement(1, required, weight, True, "Python"),
        requirement(2, required_sql, other_weight, False, "SQL"),
    ]
    eligible = candidate(
        1,
        "Aarohi Sharma",
        levels={1: (required, required, required), 2: (min(5, required_sql + 2),) * 3},
    )
    failing = candidate(
        2,
        "Karan Gill",
        levels={1: (max(1, required - 1),) * 3, 2: (5, 5, 5)},
    )
    ranked = rules.rank_candidates(project(), requirements, [failing, eligible])
    assert ranked[0].student_id == 1
    assert ranked[0].eligibility == rules.ELIGIBLE
    assert ranked[1].eligibility == rules.NOT_ELIGIBLE


class TestAvailabilityPolicy:
    def test_unknown_is_not_unavailable(self):
        check = availability.check(
            available_from=None,
            available_to=None,
            student_timezone="Asia/Kolkata",
            project_start=TODAY,
            project_deadline=TODAY + timedelta(days=10),
            project_timezone="Asia/Kolkata",
        )
        assert check.status == availability.STATUS_UNKNOWN
        assert check.is_hard_failure is False

    def test_overlap_is_inclusive(self):
        assert (
            availability.overlap_days(
                TODAY, TODAY, TODAY, TODAY + timedelta(days=10)
            )
            == 1
        )

    @pytest.mark.parametrize("zone", ["Asia/Kolkata", "Europe/London", "UTC"])
    def test_valid_timezones_are_accepted(self, zone):
        assert availability.is_valid_timezone(zone)

    @pytest.mark.parametrize("value", ["", "not a zone", "Asia/", "123", None])
    def test_malformed_timezones_are_rejected(self, value):
        assert not availability.is_valid_timezone(value)
