"""Weight normalisation, requirement validation and the project state machine."""

from __future__ import annotations

import pytest

from core.requirements import (
    ALLOWED_TRANSITIONS,
    ProjectState,
    RequirementDraft,
    TransitionError,
    allowed_next_states,
    as_percentages,
    can_transition,
    ensure_transition,
    mandatory_gaps,
    normalize_weights,
    validate_requirement_set,
    weights_valid,
)


class TestWeights:
    def test_relative_importance_is_normalised(self):
        assert normalize_weights([3, 1]) == [0.75, 0.25]

    def test_sum_is_exactly_one(self):
        weights = normalize_weights([1, 1, 1])
        assert round(sum(weights), 3) == 1.0

    def test_remainder_absorbed_by_largest(self):
        weights = normalize_weights([1, 1, 1])
        assert round(sum(weights), 3) == 1.0
        assert weights_valid(weights)

    @pytest.mark.parametrize("raw", [[0], [-1], []])
    def test_rejects_non_positive(self, raw):
        with pytest.raises(ValueError):
            normalize_weights(raw)

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError):
            normalize_weights(["a"])  # type: ignore[list-item]

    def test_rejects_bool(self):
        with pytest.raises(ValueError):
            normalize_weights([True, 2])  # type: ignore[list-item]

    def test_weights_valid_tolerates_rounding(self):
        assert weights_valid([0.333, 0.333, 0.334]) is True

    def test_weights_valid_rejects_wrong_total(self):
        assert weights_valid([0.5, 0.4]) is False

    def test_weights_valid_rejects_empty(self):
        assert weights_valid([]) is False


class TestPercentages:
    def test_adds_to_one_hundred(self):
        assert sum(as_percentages([0.333, 0.333, 0.334])) == 100

    def test_simple_split(self):
        assert as_percentages([0.5, 0.5]) == [50, 50]

    def test_empty(self):
        assert as_percentages([]) == []

    def test_rounding_never_shows_99(self):
        assert sum(as_percentages([1 / 3, 1 / 3, 1 / 3])) == 100


class TestStateMachine:
    @pytest.mark.parametrize(
        "current,target",
        [
            ("draft", "pending_approval"),
            ("pending_approval", "approved"),
            ("approved", "open"),
            ("open", "active"),
            ("active", "completed"),
            ("draft", "cancelled"),
        ],
    )
    def test_legal_transitions(self, current, target):
        assert can_transition(current, target) is True

    @pytest.mark.parametrize(
        "current,target",
        [
            ("draft", "active"),
            ("draft", "approved"),
            ("completed", "open"),
            ("cancelled", "draft"),
            ("pending_approval", "open"),
            ("open", "approved"),
        ],
    )
    def test_illegal_transitions(self, current, target):
        assert can_transition(current, target) is False

    def test_unknown_state_is_refused_not_coerced(self):
        assert can_transition("nonsense", "open") is False
        assert allowed_next_states("nonsense") == []

    def test_ensure_transition_raises(self):
        with pytest.raises(TransitionError):
            ensure_transition("draft", "completed")

    def test_final_states_have_no_successors(self):
        assert ALLOWED_TRANSITIONS[ProjectState.COMPLETED] == frozenset()
        assert ALLOWED_TRANSITIONS[ProjectState.CANCELLED] == frozenset()


class TestRequirementSet:
    def test_requires_at_least_one(self):
        weights, errors = validate_requirement_set([])
        assert weights == []
        assert errors

    def test_valid_set_normalises(self):
        drafts = [
            RequirementDraft(skill_id=1, required_level=3, weight_raw=3, is_mandatory=True),
            RequirementDraft(skill_id=2, required_level=2, weight_raw=1, is_mandatory=False),
        ]
        weights, errors = validate_requirement_set(drafts)
        assert errors == {}
        assert weights == [0.75, 0.25]

    def test_duplicate_skill_rejected(self):
        drafts = [
            RequirementDraft(skill_id=1, required_level=3, weight_raw=3),
            RequirementDraft(skill_id=1, required_level=4, weight_raw=2),
        ]
        _weights, errors = validate_requirement_set(drafts)
        assert 1 in errors
        assert any("already listed" in message for message in errors[1])

    def test_invalid_level_rejected(self):
        drafts = [RequirementDraft(skill_id=1, required_level=9, weight_raw=1)]
        _weights, errors = validate_requirement_set(drafts)
        assert 0 in errors

    def test_missing_skill_rejected(self):
        drafts = [RequirementDraft(skill_id=None, required_level=3, weight_raw=1)]
        _weights, errors = validate_requirement_set(drafts)
        assert 0 in errors

    def test_non_positive_weight_rejected(self):
        drafts = [RequirementDraft(skill_id=1, required_level=3, weight_raw=0)]
        _weights, errors = validate_requirement_set(drafts)
        assert 0 in errors


class TestMandatoryGaps:
    def test_names_unmet_mandatory_only(self):
        requirements = [("Python", 3, True), ("SQL", 4, False)]
        gaps = mandatory_gaps(requirements, {"Python": 2, "SQL": 1})
        assert gaps == ["Python"]

    def test_unknown_is_not_a_gap(self):
        gaps = mandatory_gaps([("Python", 3, True)], {"Python": None})
        assert gaps == []

    def test_met_requirement_is_not_a_gap(self):
        gaps = mandatory_gaps([("Python", 3, True)], {"Python": 4})
        assert gaps == []
