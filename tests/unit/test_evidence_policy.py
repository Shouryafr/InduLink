"""The evidence policy: the single rule that decides what a level means.

This module is the reason the product can show two scores instead of one, so it
is tested exhaustively — including the cases that would be tempting to fudge.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given, settings as hypothesis_settings
from hypothesis import strategies as st

from core import evidence_policy as policy

TODAY = date(2026, 10, 28)


def claim(
    evidence_id: int = 1,
    *,
    category: str = "practical_task",
    status: str = "reviewed",
    level: int | None = 3,
    when: date | None = None,
    expiry: date | None = None,
) -> policy.EvidenceClaim:
    return policy.EvidenceClaim(
        evidence_id=evidence_id,
        category=category,
        status=status,
        demonstrated_level=level,
        evidence_date=when or TODAY - timedelta(days=30),
        expiry_date=expiry,
    )


class TestReviewedEvidence:
    def test_reviewed_sets_confirmed_level(self):
        result = policy.evaluate_skill(3, [claim(level=4)], today=TODAY)
        assert result.confirmed_level == 4
        assert result.label == policy.LABEL_REVIEWED
        assert result.effective_level() == 4

    def test_verified_outranks_reviewed_label(self):
        result = policy.evaluate_skill(
            3,
            [claim(1, level=3), claim(2, level=5, status="verified")],
            today=TODAY,
        )
        assert result.confirmed_level == 5
        assert result.label == policy.LABEL_VERIFIED

    def test_highest_confirmed_level_wins(self):
        result = policy.evaluate_skill(
            2, [claim(1, level=5), claim(2, level=3)], today=TODAY
        )
        assert result.confirmed_level == 5
        assert result.label == policy.LABEL_REVIEWED


class TestCertificates:
    def test_certificate_alone_never_confirms_a_practical_level(self):
        result = policy.evaluate_skill(
            3, [claim(1, category="certificate", status="verified", level=5)], today=TODAY
        )
        assert result.confirmed_level is None
        assert result.label == policy.LABEL_PROVISIONAL
        assert result.provisional_level == 5
        assert any("does not confirm" in note for note in result.notes)

    def test_expired_certificate_is_labelled_and_not_confirmed(self):
        result = policy.evaluate_skill(
            4,
            [
                claim(
                    1,
                    category="certificate",
                    status="reviewed",
                    level=5,
                    expiry=TODAY - timedelta(days=1),
                )
            ],
            today=TODAY,
        )
        assert result.confirmed_level is None
        assert result.expired_certification_ids == (1,)
        assert any("expired" in note for note in result.notes)

    def test_certificate_plus_practical_evidence_confirms_practical(self):
        result = policy.evaluate_skill(
            3,
            [
                claim(1, category="certificate", status="reviewed", level=5),
                claim(2, category="practical_task", status="reviewed", level=3),
            ],
            today=TODAY,
        )
        assert result.confirmed_level == 3
        assert result.provisional_level == 5


class TestDeclaredOnly:
    def test_declared_only_is_provisional(self):
        result = policy.evaluate_skill(3, [], today=TODAY)
        assert result.confirmed_level is None
        assert result.provisional_level == 3
        assert result.label == policy.LABEL_PROVISIONAL

    def test_unreviewed_evidence_stays_provisional(self):
        result = policy.evaluate_skill(
            2, [claim(1, status="submitted", level=4)], today=TODAY
        )
        assert result.confirmed_level is None
        assert result.provisional_level == 4
        assert result.label == policy.LABEL_PROVISIONAL

    def test_declared_outranks_lower_unreviewed_claim(self):
        result = policy.evaluate_skill(
            4, [claim(1, status="submitted", level=2)], today=TODAY
        )
        assert result.provisional_level == 4


class TestNothingKnown:
    def test_nothing_is_unknown_not_zero(self):
        result = policy.evaluate_skill(None, [], today=TODAY)
        assert result.label == policy.LABEL_UNKNOWN
        assert result.confirmed_level is None
        assert result.provisional_level is None
        assert result.effective_level() is None

    def test_rejected_only_is_rejected(self):
        result = policy.evaluate_skill(
            None, [claim(1, status="rejected", level=3)], today=TODAY
        )
        assert result.label == policy.LABEL_REJECTED
        assert result.effective_level() is None
        assert result.rejected_evidence_ids == (1,)

    def test_rejected_evidence_does_not_strip_declared_level(self):
        result = policy.evaluate_skill(
            3, [claim(1, status="rejected", level=5)], today=TODAY
        )
        assert result.label == policy.LABEL_PROVISIONAL
        assert result.provisional_level == 3


class TestMandatoryChecks:
    def test_confirmed_at_threshold_meets(self):
        assessed = policy.evaluate_skill(3, [claim(level=3)], today=TODAY)
        assert policy.check_requirement(assessed, 3) == policy.OUTCOME_MET

    def test_confirmed_below_threshold_fails(self):
        assessed = policy.evaluate_skill(3, [claim(level=2)], today=TODAY)
        assert policy.check_requirement(assessed, 3) == policy.OUTCOME_FAILED

    def test_provisional_above_threshold_is_provisional(self):
        assessed = policy.evaluate_skill(4, [], today=TODAY)
        assert policy.check_requirement(assessed, 3) == policy.OUTCOME_PROVISIONAL

    def test_provisional_below_threshold_fails(self):
        assessed = policy.evaluate_skill(1, [], today=TODAY)
        assert policy.check_requirement(assessed, 3) == policy.OUTCOME_FAILED

    def test_unknown_is_no_data(self):
        assessed = policy.evaluate_skill(None, [], today=TODAY)
        assert policy.check_requirement(assessed, 3) == policy.OUTCOME_NO_DATA

    def test_invalid_required_level_raises(self):
        assessed = policy.evaluate_skill(3, [], today=TODAY)
        with pytest.raises(ValueError):
            policy.check_requirement(assessed, 0)


class TestEligibility:
    def test_all_met_is_eligible(self):
        outcomes = [policy.OUTCOME_MET, policy.OUTCOME_MET]
        assert policy.determine_eligibility(outcomes) == policy.ELIGIBLE

    def test_any_failed_is_not_eligible(self):
        outcomes = [policy.OUTCOME_MET, policy.OUTCOME_FAILED]
        assert policy.determine_eligibility(outcomes) == policy.NOT_ELIGIBLE

    def test_provisional_is_provisionally_eligible(self):
        assert (
            policy.determine_eligibility([policy.OUTCOME_MET, policy.OUTCOME_PROVISIONAL])
            == policy.PROVISIONALLY_ELIGIBLE
        )

    def test_no_data_is_provisionally_eligible(self):
        assert (
            policy.determine_eligibility([policy.OUTCOME_NO_DATA])
            == policy.PROVISIONALLY_ELIGIBLE
        )

    def test_empty_is_eligible(self):
        assert policy.determine_eligibility([]) == policy.ELIGIBLE

    def test_failure_dominates_provisional(self):
        outcomes = [policy.OUTCOME_PROVISIONAL, policy.OUTCOME_FAILED]
        assert policy.determine_eligibility(outcomes) == policy.NOT_ELIGIBLE


class TestContribution:
    def test_unknown_contributes_nothing(self):
        assert policy.contribution(None, 3, 0.5) == 0.0

    def test_capped_at_required_level(self):
        assert policy.contribution(5, 3, 0.5) == policy.contribution(3, 3, 0.5)

    def test_half_level_scores_half(self):
        assert policy.contribution(2, 4, 0.5) == pytest.approx(25.0)

    def test_zero_weight_scores_zero(self):
        assert policy.contribution(4, 3, 0.0) == 0.0

    def test_score_clamped(self):
        assert policy.score_from_components([200.0]) == 100.0
        assert policy.score_from_components([-10.0]) == 0.0


@hypothesis_settings(max_examples=200, deadline=None)
@given(
    level=st.integers(min_value=1, max_value=5),
    required=st.integers(min_value=1, max_value=5),
    weight=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_contribution_always_within_bounds(level, required, weight):
    points = policy.contribution(level, required, weight)
    assert 0.0 <= points <= 100.0


@hypothesis_settings(max_examples=200, deadline=None)
@given(
    required=st.integers(min_value=1, max_value=5),
    weight=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_contribution_is_monotonic_in_level(required, weight):
    points = [policy.contribution(level, required, weight) for level in range(1, 6)]
    assert points == sorted(points)


@hypothesis_settings(max_examples=100, deadline=None)
@given(
    declared=st.one_of(st.none(), st.integers(min_value=1, max_value=5)),
    statuses=st.lists(
        st.sampled_from(sorted(policy.EVIDENCE_STATUSES)), max_size=4
    ),
    categories=st.lists(
        st.sampled_from(sorted(policy.EVIDENCE_CATEGORIES)), max_size=4
    ),
)
def test_policy_never_invents_a_level(declared, statuses, categories):
    claims = [
        policy.EvidenceClaim(
            evidence_id=index,
            category=categories[index % len(categories)] if categories else "practical_task",
            status=status,
            demonstrated_level=3,
            evidence_date=TODAY,
        )
        for index, status in enumerate(statuses)
    ]
    result = policy.evaluate_skill(declared, claims, today=TODAY)
    if result.confirmed_level is None:
        assert result.effective_level() == result.provisional_level
    assert result.confirmed_level is None or result.confirmed_level == 3


class TestEvidenceAge:
    def test_age_in_days(self):
        assert policy.evidence_age_days(TODAY - timedelta(days=10), TODAY) == 10

    def test_future_date_clamps_to_zero(self):
        assert policy.evidence_age_days(TODAY + timedelta(days=5), TODAY) == 0

    @pytest.mark.parametrize(
        "days,tone",
        [(30, "ok"), (200, "info"), (400, "warn")],
    )
    def test_age_badge(self, days, tone):
        _label, badge_tone = policy.age_badge(days)
        assert badge_tone == tone

    def test_needs_revalidation_after_a_year(self):
        assert policy.needs_revalidation(TODAY - timedelta(days=400), TODAY) is True
        assert policy.needs_revalidation(TODAY - timedelta(days=30), TODAY) is False

    def test_expiry(self):
        assert policy.is_expired(TODAY - timedelta(days=1), TODAY) is True
        assert policy.is_expired(None, TODAY) is False
        assert policy.is_expired(TODAY + timedelta(days=1), TODAY) is False
