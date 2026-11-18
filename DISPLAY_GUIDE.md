# Display guide

Materials for showing InduLink live, written to the *Master Product Blueprint and
Display Guide* sections 16–19. One rule governs everything here: a capability is
called **Implemented** only when the code exists **and** an accepted test covers
it. Anything weaker is labelled as such, and nothing is mocked up for a screen.

Companion documents: [FEATURES.md](FEATURES.md) (per-feature route and proving
test), [DEMO_SCRIPT.md](DEMO_SCRIPT.md) (the longer tour),
[DEFECTS.md](DEFECTS.md) and [RISKS.md](RISKS.md) (what is not proven).

---

## 1. The demonstration scenario (section 16)

**Secure Financial Transaction Monitoring System**, posted by the fictional
company Northwind Grid. All records are synthetic; the ribbon on every page says
so.

| Requirement | Required level | Weight | Mandatory |
| --- | --- | --- | --- |
| Python | 4 / 5 | 30% | Yes |
| Database Management | 3 / 5 | 25% | Yes |
| Networking | 3 / 5 | 20% | Yes |
| Cybersecurity | 4 / 5 | 25% | Yes |

These numbers are stored exactly as written and normalised on write, so the
weights total 100%. The challenge is public, open, and owned by the demo partner
account, so one sign-in drives the whole story. Its description, eligibility
note, evaluation criteria and deliverables are filled in on the detail page.

### Demo accounts

Password for all: `DemoPass!2026`. The sign-in page lists these while
`DEMO_MODE=true`.

| Role | Account | Person |
| --- | --- | --- |
| Industry partner | `kabir.malhotra@indulink.example.test` | Kabir Malhotra, Northwind Grid |
| Student | `aarohi.sharma@indulink.example.test` | Aarohi Sharma |
| Mentor | `harpreet.singh@indulink.example.test` | Dr Harpreet Singh |
| Faculty reviewer | `neha.tandon@indulink.example.test` | Prof. Neha Tandon |
| Administrator | `meera.joshi@indulink.example.test` | Meera Joshi |

### The display cases (section 18)

Open **Industry → My challenges → *Secure Financial Transaction Monitoring
System*** → **Candidates**. Each case is a named seeded person, so it looks the
same on every run.

| Case | Who | What the screen must show |
| --- | --- | --- |
| Successful match | **Anjali Mehra** | *Eligible*, score 100, every requirement *Met* |
| Mandatory failure | **Dev Rathore** | *Not eligible*, Networking *Below the required level*, soft score 86.7 — a high score does not rescue a failed mandatory |
| Incomplete evidence | **Sakshi Chopra** | *Provisionally eligible*; all four rows say *Evidence needed*; confirmed 0, provisional 100 |
| Private profile | **Neha Khanna** | Absent from the partner shortlist; the administrator still sees her |

Two more cases cover the mentor step, on the same challenge and on
*Security operations triage workbook* (project 3, also owned by the demo partner):

| Case | What to show |
| --- | --- |
| Complete mentor fit | **Dr Ritika Sethi** covers all four requirements: 100, no gaps, available |
| No complete fit | The triage workbook lists mentors with their gaps; the best available covers three of four and still misses *Linux administration*, and the page states that rather than inventing a match |

All of the above is asserted in
[tests/integration/test_display_cases.py](tests/integration/test_display_cases.py)
through the real services and routes, so a change to the matcher cannot leave the
demo quietly wrong.

---

## 2. Architecture (one page)

```
browser
  │  HTML form / link (no client-side framework, no fetch API)
  ▼
FastAPI app (app/main.py)
  │  middleware: per-request session, identity snapshot, CSRF double-submit,
  │              security headers, no-store
  ├─ deps.py         current user, role guards, object-level checks
  ├─ routers/        public, student, mentor, industry, admin, notifications
  │     │
  │     ▼
  │  services/       one transaction per action; permission checks; audit writes;
  │                  domain rules (matching, teams, mentors, passports, what-if)
  │     │
  │     ▼
  │  core/           pure policy objects, no database access, property-tested
  │                  (security, skills, requirements, evidence policy, matching)
  ▼
SQLite in WAL mode (PostgreSQL-ready schema: both partial indexes carry
postgresql_where and services contain no SQLite-only SQL)
```

