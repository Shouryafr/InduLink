"""Canonical skill names, alias resolution and level definitions."""

from __future__ import annotations

import pytest

from core import skills


class TestNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("JavaScript", "javascript"),
            ("java script", "java script"),
            ("Node.js", "node js"),
            ("  C++  ", "c"),
            ("Data  Modelling", "data modelling"),
            ("R&D", "r and d"),
        ],
    )
    def test_normalise(self, raw, expected):
        assert skills.normalize_skill_name(raw) == expected

    def test_empty_is_empty(self):
        assert skills.normalize_skill_name("") == ""
        assert skills.normalize_skill_name(None) == ""

    def test_case_and_punctuation_match(self):
        assert skills.normalize_skill_name("Node.js") == skills.normalize_skill_name("node js")


class TestResolution:
    CANONICAL = {
        skills.normalize_skill_name("JavaScript"): (1, "JavaScript"),
        skills.normalize_skill_name("Python"): (2, "Python"),
    }
    ALIASES = {skills.normalize_skill_name("js"): 1}

    def test_exact_canonical(self):
        resolved = skills.resolve_typed_skill("Python", self.CANONICAL, self.ALIASES)
        assert resolved is not None
        assert resolved.skill_id == 2
        assert resolved.match == "canonical"

    def test_alias_resolves_to_canonical(self):
        resolved = skills.resolve_typed_skill("JS", self.CANONICAL, self.ALIASES)
        assert resolved is not None
        assert resolved.skill_id == 1
        assert resolved.skill_name == "JavaScript"
        assert resolved.match == "alias"

    def test_unknown_returns_none(self):
        assert skills.resolve_typed_skill("COBOL", self.CANONICAL, self.ALIASES) is None

    def test_blank_returns_none(self):
        assert skills.resolve_typed_skill("   ", self.CANONICAL, self.ALIASES) is None

    def test_alias_pointing_nowhere_raises(self):
        with pytest.raises(KeyError):
            skills.resolve_typed_skill("ghost", self.CANONICAL, {"ghost": 99})


class TestLevels:
    @pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
    def test_valid_levels(self, level):
        assert skills.is_valid_level(level) is True
        assert skills.level_definition(level)

    @pytest.mark.parametrize("level", [0, 6, -1, None, "3"])
    def test_invalid_levels(self, level):
        assert skills.is_valid_level(level) is False

    def test_definition_raises_for_invalid(self):
        with pytest.raises(ValueError):
            skills.level_definition(0)


class TestDisplayAndResolutionState:
    def test_display_collapses_whitespace(self):
        assert skills.display_name("  Data   Modelling ") == "Data Modelling"

    def test_display_preserves_the_author_casing(self):
        assert skills.display_name("SQL") == "SQL"
        assert skills.display_name("JavaScript") == "JavaScript"

    def test_display_of_blank_is_blank(self):
        assert skills.display_name("   ") == ""
        assert skills.display_name(None) == ""

    def test_unresolved_match_reports_not_resolved(self):
        resolved = skills.ResolvedSkill(skill_id=0, skill_name="", match="none")
        assert resolved.resolved is False

    def test_canonical_match_reports_resolved(self):
        resolved = skills.resolve_typed_skill("Python", TestResolution.CANONICAL, {})
        assert resolved is not None and resolved.resolved is True
