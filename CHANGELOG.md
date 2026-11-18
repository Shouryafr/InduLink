# Changelog

## 0.4.1 — sign-in screen layout (31 Oct 2026)

### Fixed

- The demo credential box overflowed its card: the two-line role, three-column
  table could not fit a 34-character e-mail address beside the password. The
  panel is now a list of role, name and address, with the shared password stated
  once above it.
- The sign-in page scrolled by the height of the synthetic-data ribbon on a
  1600 × 905 viewport; the split now accounts for the ribbon, and the card is
  compacted so the demo panel sits fully on screen.
- The admin demo page no longer shows the Environment card (environment name,
  demo flag and database path); the controls and recent resets remain. A
  duplicated "the" in the reset explanation was corrected.

### Changed

- The left half of the sign-in page was a mostly empty column with three gaps.
  It now holds one composition — eyebrow, larger headline (40–52px instead of a
  fixed 40px), the compatibility quote and three lines on what the product does —
  centred between the wordmark and the synthetic-data footnote.

## 0.4.0 — display materials pass (30 Oct 2026)

The master blueprint's live-demonstration scenario now exists in the seeded data,
and the materials for showing it are written.

### Added

- **The section 16 challenge**: *Secure Financial Transaction Monitoring System*,
  public, open, owned by the demo partner, with Python 4/30%, Database Management
  3/25%, Networking 3/20% and Cybersecurity 4/25%, all mandatory.
- **The section 18 display cases** as fixed profiles: Anjali Mehra (full match),
  Dev Rathore (high soft score, one mandatory below level), Sakshi Chopra
  (declared only, no evidence) and Neha Khanna (private profile).
- **A mentor who covers all four requirements** (Dr Ritika Sethi), so the mentor
  step has a genuine fit, and a separate challenge with no complete fit.
- **`tests/integration/test_display_cases.py`**: 16 tests over the canonical
  challenge, the four cases, the mentor fit, the no-fit challenge and the demo
  sign-in accounts.
- **`DISPLAY_GUIDE.md`**: the scenario and its cases, a one-page architecture,
  release status labelled Implemented / In development / Future scope, test
  evidence, the section 17 run of show mapped to routes, and the section 19
  questions with answers.

### Changed

- The demo dataset is 17 projects and 13 mentors; counts across the docs and the
  run doc were corrected to match.
- Measured numbers refreshed: seed 0.58 s, page p95 31.1 ms, matching 2,000
  students 0.22 s, demo reset 1.43 s.

### Fixed

- The sign-in page's demo table was hardcoded and listed four accounts that do not
  exist; it now renders from `DEMO_LOGINS`, and a test signs in with each one.
- Two display candidates could be drawn with a non-public visibility, silently
  removing them from the partner's shortlist; their visibility is now pinned.
- The Why panel printed raw requirement points (`6.6667`); it now prints one
  decimal, so the failed-mandatory case reads as `6.7`.

## 0.3.0 — prototype completion pass (30 Oct 2026)

The prototype now runs every feature in the brief on a seeded dataset, with the
gates the brief asks for.

### Added

- **Matching engine** with hard constraints checked before soft scores, per
  requirement contributions, and a Why panel on the candidates screen.
- **Team suggestion and confirmation**, including the refusal text for a team that
  breaks a hard constraint.
- **Mentor request, fit list and confirmation**, with exactly one confirmed mentor
  per project.
- **What-If simulation** on a read-only path, with a domain checksum that refuses
  a result if stored data moved.
- **Skill passport**: create from a confirmed team place, publish/unpublish, JSON
  export with an attachment header.
- **Feedback, evaluations and appeals**, including a confidentiality flag.
- **Notification ledger and inbox.**
- **Admin screens** for appeals, passports and demo controls.
- **Demo dataset**: 60 students, 13 mentors, 6 faculty reviewers, 4 industry
  contacts, 2 administrators, 17 projects, 13 teams, 588 evidence items, 20
  passports and 30 engagement records, all with North Indian names from the
  brief's pools and all flagged synthetic.
- **Scenarios A–G** as end-to-end tests (`make scenarios`), each driving real
  routes and asserting on the database.
- **Concurrency tests**: contested mentor slot (100 rounds) and double team
  confirmation (20 rounds).
- **Copy audit and ribbon tests**: banned claims fail the suite; the synthetic
  ribbon must appear on every demo page and nowhere else.
- **`tools/measure.py`**: measures the section 9 targets and prints them.
- **Documentation**: `FEATURES.md`, `DEFECTS.md`, `DECISIONS.md`,
  `DEMO_SCRIPT.md`, this changelog, and updated `README.md` / `RISKS.md`.

### Fixed

- Seed crashed on a missing `RecommendationSnapshot` import.
- Two simultaneous team confirmations could create two confirmed teams; the
  database now refuses the loser (migration `4d1b8e6c2a77`).
- The synthetic-data ribbon never rendered, because an imported Jinja macro cannot
  read the rendering context.
- Duplicate full names between titled personas and the student roster.
- A Sikh first name paired with a non-Kaur surname in the seeded personas.
- The "declared skills, no evidence" edge case was missing from the seed.
- Two pieces of copy read as absolute guarantees.
- README still listed phase-1 demo accounts that no longer exist.
- The accessibility audit ignored implicitly labelled controls.

### Known gaps

`services/` coverage is 69% against an 85% target; PostgreSQL, axe and Playwright
runs are not verified here. See [DEFECTS.md](DEFECTS.md#open-gaps) and
[RISKS.md](RISKS.md).

## 0.2.0 — feature build

Student, mentor, industry and administrator workspaces; evidence submission and
review; challenge authoring with versioned requirements; profile visibility;
audit log; admin controls; accessibility and failure-path tests.

## 0.1.0 — foundation

Application factory, settings validation, database schema and migrations,
append-only audit with triggers, CSRF, session handling, design system, public
site, registration and sign-in.
