# Decisions

Where the brief was silent, or two readings were possible, the choice is recorded
here with the reason. Scoring, eligibility and evidence-policy changes are listed
first because those are the ones a reviewer must be able to audit.

## Scoring, eligibility and evidence policy

| ID | Decision | Why |
| --- | --- | --- |
| D-001 | A mandatory requirement is satisfied only by **reviewed or verified** evidence at or above the required level. Declared and unreviewed levels make a candidate *provisionally* eligible, never eligible. | Hard constraints must dominate soft scores; a self-declared level is not a constraint check. |
| D-002 | A failed mandatory requirement forces `not_eligible` even when every soft score is at maximum, and ineligible candidates are ranked below all eligible ones. | Property tests in `tests/unit/test_matching.py` assert this for arbitrary inputs. |
| D-003 | A certificate confirms a learning claim, not a practical level, and never alone satisfies a practical requirement. Expired certificates are labelled and contribute nothing. | Matches the original brief's evidence hierarchy. |
| D-004 | Soft score is the weight-normalised, level-capped contribution of confirmed evidence; `None` (no data) contributes 0 but is displayed as "No data", never as zero ability. | Prevents "no evidence" from reading as "proven weak". |
| D-005 | A requirement edit always writes a new version; snapshots made under the previous version are kept and shown with their stamp. | The demo needs to show what an edit did to a shortlist. |
| D-006 | What-If runs entirely in memory against the same candidate pool. It stores only the scenario record and an audit entry, and refuses the result if the domain checksum changed. | Read-only path; Scenario F proves it by checksum. |

## Data integrity

| ID | Decision | Why |
| --- | --- | --- |
| D-010 | The database, not the service, enforces "one confirmed team per project" and "one confirmed mentor per project" through partial unique indexes. | A check-then-write race produced two teams in testing; only a constraint can promise one winner. |
| D-011 | Deleting domain rows is refused by triggers; records are cancelled or dissolved instead. | Auditability. Tests assert raw SQL cannot rewrite or delete history. |
| D-012 | Seed refuses unless `DEMO_MODE=true` and `APP_ENV` is local/ci/demo/staging/development/test. | Synthetic people must be impossible in production. |
| D-013 | Seeded names are drawn from the brief's pools with a fixed RNG seed; Sikh first names take Kaur/Singh; titles are stripped before reserving a name so two people cannot share a full name. | Deterministic demo, auditable names. |
| D-014 | Emails use `indulink.example.test`, a reserved domain, so no message can reach a real address. | Safety of the demo. |

## Interface

| ID | Decision | Why |
| --- | --- | --- |
| D-020 | Demo behaviour is one flag (`DEMO_MODE`): ribbon, login picker and reset control all read it. With the flag off, the ribbon and picker are absent. | One control, one test. |
| D-021 | Refusals redirect with a short `?error=` code that the target screen renders as a banner, instead of re-rendering the form with a 422. | Prevents form replay on refresh; the reason is still on screen. |
| D-022 | The synthetic ribbon is rendered by the base template for every page, driven by the setting, not per-screen includes. | A screen cannot forget it; a test checks public and signed-in pages. |
| D-023 | Copy never claims prediction, guarantee, compliance or certification. Disclaimers ("decision support, not a guarantee") are allowed; the copy audit understands negation. | Honest-claims rule, enforced by test. |
| D-024 | Accessibility audit accepts implicit labels (`<input>` inside `<label>`) as well as `label[for]`. | Both are valid HTML; the audit was wrong, not the markup. |
| D-025 | "No data" is a first-class state in every skill table, not an empty value. | The brief's six-field rule. |

## Display materials

| ID | Decision | Why |
| --- | --- | --- |
| D-040 | The blueprint's section 16 challenge is seeded exactly as written: Python 4/30%, Database Management 3/25%, Networking 3/20%, Cybersecurity 4/25%, all mandatory. The raw 30/25/20/25 values are stored as entered and normalised to sum to 1. | A demo that quietly "fixes" the guide's numbers cannot show that weights are validated and normalised. |
| D-041 | That challenge is owned by the documented demo partner (Kabir Malhotra), not by a fourth partner. | The whole run of show, including matching, teams and mentors, needs one sign-in. Ownership is checked server-side, so a different account cannot open those screens. |
| D-042 | "Database Management" and "Cybersecurity" are canonical skills distinct from SQL, Threat modelling and Secure coding. They are registered but kept out of the random student draw. | The blueprint names them as requirements; mapping them onto neighbouring skills would make the display case's levels meaningless, and adding them to the random draw would shift every deterministic profile. |
| D-043 | The section 18 cases are fixed profiles (Anjali Mehra, Dev Rathore, Sakshi Chopra, Neha Khanna) excluded from the per-project coverage pass that gifts mandatory skills to other students. | The coverage pass exists so screens are not empty; applied to the display candidates it would erase the failed-mandatory and no-evidence cases the demo depends on. |
| D-044 | The "no complete mentor fit" case uses the *Clinic appointment triage companion* challenge rather than weakening the canonical one. | The canonical challenge needs a genuine fit for the mentor step; a separate challenge shows the honest no-fit state without sacrificing it. |
| D-045 | There is no automatic brief-to-requirements decomposer. The wizard takes skills, levels, weights and mandatory flags directly, and admin approval gates the version. | The blueprint's decomposer step is about approval and structure; a rules-based text parser would be guesswork dressed as analysis, so it is labelled Future Scope in [DISPLAY_GUIDE.md](DISPLAY_GUIDE.md) instead. |
| D-046 | The sign-in page renders its demo table from `DEMO_LOGINS` rather than hardcoded addresses. | The hardcoded table outlived the accounts it named; a test now signs in with every listed persona. |

## Engineering

| ID | Decision | Why |
| --- | --- | --- |
| D-030 | SQLite for local and demo, with `postgresql_where` on both partial indexes and no SQLite-specific SQL in services. | Demo must be one command; production can move without rework. |
| D-031 | Scenarios live in `tests/scenarios/` and run with `make scenarios`; they drive real routes and then assert on the database. | A rendering-only test does not prove a feature works. |
| D-032 | Performance is measured by `tools/measure.py` and the numbers are copied into the README, not estimated. | The brief asks for measured, not assumed. |
| D-033 | The seed is idempotent by checking for existing rows rather than by deleting and rebuilding. | Re-running must not destroy a walkthrough in progress. |
