"""Canonical skill registry helpers (brief section 5, section 7.3).

Skills are chosen from one canonical registry. Aliases map equivalent labels
(``JS``, ``java script``, ``JavaScript``) onto a single skill id, so a typed
alias never creates a duplicate skill.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_NON_WORD = re.compile(r"[^a-z0-9]+")

# Skill categories used by the registry screens and the demand dashboard.
CATEGORIES: tuple[str, ...] = (
    "software",
    "data",
    "security",
    "embedded",
    "design",
    "delivery",
    "domain",
)

LEVEL_DEFINITIONS: dict[int, str] = {
    1: "Awareness — recognises the vocabulary; has read about it, not used it.",
    2: "Assisted — has used it with guidance or in coursework.",
    3: "Independent — has delivered a real task in it without close supervision.",
    4: "Advanced — has led design or delivery decisions in it, and reviewed others' work.",
    5: "Expert — can define practice for a team, handle novel problems, and mentor others.",
}

LEVEL_MIN = 1
LEVEL_MAX = 5


def normalize_skill_name(raw: str) -> str:
    """Canonical lookup key for a skill label.

    Lower-cases, normalises unicode, and collapses punctuation and spacing so
    ``Node.js`` and ``node js`` share the key ``node js``.
    """
    if raw is None:
        return ""
    text = unicodedata.normalize("NFKD", raw.strip().lower())
    text = text.replace("&", " and ")
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.split())


def display_name(raw: str) -> str:
    """Collapse whitespace for display, preserving the author's casing."""
    cleaned = " ".join((raw or "").split())
    if not cleaned:
        return ""
    if cleaned.isupper() and len(cleaned) <= 5:
        return cleaned
    return cleaned


def is_valid_level(level: int | None) -> bool:
    return isinstance(level, int) and LEVEL_MIN <= level <= LEVEL_MAX


def level_definition(level: int) -> str:
    if not is_valid_level(level):
        raise ValueError(f"Proficiency level must be {LEVEL_MIN}-{LEVEL_MAX}, got {level!r}")
    return LEVEL_DEFINITIONS[level]


@dataclass(frozen=True)
class ResolvedSkill:
    """Result of resolving a typed label against the registry."""

    skill_id: int
    skill_name: str
    match: str  # "canonical" | "alias" | "none"
    matched_label: str = ""

    @property
    def resolved(self) -> bool:
        return self.match != "none"


def resolve_typed_skill(
    typed: str,
    canonical: dict[str, tuple[int, str]],
    aliases: dict[str, int],
) -> ResolvedSkill | None:
    """Resolve a typed label to a registry skill.

    ``canonical`` maps ``normalize_skill_name(name) -> (id, name)`` and
    ``aliases`` maps ``normalize_skill_name(alias) -> id``. Returns ``None``
    when the label is unknown, so the caller can offer "propose this skill to
    an administrator" rather than guessing (brief section 7.3).
    """
    key = normalize_skill_name(typed)
    if not key:
        return None
    if key in canonical:
        skill_id, name = canonical[key]
        return ResolvedSkill(skill_id, name, "canonical", typed)
    if key in aliases:
        skill_id = aliases[key]
        for canon_key, (cand_id, name) in canonical.items():
            if cand_id == skill_id:
                return ResolvedSkill(skill_id, name, "alias", typed)
        raise KeyError(f"Alias points at unknown skill id {skill_id}")
    return None
