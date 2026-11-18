"""The single evidence policy (brief section 6).

Every module that needs to know "how good is this student at this skill, and
how sure are we?" calls this module. There is exactly one implementation so
the rule cannot drift between the matching engine, the radar, the readiness
estimator and the UI.

The four-row rule this module encodes:

===================================  ================  ===========  ======================
Student has for a skill              Effective level   Label        Counts toward
===================================  ================  ===========  ======================
Reviewed or verified evidence        assessed level    Reviewed /   confirmed score and
                                     from evidence     Verified     mandatory checks
Only declared level, or certificate  declared level    Provisional  provisional score only;
or unreviewed evidence                                             mandatory checks return
                                                                   "provisional - evidence
                                                                   needed"
Nothing                              no value          Unknown      neither score; shown as
                                                                   "no data", never 0
Rejected evidence only               no value          Rejected     nothing
===================================  ================  ===========  ======================

Unknown proficiency is never treated as zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

EVIDENCE_CATEGORIES: tuple[str, ...] = (
    "self_declared",
    "certificate",
    "practical_task",
    "project_based",
    "mentor_reviewed",
    "independently_verified",
)

EVIDENCE_STATUSES: tuple[str, ...] = (
    "submitted",
    "under_review",
    "reviewed",
    "rejected",
    "verified",
)

CATEGORY_LABELS: dict[str, str] = {
    "self_declared": "Self-declared",
    "certificate": "Certificate-backed",
    "practical_task": "Practical task",
    "project_based": "Project-based",
    "mentor_reviewed": "Mentor-reviewed",
    "independently_verified": "Independently verified",
}

STATUS_LABELS: dict[str, str] = {
    "submitted": "Submitted",
    "under_review": "Under review",
    "reviewed": "Reviewed",
    "rejected": "Rejected",
    "verified": "Verified",
}

#: Statuses whose assessed level counts as confirmed data.
CONFIRMING_STATUSES: frozenset[str] = frozenset({"reviewed", "verified"})

#: A certificate supports a learning claim only. It never confirms a practical level.
NON_PRACTICAL_CATEGORIES: frozenset[str] = frozenset({"certificate"})

LABEL_VERIFIED = "verified"
LABEL_REVIEWED = "reviewed"
LABEL_PROVISIONAL = "provisional"
LABEL_UNKNOWN = "unknown"
LABEL_REJECTED = "rejected"

LABEL_TEXT: dict[str, str] = {
    LABEL_VERIFIED: "Verified",
    LABEL_REVIEWED: "Reviewed",
    LABEL_PROVISIONAL: "Provisional",
    LABEL_UNKNOWN: "Unknown",
    LABEL_REJECTED: "Rejected",
}

#: Tone names map onto the design system's status chips.
LABEL_TONE: dict[str, str] = {
    LABEL_VERIFIED: "ok",
    LABEL_REVIEWED: "ok",
    LABEL_PROVISIONAL: "warn",
    LABEL_UNKNOWN: "unknown",
    LABEL_REJECTED: "risk",
}

OUTCOME_MET = "met"
OUTCOME_PROVISIONAL = "provisional"
OUTCOME_FAILED = "failed"
OUTCOME_NO_DATA = "no_data"

ELIGIBLE = "eligible"
PROVISIONALLY_ELIGIBLE = "provisionally_eligible"
NOT_ELIGIBLE = "not_eligible"

ELIGIBILITY_TEXT: dict[str, str] = {
    ELIGIBLE: "Eligible",
    PROVISIONALLY_ELIGIBLE: "Provisionally eligible",
    NOT_ELIGIBLE: "Not eligible",
}

ELIGIBILITY_TONE: dict[str, str] = {
    ELIGIBLE: "ok",
    PROVISIONALLY_ELIGIBLE: "warn",
    NOT_ELIGIBLE: "risk",
}


@dataclass(frozen=True)
class EvidenceClaim:
    """One evidence item reduced to what the policy needs to know."""

    evidence_id: int
    category: str
    status: str
    demonstrated_level: int | None = None
    evidence_date: date | None = None
    expiry_date: date | None = None
    reviewer_id: int | None = None
    source: str = ""


@dataclass(frozen=True)
class SkillAssessment:
    """The two-number answer this policy produces for one skill."""

    declared_level: int | None
    confirmed_level: int | None
    provisional_level: int | None
    label: str
    confirmed_evidence_ids: tuple[int, ...] = ()
    provisional_evidence_ids: tuple[int, ...] = ()
    rejected_evidence_ids: tuple[int, ...] = ()
    expired_certification_ids: tuple[int, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_unknown(self) -> bool:
        return self.label == LABEL_UNKNOWN

    @property
    def has_confirmed(self) -> bool:
        return self.confirmed_level is not None

    @property
    def label_text(self) -> str:
        return LABEL_TEXT.get(self.label, self.label.title())

    @property
    def tone(self) -> str:
        return LABEL_TONE.get(self.label, "unknown")

    def effective_level(self) -> int | None:
        """Best defensible level: confirmed, else declared/provisional."""
        if self.confirmed_level is not None:
            return self.confirmed_level
        return self.provisional_level

    def to_context(self) -> dict:
        """Template-friendly view. Keeps the six fields separate."""
        return {
            "declared_level": self.declared_level,
            "assessed_level": self.confirmed_level,
            "effective_level": self.effective_level(),
            "provisional_level": self.provisional_level,
            "label": self.label,
            "label_text": self.label_text,
            "tone": self.tone,
            "evidence_ids": list(self.confirmed_evidence_ids),
            "notes": list(self.notes),
        }


def evaluate_skill(
    declared_level: int | None, claims: list[EvidenceClaim], *, today: date | None = None
) -> SkillAssessment:
    """Apply the evidence policy to one student-skill pair.

    Confirmed level is the highest assessed level among non-certificate evidence
    that a reviewer has signed off on. The highest (rather than the newest) is
    used because the claim being made is "this level has been demonstrated",
    and the individual evidence items are all listed beside the number so the
    choice is visible. Ties fall back to the most recent evidence date.
    """
    today = today or date.today()
    notes: list[str] = []

    confirmed_pool: list[tuple[int, date, int]] = []
    provisional_pool: list[tuple[int, date, int]] = []
    rejected_ids: list[int] = []
    expired_cert_ids: list[int] = []

    for claim in claims:
        if claim.status == "rejected":
            rejected_ids.append(claim.evidence_id)
            continue

        expired = (
            claim.expiry_date is not None
            and claim.expiry_date < today
            and claim.category == "certificate"
        )
        if expired:
            expired_cert_ids.append(claim.evidence_id)

        if claim.demonstrated_level is None:
            continue

        sort_date = claim.evidence_date or date.min

        # Provisional pool: anything not rejected can raise the provisional view,
        # including a certificate (which supports a learning claim).
        provisional_pool.append((claim.demonstrated_level, sort_date, claim.evidence_id))

        # Confirmed pool: strictly reviewed/verified and practical in nature.
        if claim.status in CONFIRMING_STATUSES and claim.category not in NON_PRACTICAL_CATEGORIES:
            if not expired:
                confirmed_pool.append((claim.demonstrated_level, sort_date, claim.evidence_id))

    confirmed_level = None
    confirmed_ids: list[int] = []
    if confirmed_pool:
        best = max(entry[0] for entry in confirmed_pool)
        winners = [entry for entry in confirmed_pool if entry[0] == best]
        winners.sort(key=lambda entry: entry[1], reverse=True)
        confirmed_level = best
        confirmed_ids = [entry[2] for entry in winners]

    provisional_level = declared_level
    provisional_ids: list[int] = []
    if provisional_pool:
        best_provisional = max(entry[0] for entry in provisional_pool)
        provisional_level = max(provisional_level or 0, best_provisional) or None
        provisional_ids = [
            entry[2] for entry in provisional_pool if entry[0] == best_provisional
        ]

    has_verified_claim = any(
        claim.status == "verified" and claim.evidence_id in confirmed_ids for claim in claims
    )

    if confirmed_level is not None:
        label = LABEL_VERIFIED if has_verified_claim else LABEL_REVIEWED
        if declared_level is not None and declared_level != confirmed_level:
            notes.append(
                f"Declared level {declared_level} differs from the reviewed level "
                f"{confirmed_level}; the reviewed level is used."
            )
    elif provisional_level is not None:
        label = LABEL_PROVISIONAL
        if any(claim.category == "certificate" and claim.status in CONFIRMING_STATUSES for claim in claims):
            notes.append(
                "A certificate supports the learning claim but does not confirm a "
                "practical level. Submit practical evidence to confirm it."
            )
        else:
            notes.append("No reviewed evidence yet, so this level is provisional.")
    elif rejected_ids:
        label = LABEL_REJECTED
        notes.append("The submitted evidence was rejected and is not counted.")
    else:
        label = LABEL_UNKNOWN
        notes.append("No declared or assessed level recorded. Shown as no data, not zero.")

    if expired_cert_ids:
        notes.append("An official certification on this skill has expired; it is labelled expired.")

    return SkillAssessment(
        declared_level=declared_level,
        confirmed_level=confirmed_level,
        provisional_level=provisional_level,
        label=label,
        confirmed_evidence_ids=tuple(confirmed_ids),
        provisional_evidence_ids=tuple(provisional_ids),
        rejected_evidence_ids=tuple(rejected_ids),
        expired_certification_ids=tuple(expired_cert_ids),
        notes=tuple(notes),
    )


def check_requirement(assessment: SkillAssessment, required_level: int) -> str:
    """Mandatory-check result for one requirement.

    Hard checks are pass/fail and independent of any soft score. Provisional
    data never satisfies a mandatory requirement outright: at best it makes the
    candidate provisionally eligible, which is stated in the UI.
    """
    if required_level < 1:
        raise ValueError("Required level must be 1-5.")

    if assessment.confirmed_level is not None:
        return OUTCOME_MET if assessment.confirmed_level >= required_level else OUTCOME_FAILED

    if assessment.provisional_level is not None:
        if assessment.provisional_level >= required_level:
            return OUTCOME_PROVISIONAL
        return OUTCOME_FAILED

    return OUTCOME_NO_DATA


def determine_eligibility(outcomes: list[str]) -> str:
    """Aggregate hard-check outcomes into one eligibility result."""
    if not outcomes:
        return ELIGIBLE
    if any(outcome == OUTCOME_FAILED for outcome in outcomes):
        return NOT_ELIGIBLE
    if any(outcome in (OUTCOME_PROVISIONAL, OUTCOME_NO_DATA) for outcome in outcomes):
        return PROVISIONALLY_ELIGIBLE
    return ELIGIBLE


def contribution(level: int | None, required_level: int, weight: float) -> float:
    """Points this skill contributes to a 0-100 score.

    ``level`` of ``None`` means unknown and contributes nothing; it is reported
    as "no data" rather than being scored as a failure.
    """
    if required_level <= 0:
        raise ValueError("Required level must be positive.")
    if level is None:
        return 0.0
    ratio = min(level / required_level, 1.0)
    return round(100.0 * weight * ratio, 4)


def score_from_components(components: list[float]) -> float:
    """Sum component points, clamped to 0-100 and rounded for display."""
    return round(min(max(sum(components), 0.0), 100.0), 1)


# --- Evidence age ---------------------------------------------------------

REVIEW_PROMPT_AFTER_DAYS = 365


def evidence_age_days(evidence_date: date, today: date | None = None) -> int:
    today = today or date.today()
    return max((today - evidence_date).days, 0)


def age_badge(days: int) -> tuple[str, str]:
    """Neutral age badge for an evidence item. Old evidence is not invalidated."""
    if days <= 180:
        return ("Recent", "ok")
    if days <= REVIEW_PROMPT_AFTER_DAYS:
        return ("Aging", "info")
    return ("Consider re-review", "warn")


def needs_revalidation(evidence_date: date, today: date | None = None) -> bool:
    return evidence_age_days(evidence_date, today) > REVIEW_PROMPT_AFTER_DAYS


def is_expired(expiry_date: date | None, today: date | None = None) -> bool:
    if expiry_date is None:
        return False
    return expiry_date < (today or date.today())