**Request path.** One middleware opens a session and resolves the signed-in
account once, keeping a plain snapshot so even the 500 page can render the chrome
after the transaction rolls back. Every state-changing request must carry a
signed CSRF token. Routers check roles, then services re-check ownership, so a
hidden button is never the control.

**Why the layers are split.** `core/` contains the rules a reviewer must be able
to audit — the weighted score, the mandatory check, the declared/confirmed
evidence policy, the state machine. It takes dataclasses in and returns
dataclasses out, which is why it can be tested exhaustively without a database.
`services/` owns transactions, notifications and audit entries. `app/` owns
transport and presentation.

**Matching, end to end.**

1. The current requirement version is loaded (an edit always creates a new
   version; older snapshots keep their stamp).
2. Candidates are loaded for the viewer's visibility scope in three queries: the
   profile, declared skills, evidence. Private profiles never reach a partner.
3. Each requirement is judged on **reviewed or verified evidence** at or above the
   required level. A declared-only level is *provisional*, and missing data is
   *No data* — never zero ability.
4. Soft score = `100 · Σ weightᵢ · min(levelᵢ / requiredᵢ, 1)` over confirmed
   levels. Failed mandatory requirements set `not_eligible`, and ineligible
   candidates rank below every eligible one regardless of score.
5. Ties break deterministically on student id, and the screen states that it did.
6. A stored ranking is a snapshot: later requirement edits do not rewrite it.

**Integrity.** Partial unique indexes enforce one current version, one confirmed
mentor and one confirmed team per project — the last two hold under concurrent
writes. Triggers refuse `UPDATE`/`DELETE` on the audit table and hard deletion of
users. Every action is one transaction; a failure leaves no partial state.

**Operations.** One process, one file, no external service: `make demo` migrates,
seeds and serves. The admin demo reset rebuilds the dataset in about 1.4 seconds
and records itself in the audit log.

---

## 3. Release status, labelled

### Implemented — with behaviour-specific tests

| Capability | Where | Proven by |
| --- | --- | --- |
| Registration, sign-in, sign-out, lockout, password help screen | `/register`, `/sign-in`, `/sign-out`, `/forgot-password` | `tests/integration/test_auth_flow.py` |
| CSRF and security headers on every request | all POST routes, middleware | `test_auth_flow.py`, `tests/integration/test_integrity.py` |
| Server-side role and ownership authorization | every protected route | `tests/authz/test_permissions.py` (16 routes × 4 roles) |
| Student profile, visibility, availability freshness | `/student/profile`, `/students/{id}` | `test_permissions.py`, `tests/integration/test_accessibility.py` |
| Evidence submission across six categories and mentor review | `/student/evidence`, `/mentor/evidence` | `tests/scenarios/test_scenarios.py` B |
| Challenge authoring with weighted, versioned requirements and approval | `/industry/projects/new`, `/admin/projects` | scenarios C, `test_integrity.py` |
| Explainable matching with mandatory checks before soft scores | `/industry/projects/{id}/candidates` | `tests/unit/test_matching.py` (property tests), `test_display_cases.py` |
| Team suggestion, hard-constraint refusal, one confirmed team | `/industry/projects/{id}/team` | scenario E, `tests/integration/test_concurrency.py` |
| Mentor fit, request and decision, one confirmed mentor | `/industry/projects/{id}/mentor`, `/mentor/requests` | scenario D, `test_concurrency.py`, `test_display_cases.py` |
| What-If simulation that provably changes nothing | `/industry/projects/{id}/what-if` | scenario F (checksum) |
| Passport create, publish, export | `/student/passport` | scenario G |
| Append-only audit log and admin oversight | `/admin/audit` | `test_integrity.py` |
| Honest-claims copy and the synthetic ribbon | every page | `tests/integration/test_copy_and_ribbon.py` |
| Deterministic synthetic dataset with named edge cases | `data/seed_synthetic.py` | `tests/integration/test_demo_dataset.py` |

### Implemented — route-level proof only

The screen renders, the role guard is tested and the seeded data exercises the
feature, but no test would fail if the behaviour regressed. Say "implemented,
test coverage partial" if asked.

