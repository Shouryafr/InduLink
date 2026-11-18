"""The matching and team-optimisation rules.

Hard constraints come first, always. A candidate who fails a mandatory
requirement or whose availability does not overlap the project is *not
eligible*, and no amount of soft score can move them above an eligible
candidate. This module is pure: no database, no clock, no randomness, so the
same inputs always produce the same ranking and every number can be explained.

Unknown data is never scored as zero. A missing level contributes no points and
is reported as "no data"; a missing availability window makes the candidate
provisional rather than unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from core import availability, evidence_policy

ELIGIBLE = evidence_policy.ELIGIBLE
PROVISIONALLY_ELIGIBLE = evidence_policy.PROVISIONALLY_ELIGIBLE
NOT_ELIGIBLE = evidence_policy.NOT_ELIGIBLE

ELIGIBILITY_ORDER = {
    ELIGIBLE: 0,
    PROVISIONALLY_ELIGIBLE: 1,
    NOT_ELIGIBLE: 2,
}

HARD_CONSTRAINT_LABEL = "Hard constraints"
SOFT_SCORE_LABEL = "Weighted skill evidence"


@dataclass(frozen=True)
class ProjectView:
    """Everything the rules need to know about a challenge."""

    project_id: int
    title: str
    start_date: date
    deadline: date
    timezone: str = "Asia/Kolkata"
    team_size_min: int = 2
    team_size_max: int = 4


@dataclass(frozen=True)
class RequirementView:
    skill_id: int
    skill_name: str
    required_level: int
    weight: float
    is_mandatory: bool = False


@dataclass(frozen=True)
class CandidateSkill:
    skill_id: int
    declared_level: int | None
    confirmed_level: int | None
    provisional_level: int | None
    label: str

    @property
    def effective_level(self) -> int | None:
        if self.confirmed_level is not None:
            return self.confirmed_level
        return self.provisional_level

    @property
    def is_unknown(self) -> bool:
        return self.effective_level is None


@dataclass(frozen=True)
class Candidate:
    student_id: int
    display_name: str
    skills: dict[int, CandidateSkill] = field(default_factory=dict)
    availability_hours: int | None = None
    availability_from: date | None = None
    availability_to: date | None = None
    availability_timezone: str = "Asia/Kolkata"
    visibility: str = "private"
    headline: str = ""
    evidence_count: int = 0

    def skill(self, skill_id: int) -> CandidateSkill:
        return self.skills.get(skill_id) or CandidateSkill(
            skill_id=skill_id,
            declared_level=None,
            confirmed_level=None,
            provisional_level=None,
            label=evidence_policy.LABEL_UNKNOWN,
        )


@dataclass(frozen=True)
class RequirementRow:
    skill_id: int
    skill_name: str
    required_level: int
    weight: float
    is_mandatory: bool
    level: int | None
    label: str
    outcome: str
    points: float
    note: str

    @property
    def met(self) -> bool:
        return self.outcome == evidence_policy.OUTCOME_MET

    @property
    def tone(self) -> str:
        return {
            evidence_policy.OUTCOME_MET: "ok",
            evidence_policy.OUTCOME_PROVISIONAL: "warn",
            evidence_policy.OUTCOME_FAILED: "risk",
            evidence_policy.OUTCOME_NO_DATA: "unknown",
        }.get(self.outcome, "unknown")

    @property
    def outcome_text(self) -> str:
        return {
            evidence_policy.OUTCOME_MET: "Met",
            evidence_policy.OUTCOME_PROVISIONAL: "Evidence needed",
            evidence_policy.OUTCOME_FAILED: "Below the required level",
            evidence_policy.OUTCOME_NO_DATA: "No data",
        }.get(self.outcome, self.outcome)


@dataclass(frozen=True)
class ConstraintRow:
    key: str
    label: str
    status: str
    detail: str
    hard: bool = True

    @property
    def tone(self) -> str:
        return {
            "met": "ok",
            "caveat": "warn",
            "failed": "risk",
            "unknown": "unknown",
        }.get(self.status, "unknown")

    @property
    def outcome_text(self) -> str:
        return {
            "met": "Met",
            "caveat": "Caveat",
            "failed": "Not met",
            "unknown": "Unverified",
        }.get(self.status, self.status)


@dataclass
class MatchResult:
    candidate: Candidate
    eligibility: str
    confirmed_score: float
    provisional_score: float
    ranking_score: float
    rows: list[RequirementRow]
    constraints: list[ConstraintRow]
    reasons: list[str]
    caveats: list[str]
    rank: int = 0
    tied: bool = False

    @property
    def student_id(self) -> int:
        return self.candidate.student_id

    @property
    def display_name(self) -> str:
        return self.candidate.display_name

    @property
    def eligibility_text(self) -> str:
        return evidence_policy.ELIGIBILITY_TEXT.get(self.eligibility, self.eligibility)

    @property
    def eligibility_tone(self) -> str:
        return evidence_policy.ELIGIBILITY_TONE.get(self.eligibility, "unknown")

    @property
    def score(self) -> float:
        return self.ranking_score

    @property
    def mandatory_failures(self) -> list[RequirementRow]:
        return [row for row in self.rows if row.is_mandatory and row.outcome == evidence_policy.OUTCOME_FAILED]

    @property
    def mandatory_gaps(self) -> list[RequirementRow]:
        return [
            row
            for row in self.rows
            if row.is_mandatory
            and row.outcome in {evidence_policy.OUTCOME_NO_DATA, evidence_policy.OUTCOME_PROVISIONAL}
        ]

    @property
    def evidence_needed(self) -> list[RequirementRow]:
        return [row for row in self.rows if row.outcome == evidence_policy.OUTCOME_NO_DATA]


def _skill_row(requirement: RequirementView, candidate: Candidate) -> RequirementRow:
    skill = candidate.skill(requirement.skill_id)
    outcome = evidence_policy.check_requirement(
        evidence_policy.SkillAssessment(
            declared_level=skill.declared_level,
            confirmed_level=skill.confirmed_level,
            provisional_level=skill.provisional_level,
            label=skill.label,
        ),
        requirement.required_level,
    )
    level = skill.effective_level
    points = evidence_policy.contribution(level, requirement.required_level, requirement.weight)

    if outcome == evidence_policy.OUTCOME_MET:
        note = (
            f"Confirmed level {skill.confirmed_level} meets the required level "
            f"{requirement.required_level}; {points:.1f} points from a {requirement.weight:.0%} weight."
        )
    elif outcome == evidence_policy.OUTCOME_PROVISIONAL:
        note = (
            f"Level {level} is provisional or has no reviewed evidence, so it cannot satisfy a "
            f"mandatory requirement on its own. {points:.1f} provisional points."
        )
    elif outcome == evidence_policy.OUTCOME_FAILED:
        note = (
            f"Level {level} is below the required level {requirement.required_level}. "
            f"The hard check fails; the shortfall is not offset by other skills."
        )
    else:
        note = (
            "No declared or reviewed level for this skill. Shown as no data, never as zero, "
            "and it contributes no points."
        )

    return RequirementRow(
        skill_id=requirement.skill_id,
        skill_name=requirement.skill_name,
        required_level=requirement.required_level,
        weight=requirement.weight,
        is_mandatory=requirement.is_mandatory,
        level=level,
        label=skill.label,
        outcome=outcome,
        points=points,
        note=note,
    )


def evaluate_candidate(
    project: ProjectView,
    requirements: list[RequirementView],
    candidate: Candidate,
    *,
    today: date | None = None,
) -> MatchResult:
    """Score one candidate against one project, hard constraints first."""
    rows = [_skill_row(requirement, candidate) for requirement in requirements]
    mandatory_rows = [row for row in rows if row.is_mandatory]

    availability_check = availability.check(
        available_from=candidate.availability_from,
        available_to=candidate.availability_to,
        student_timezone=candidate.availability_timezone,
        project_start=project.start_date,
        project_deadline=project.deadline,
        project_timezone=project.timezone,
    )

    if availability_check.status == availability.STATUS_OUTSIDE:
        eligibility = NOT_ELIGIBLE
    else:
        eligibility = evidence_policy.determine_eligibility(
            [row.outcome for row in mandatory_rows]
        )
        if (
            eligibility == ELIGIBLE
            and availability_check.status == availability.STATUS_UNKNOWN
        ):
            eligibility = PROVISIONALLY_ELIGIBLE

    confirmed = evidence_policy.score_from_components(
        [
            evidence_policy.contribution(
                candidate.skill(row.skill_id).confirmed_level, row.required_level, row.weight
            )
            for row in rows
        ]
    )
    provisional = evidence_policy.score_from_components([row.points for row in rows])
    ranking_score = confirmed if eligibility == ELIGIBLE else provisional

    constraints: list[ConstraintRow] = []
    met = [row for row in mandatory_rows if row.outcome == evidence_policy.OUTCOME_MET]
    if mandatory_rows:
        if len(met) == len(mandatory_rows):
            status, detail = (
                "met",
                f"All {len(mandatory_rows)} mandatory requirement(s) are met with confirmed evidence.",
            )
        elif eligibility == NOT_ELIGIBLE:
            failed = [row.skill_name for row in mandatory_rows if row.outcome == evidence_policy.OUTCOME_FAILED]
            status = "failed"
            detail = (
                "Mandatory requirement(s) not met: "
                + ", ".join(failed)
                + ". A higher soft score cannot outrank this."
            )
        else:
            status = "unknown"
            names = [
                row.skill_name
                for row in mandatory_rows
                if row.outcome
                in {evidence_policy.OUTCOME_NO_DATA, evidence_policy.OUTCOME_PROVISIONAL}
            ]
            detail = (
                "Mandatory requirement(s) still need reviewed evidence: "
                + ", ".join(names)
                + "."
            )
    else:
        status, detail = ("met", "This challenge lists no mandatory requirements.")

    constraints.append(
        ConstraintRow(
            key="mandatory_requirements",
            label="Mandatory requirements",
            status=status,
            detail=detail,
        )
    )
    constraints.append(
        ConstraintRow(
            key="availability",
            label="Availability",
            status={
                availability.STATUS_AVAILABLE: "met",
                availability.STATUS_PARTIAL: "caveat",
                availability.STATUS_OUTSIDE: "failed",
                availability.STATUS_UNKNOWN: "unknown",
            }[availability_check.status],
            detail=availability_check.detail,
            hard=availability_check.status == availability.STATUS_OUTSIDE,
        )
    )

    reasons = [row.note for row in rows]
    reasons.append(availability_check.detail)

    caveats: list[str] = []
    if availability_check.status == availability.STATUS_PARTIAL:
        caveats.append(availability_check.detail)
    note = availability.timezone_note(
        candidate.availability_timezone, project.timezone
    )
    if note:
        caveats.append(note)
        constraints.append(
            ConstraintRow(
                key="timezone",
                label="Timezone",
                status="caveat",
                detail=note,
                hard=False,
            )
        )
    if candidate.evidence_count == 0:
        caveats.append(
            "No evidence items are recorded for this student; every level shown is declared only."
        )

    return MatchResult(
        candidate=candidate,
        eligibility=eligibility,
        confirmed_score=confirmed,
        provisional_score=provisional,
        ranking_score=ranking_score,
        rows=rows,
        constraints=constraints,
        reasons=reasons,
        caveats=caveats,
    )


def rank_candidates(
    project: ProjectView,
    requirements: list[RequirementView],
    candidates: list[Candidate],
    *,
    today: date | None = None,
) -> list[MatchResult]:
    """Evaluate and order candidates. Eligible first, then score, then name."""
    results = [
        evaluate_candidate(project, requirements, candidate, today=today)
        for candidate in candidates
    ]
    # Names are display text only. Ordering between equal scores uses the stable
    # record id, so renaming a person can never change a ranking.
    results.sort(
        key=lambda result: (
            ELIGIBILITY_ORDER.get(result.eligibility, 9),
            -result.ranking_score,
            result.student_id,
        )
    )
    for index, result in enumerate(results, start=1):
        result.rank = index

    # A tie is two candidates in the same eligibility band with the same score.
    # The order between them is still deterministic (name, then id), and the UI
    # says so rather than implying a difference the data does not support.
    for result in results:
        result.tied = any(
            other is not result
            and other.eligibility == result.eligibility
            and other.ranking_score == result.ranking_score
            for other in results
        )
    return results


# --- Teams ----------------------------------------------------------------


@dataclass
class TeamSuggestion:
    """A proposed team plus the reasons a reviewer can audit."""

    members: list[MatchResult]
    score: float
    covered_skill_ids: list[int]
    gaps: list[str]
    rationale: list[str]
    violations: list[str]
    candidates_considered: int

    @property
    def valid(self) -> bool:
        return not self.violations

    @property
    def student_ids(self) -> list[int]:
        return [member.student_id for member in self.members]


def _coverage_gain(member: MatchResult, covered: set[int]) -> float:
    """How much new requirement coverage one member adds on top of the team."""
    gain = 0.0
    for row in member.rows:
        if row.skill_id in covered:
            continue
        if row.level is None:
            continue
        gain += row.points * min(row.level / max(row.required_level, 1), 1.0)
    return gain


def optimise_team(
    project: ProjectView,
    requirements: list[RequirementView],
    ranked: list[MatchResult],
    *,
    team_size_min: int | None = None,
    team_size_max: int | None = None,
) -> TeamSuggestion:
    """Greedy, explainable team suggestion.

    Greedy rather than exhaustive: the UI must be able to say exactly why each
    member was added. Every step is "the candidate who adds the most uncovered
    requirement points"; ties break on the overall score, then name.
    """
    minimum = team_size_min if team_size_min is not None else project.team_size_min
    maximum = team_size_max if team_size_max is not None else project.team_size_max
    pool = [r for r in ranked if r.eligibility != NOT_ELIGIBLE]
    chosen: list[MatchResult] = []
    covered: set[int] = set()
    rationale: list[str] = []

    while len(chosen) < maximum:
        remaining = [result for result in pool if result not in chosen]
        if not remaining:
            break
        best = max(
            remaining,
            key=lambda result: (
                round(_coverage_gain(result, covered), 4),
                result.ranking_score,
                -result.student_id,
            ),
        )
        gain = round(_coverage_gain(best, covered), 1)
        # Team size is itself a hard constraint, so keep filling to the minimum
        # even when a member adds no new skill coverage. Stop early only once
        # the team is legally sized and further members add nothing.
        if chosen and gain <= 0 and len(chosen) >= minimum:
            break
        new_skills = [
            row.skill_name
            for row in best.rows
            if row.skill_id not in covered and row.level is not None
        ]
        chosen.append(best)
        covered.update(row.skill_id for row in best.rows if row.level is not None)
        rationale.append(
            f"{best.display_name} adds {gain:.1f} new coverage points"
            + (f" across {', '.join(new_skills)}." if new_skills else "; no new skills, added for overall strength.")
        )

    gaps = [
        f"{row.skill_name} (needs level {row.required_level})"
        for row in (chosen[0].rows if chosen else [])
        if row.skill_id not in covered
    ]

    violations = validate_team(
        minimum=minimum,
        maximum=maximum,
        members=chosen,
    )
    total = round(sum(member.ranking_score for member in chosen) / len(chosen), 1) if chosen else 0.0
    if not chosen:
        rationale.append("No candidate meets the hard constraints for this challenge.")
    return TeamSuggestion(
        members=chosen,
        score=total,
        covered_skill_ids=sorted(covered),
        gaps=gaps,
        rationale=rationale,
        violations=violations,
        candidates_considered=len(ranked),
    )


def validate_team(
    *,
    minimum: int,
    maximum: int,
    members: list[MatchResult],
) -> list[str]:
    """Hard-constraint check for a team. Empty list means it may be confirmed."""
    violations: list[str] = []
    if len(members) < minimum:
        violations.append(
            f"The challenge needs at least {minimum} members; this team has {len(members)}."
        )
    if len(members) > maximum:
        violations.append(
            f"The challenge allows at most {maximum} members; this team has {len(members)}."
        )
    ineligible = [m.display_name for m in members if m.eligibility == NOT_ELIGIBLE]
    if ineligible:
        violations.append(
            "Not eligible: " + ", ".join(ineligible) + ". A hard constraint is not met."
        )
    ids = [m.student_id for m in members]
    if len(ids) != len(set(ids)):
        violations.append("The same student is listed more than once.")
    return violations


def best_alternatives(
    ranked: list[MatchResult], chosen: list[MatchResult], *, limit: int = 3
) -> list[MatchResult]:
    """The next-best candidates who are not already on the team."""
    chosen_ids = {member.student_id for member in chosen}
    return [
        result
        for result in ranked
        if result.student_id not in chosen_ids and result.eligibility != NOT_ELIGIBLE
    ][:limit]
