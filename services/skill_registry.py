"""Canonical skill registry (brief sections 5, 7.3, 8.5).

One list of canonical skills, one alias table. Students and mentors pick from
the registry; a label that resolves through an alias lands on the same skill id,
so equivalent wording never fragments the data.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import skills as skill_rules
from data.models import ProficiencyLevel, Skill, SkillAlias, SkillProposal, User
from services import audit_log
from services.errors import Conflict, NotFound, PermissionDenied, ValidationError


@dataclass
class Registry:
    """An in-memory view of the registry, built once per request."""

    items: list[Skill]
    by_key: dict[str, Skill]
    by_id: dict[int, Skill]
    alias_to_id: dict[str, int]

    def resolve(self, typed: str):
        return skill_rules.resolve_typed_skill(
            typed,
            {key: (skill.id, skill.canonical_name) for key, skill in self.by_key.items()},
            self.alias_to_id,
        )

    def get(self, skill_id: int) -> Skill | None:
        return self.by_id.get(skill_id)

    def name_of(self, skill_id: int) -> str:
        skill = self.by_id.get(skill_id)
        return skill.canonical_name if skill else "Unknown skill"


def load(session: Session) -> Registry:
    items = list(session.scalars(select(Skill).order_by(Skill.canonical_name)).all())
    alias_rows = list(session.scalars(select(SkillAlias)).all())
    by_key = {skill_rules.normalize_skill_name(s.canonical_name): s for s in items}
    by_id = {s.id: s for s in items}
    alias_to_id = {skill_rules.normalize_skill_name(a.alias): a.skill_id for a in alias_rows}
    return Registry(items=items, by_key=by_key, by_id=by_id, alias_to_id=alias_to_id)


def ensure_levels(session: Session) -> None:
    """Write the five written level definitions. Idempotent."""
    existing = {row.level for row in session.scalars(select(ProficiencyLevel)).all()}
    names = {1: "Awareness", 2: "Assisted", 3: "Independent", 4: "Advanced", 5: "Expert"}
    for level, definition in skill_rules.LEVEL_DEFINITIONS.items():
        if level in existing:
            continue
        session.add(
            ProficiencyLevel(level=level, name=names[level], definition=definition)
        )


def create_skill(
    session: Session,
    *,
    name: str,
    category: str,
    actor: User,
    aliases: list[str] | None = None,
    ip: str = "",
) -> Skill:
    if not (actor and actor.role == "admin"):
        raise PermissionDenied("Only an administrator can change the skill registry.")
    cleaned = skill_rules.display_name(name)
    if not cleaned:
        raise ValidationError("Enter a skill name.", field_errors={"name": ["Enter a skill name."]})
    if category not in skill_rules.CATEGORIES:
        raise ValidationError(
            "Choose a category.", field_errors={"category": ["Choose a category."]}
        )

    registry = load(session)
    if skill_rules.normalize_skill_name(cleaned) in registry.by_key:
        raise Conflict(
            "That skill already exists in the registry.",
            field_errors={"name": ["That skill already exists in the registry."]},
        )
    key = skill_rules.normalize_skill_name(cleaned)
    if key in registry.alias_to_id:
        raise Conflict(
            "That name is already an alias for another skill.",
            field_errors={"name": ["That name is already an alias for another skill."]},
        )

    skill = Skill(canonical_name=cleaned, category=category)
    session.add(skill)
    session.flush()

    for alias in aliases or []:
        add_alias(session, skill=skill, alias=alias, actor=actor, ip=ip)

    audit_log.record(
        session,
        actor=actor,
        action="skill.created",
        target_type="skill",
        target_id=skill.id,
        detail={"name": cleaned, "category": category},
        ip=ip,
    )
    return skill


def add_alias(
    session: Session, *, skill: Skill, alias: str, actor: User, ip: str = ""
) -> SkillAlias:
    if not (actor and actor.role == "admin"):
        raise PermissionDenied("Only an administrator can change the skill registry.")
    cleaned = skill_rules.display_name(alias)
    key = skill_rules.normalize_skill_name(cleaned)
    if not key:
        raise ValidationError("Enter an alias.", field_errors={"alias": ["Enter an alias."]})
    registry = load(session)
    if key in registry.by_key:
        existing = registry.by_key[key]
        if existing.id == skill.id:
            raise Conflict("That is already this skill's canonical name.")
        raise Conflict("That name belongs to another canonical skill.")

    row = SkillAlias(skill_id=skill.id, alias=cleaned)
    session.add(row)
    audit_log.record(
        session,
        actor=actor,
        action="skill.alias_added",
        target_type="skill",
        target_id=skill.id,
        after_ref=cleaned,
        ip=ip,
    )
    return row


def propose_skill(
    session: Session,
    *,
    actor: User,
    name: str,
    category: str,
    note: str = "",
) -> SkillProposal:
    """A student or mentor asks for a missing skill rather than guessing one."""
    cleaned = skill_rules.display_name(name)
    if not cleaned:
        raise ValidationError("Enter the skill name.", field_errors={"name": ["Enter the skill name."]})
    if category not in skill_rules.CATEGORIES:
        category = "software"
    proposal = SkillProposal(
        proposed_by=actor.id,
        proposed_name=cleaned[:120],
        category=category,
        note=(note or "")[:1000],
    )
    session.add(proposal)
    audit_log.record(
        session,
        actor=actor,
        action="skill.proposed",
        target_type="skill_proposal",
        target_id="",
        detail={"name": cleaned, "category": category},
    )
    return proposal


def get_skill_or_404(session: Session, skill_id: int) -> Skill:
    skill = session.get(Skill, skill_id)
    if skill is None:
        raise NotFound("That skill is not in the registry.")
    return skill