| Capability | Route |
| --- | --- |
| Skill registry aliases and student proposals | `/admin/skills`, `/mentor/registry`, `/student/profile/skills` |
| Mentor expertise registry and capacity edits | `/mentor/registry` |
| Evaluations and directed feedback | `/industry/projects/{id}/review`, `/student/feedback` |
| Appeals | `/student/appeals`, `/admin/appeals/{id}` |
| Notification inbox behaviour | `/notifications` |
| Demo reset behaviour | `/admin/demo` |
| Search results behaviour | `/search` |

### In development

Nothing. No screen in this build is a mock or a placeholder: either the workflow
works against the database or the route does not exist.

### Future scope — designed for, deliberately not built

| Item | Why it is not here |
| --- | --- |
| Automatic brief decomposition into requirements (LLM or rules) | The wizard takes skills, levels, weights and mandatory flags directly. Proposing them from free text is unbuilt; claiming it would be the one dishonest slide. |
| Real e-mail delivery, reset tokens, address verification | No mail provider is connected; password help is a screen. Self-service reset needs a token table. |
| File uploads and malware scanning | Evidence is described, not attached, so no upload path exists to secure. |
| Machine-learned ranking | The blueprint forbids the claim unless it is implemented and evaluated. Scoring is explainable Python. |
| PostgreSQL deployment, containerisation, CI | The schema is PostgreSQL-ready and the suite runs on SQLite; neither a PostgreSQL run nor a pipeline exists. |
| axe and Playwright journeys | Accessibility is checked structurally plus a keyboard pass; journeys are covered by scenarios through the ASGI client. |
| MFA, per-IP throttling, audit retention and export | Listed in [RISKS.md](RISKS.md) as deferred. |

---

## 4. Test evidence

Run `make test` (suite plus the `core/` coverage gate) and `make scenarios`.

| Measure | Result | Target |
| --- | --- | --- |
| Tests passing | **445** | — |
| `core/` line coverage | **97%** | 90% gate — met |
| `services/` line coverage | **69%** | 85% — **not met** |
| `app/` line coverage | **71%** | no target set |
| Scenario journeys A–G | **7 passed** | all pass |
| Contested mentor slot | **100 of 100 rounds, exactly one winner** | 100 of 100 |
| Double team confirmation | **20 of 20 rounds, exactly one team** | 20 of 20 |
| Seed run | **0.58 s** | under 60 s |
| Page response, server side | **31.1 ms p95**, median 7 ms (190 requests) | under 500 ms p95 |
| Matching, 500 students | **0.02 s** | under 3 s |
| Matching, 2,000 students | **0.22 s** | under 10 s |
| Demo reset | **1.43 s** | under 10 s |

What the suites cover:

- `tests/unit` — password hashing, e-mail rules, CSRF signing, lockout, skill
  canonicalisation, weight validation, the state machine, the evidence policy,
  freshness, configuration safety, and property tests that a failed mandatory
  requirement never outranks a high soft score.
- `tests/integration` — pragmas, CHECK/UNIQUE/FK enforcement, append-only audit,
  refused hard deletes, rollback, versioning, auth flow, headers, demo dataset
  (minimums, names, edge cases, idempotency, production refusal), failure paths,
  accessibility, copy audit, ribbon, concurrency, and the display cases.
- `tests/authz` — 16 protected routes × 4 roles, anonymous redirects,
  object-level project and profile access, denial auditing.
- `tests/scenarios` — A register and sign in · B submit and review evidence ·
  C create and approve a challenge · D mentor confirms one contested request ·
  E confirm a team · F What-If changes nothing · G passport create, publish,
  export.

Numbers are measured by `tools/measure.py` on this machine against a freshly
seeded SQLite database; the test-client transport means network time is excluded.
Coverage, not assertion counts, is where the honest gap is: `services/` at 69%
against an 85% target.

---

## 5. Run of show (section 17), mapped to screens

Six to eight minutes, all on the one challenge. Sign in as the partner first. The
challenge id is 17 in a fresh seed; open it by title from *My challenges* if it
differs.

| # | Step | Where | What to point at |
| --- | --- | --- | --- |
| 1 | Company challenge | `/industry/projects/17` | The four weighted, all-mandatory requirements and the deliverables |
| 2 | Decomposition and approval | `/industry/projects/17` (versions), `/admin/projects` | Requirements entered with levels and weights, normalised to 100%; the current version and its history. Automatic brief parsing is **future scope** — say so |
| 3 | Student evidence | `/student` as Aarohi Sharma | Declared, assessed, provisional and evidence count as separate columns; *No data* is not zero |
| 4 | Matching | `/industry/projects/17/candidates` | The three display cases: Anjali, Dev, Sakshi — open each **Why** panel |
| 5 | Team formation | `/industry/projects/17/team` | Suggested members with reasons, uncovered requirements, single-person dependencies |
| 6 | Mentor selection | `/industry/projects/17/mentor` | Dr Ritika Sethi at 100 with no gaps, then `/industry/projects/3/mentor` for a challenge no mentor fully covers |
| 7 | Dashboard | `/industry` | Counts by state and the candidate totals |
| 8 | Lifecycle | `/industry/projects/17` | Allowed next states; a refused transition explains itself instead of crashing |
| 9 | Requirement change | `/industry/projects/17` → edit requirements | A new version, and the earlier snapshot keeps its stamp |
| 10 | Incomplete data | `/student` as Sakshi Chopra, or Diya Arora for missing availability | "Evidence needed" and "no availability" are stated, not guessed |
| 11 | Dispute | `/student/appeals` as a student, `/admin/appeals` | The case status and history, resolved by an administrator |
| 12 | Outcome | `/industry/projects/17/review`, `/student/passport` | Evaluation and directed feedback, then the passport entry and export |

If time is short, show 1–7 live and present 8–12 as prepared screens — but only
call them functional because they are: each is a real route over the same
database, covered by a test or listed as route-level proof above.

---

## 6. Questions a mentor is likely to ask (section 19)

**Is this AI?**
No. Ranking is transparent Python: a weighted, level-capped sum over confirmed
evidence, with hard constraints applied first. There is no model to inspect
because there is no model. ML is future scope and is never claimed on screen.

**Can students inflate their skills?**
A declared level is stored separately from reviewed and verified evidence and can
never satisfy a mandatory requirement on its own. A certificate confirms a
learning claim, not a practical level, and an expired certificate contributes
nothing. Sakshi Chopra exists to show the difference.

**What if nobody matches?**
The screen says there is no current fit, names the gaps, and shows what would
close them. It never fills the space with a fabricated match; the triage
challenge demonstrates the same honesty for mentors.

**How is mentor overload prevented?**
Each mentor has an active-project limit and a weekly hours cap. The fit list shows
capacity, current utilisation and recorded unavailability, and a confirmed
assignment is enforced by a partial unique index, so two projects cannot claim the
last slot under a race — the concurrency test runs that 100 times.

**How are the recommendations fair?**
The criteria are documented and visible: per-requirement contribution, the
mandatory check, the evidence behind each level, and the tie-break rule. People
approve requirements, confirm teams and mentors, review evidence, and resolve
appeals; the audit log records who did what. There is no hidden input, and names
are not read by scoring — a test renames everyone and asserts the ranking is
unchanged.

**What if requirements change?**
An edit writes a new version and never mutates the old one. Existing ranking
snapshots keep the stamp of the version they were made under, so a confirmed team
is not silently re-scored. What-If lets a partner test a change against live data
without writing anything, and refuses the result if the data moved underneath it.

**How is data protected?**
Server-side authorization on every route plus object-level ownership checks;
bcrypt with a production cost floor; opaque session tokens with only a hash
stored; signed CSRF tokens; `no-store` responses and a CSP without inline
scripts; append-only audit history that the database refuses to rewrite; private
profiles excluded from partner queries at the query, not the template.

**What is not finished?**
`services/` coverage is 69% against an 85% target, seven features have route-level
proof rather than behaviour tests, there is no PostgreSQL run, and no browser or
axe automation. All of it is in [RISKS.md](RISKS.md) and
[DEFECTS.md](DEFECTS.md#open-gaps).

---

## 7. What not to claim

Not prediction, not a guarantee of placement or success, not employability, not
compliance or certification, and not effectiveness on real data. The dataset is
synthetic; the scores describe fit against stated requirements, nothing more. The
copy audit fails the build if those claims appear without a qualifier.
