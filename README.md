# InduLink

Explainable academia–industry matching: industry partners post structured challenges, students
build evidence-backed skill profiles, mentors review that evidence, and teams are matched with
reasons a person can audit. Decision support — **not** a guarantee of ability, success or
employability, and not a claim about real-world effectiveness. Every demo record is synthetic.

Built to the *InduLink Website Build Brief* and the *Prototype Completion Brief*.
The feature inventory is [FEATURES.md](FEATURES.md); what is proven and what is not is in
[DEFECTS.md](DEFECTS.md) and [RISKS.md](RISKS.md).

**Status: all 25 features are implemented and reachable; 445 tests pass; scenarios A–G run end to
end.** Seven features have route-level proof but no behaviour-specific test yet, and `services/`
coverage is 69% against an 85% target. Those gaps are stated, not hidden.

---

## Quick start

```bash
make install     # .venv + pinned requirements
make demo        # migrate, seed synthetic data, serve on http://127.0.0.1:8000
make test        # full suite + coverage + the core/ 90% gate
make scenarios   # scenarios A–G against the seeded dataset
```

`make` is not installed on every Windows shell. The same commands, unedited:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m data.seed_synthetic
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --reload

.venv/Scripts/python.exe -m pytest tests -q --cov=core --cov=app --cov=services
.venv/Scripts/python.exe -m pytest tests/scenarios -q
PYTHONPATH=. .venv/Scripts/python.exe tools/measure.py     # performance targets
```

Configuration lives in environment variables, documented in [.env.example](.env.example). The
only required secret is `SECRET_KEY` in production (≥ 32 chars); development generates an
ephemeral one, so sessions do not survive a restart. `APP_ENV=production` with `DEMO_MODE=true`
refuses to start, and the seed refuses outside a demo environment.

### Demo sign-in

Shown on the sign-in page only while `DEMO_MODE=true`. Password for every seeded account:
`DemoPass!2026`.

| Role | Account | Person |
| --- | --- | --- |
| Student | `aarohi.sharma@indulink.example.test` | Aarohi Sharma |
| Mentor | `harpreet.singh@indulink.example.test` | Dr Harpreet Singh |
| Faculty reviewer | `neha.tandon@indulink.example.test` | Prof. Neha Tandon |
| Industry partner | `kabir.malhotra@indulink.example.test` | Kabir Malhotra, Northwind Grid |
| Administrator | `meera.joshi@indulink.example.test` | Meera Joshi |

The walkthrough that uses each of them is [DEMO_SCRIPT.md](DEMO_SCRIPT.md). For a live
demonstration, the blueprint's canonical financial-monitoring challenge, with its four weighted
mandatory requirements and four named display cases, and a six-minute run of show are in
[DISPLAY_GUIDE.md](DISPLAY_GUIDE.md).

The seeded set is deterministic and idempotent: **60 students** across four departments and four
years, **13 mentors**, **6 faculty reviewers**, **4 industry contacts**, **2 administrators**,
**17 projects** (open, active, completed, cancelled, one with two requirement versions),
**13 teams** (including one that breaks a hard constraint), **588 evidence items** across all six
states, **20 passports**, and **30 feedback, evaluation and appeal records**. Re-running
`make seed` changes nothing.

Deliberate edge cases live in the seed: a student with no evidence, a student with no
availability, a mentor unavailable for every window, two students competing for one mentor slot,
an edited requirement set with its earlier snapshot, a score tie, a student in another timezone,
an expired certificate, a rejected upload, and a hard-constraint failure. Each is a named test in
`tests/integration/test_demo_dataset.py`.

## What is in the product

| Area | Behaviour |
| --- | --- |
| Accounts | Registration, sign-in, sign-out, session revocation, bcrypt (cost ≥ 12 in production), 5-attempt lockout, indistinguishable sign-in failures |
| Authorization | Server-side role guards on every protected route plus object-level checks (own project, own profile, own evidence) — hiding a button is never access control |
| Skill registry | Canonical names, aliases, categories, 1–5 definitions, proposals, admin curation |
| Student profiles | Visibility (`private` / `university` / `public`), availability with freshness stamps, six separate skill fields |
| Evidence | Submission across six categories, mentor review with required note and level, certificate expiry, revalidation |
| Challenges | Authoring with weighted requirements normalised on write, immutable versions, state machine, admin approval |
| Matching | Deterministic ranking, hard constraints before soft scores, Why panel per candidate |
| Teams | Greedy suggestion with reasons, hard-constraint refusal, one confirmed team per project enforced by index |
| Mentors | Fit list, request and decision, capacity and unavailability, one confirmed mentor per project |
| What-If | Read-only simulation with a checksum that refuses a result if domain data moved |
| Outcomes | Passport create/publish/export, evaluations, directed feedback, appeals, notifications |
| Assurance | Append-only audit log, admin oversight screens, demo reset |

Screens are grouped by role: `/student/*`, `/mentor/*`, `/industry/*`, `/admin/*`, with
`/students/{id}` as the permission-aware profile.

## Verification

`make test` — **445 tests pass**; `core/` line coverage **97%** (gate: 90%), `services/` **69%**
(target: 85%, **not met**). `make scenarios` — **7 passed**.

| Suite | What it proves |
| --- | --- |
| `tests/unit` | Passwords, e-mail, CSRF signing, lockout, canonicalisation, requirement weights, state machine, evidence policy, freshness, configuration safety, and property tests that a hard-constraint failure never outranks a high soft score |
| `tests/integration` | Pragmas, CHECK/UNIQUE/FK enforcement, append-only audit, no hard deletes, transaction rollback, versioning, auth flow, CSRF, headers, demo dataset (minimums, names, edge cases, idempotency, production refusal), failure paths, accessibility, copy audit, ribbon, concurrency |
| `tests/authz` | 16 protected routes × 4 roles, anonymous redirects, object-level project and profile access, denial auditing |
| `tests/scenarios` | Scenarios A–G end to end through the real routes, asserting on the database |

Scenarios: A register and sign in · B submit and review evidence · C create and approve a
challenge · D mentor confirms one contested request · E confirm a team · F What-If changes
nothing (checksum) · G passport create, publish, export.

Accessibility is checked structurally (language, one `h1`, no heading jumps, labelled controls
including implicit labels, captioned tables, skip link, focus ring, reduced motion, status never
colour alone). That is a floor, not a WCAG audit — see [RISKS.md](RISKS.md).

### Measured performance

Measured by `tools/measure.py` on this machine against a freshly seeded SQLite database; the
test-client transport means network time is excluded.

| Measure | Measured | Target |
| --- | --- | --- |
| Seed run (migrations excluded) | **0.67 s** | under 60 s |
| Page response, server side | **32.4 ms p95** (190 requests, median 7 ms) | under 500 ms p95 |
| Matching, one project, 500 students | **0.02 s** | under 3 s |
| Matching, one project, 2,000 students | **0.25 s** | under 10 s |
| Demo reset | **1.52 s** | under 10 s |
| Concurrency, contested slot | **100 of 100 rounds, exactly one winner** | 100 of 100 |

## Layout

```
app/
  main.py            app factory, middleware wiring, error handlers
  middleware.py      per-request DB session + identity, CSRF, security headers
  deps.py            get_db, current user, role guards
  rendering.py       Jinja environment, shared context, role-aware navigation
  routers/           public, student, mentor, industry, admin, notifications
  templates/         base, layouts, components/macros, per-role screens
  static/            css (tokens, app, landing, fonts), js, self-hosted fonts
core/                pure policy, no database access
  security_policy.py passwords, e-mail, CSRF signing, lockout
  skills.py          canonicalisation, alias resolution, level definitions
  requirements.py    requirement weights, project state machine
  evidence_policy.py declared vs confirmed levels, eligibility, certificates
  matching.py        ranking, hard constraints, team validation
  freshness.py       staleness stamps and badges
data/                schema, database setup, deterministic synthetic seed, personas
services/            transactions, domain rules, audit writes, permission checks
migrations/          Alembic revisions, including append-only triggers and partial indexes
tests/               unit, integration, authz, scenarios
tools/measure.py     the section 9 measurements
```

## Data integrity

- WAL journal, `foreign_keys=ON` and a busy timeout on every connection — verified by test.
- CHECK constraints at the boundary: proficiency 1–5, requirement weight 0–1,
  `team_size_min ≤ team_size_max`, one declared level per student/skill, unique canonical names.
- `audit_events` refuses `UPDATE` and `DELETE`; `users` refuse hard deletion.
- Partial unique indexes allow exactly one current project version, one confirmed mentor and one
  confirmed team per project — the last two hold under concurrent writes (tested).
- Every mutation runs in one transaction: a failure mid-update leaves no partial state.

## Security decisions worth stating plainly

- Sessions are opaque random tokens; only a hash is stored. Cookies are `HttpOnly`, `SameSite=Lax`,
  and HTTPS-only (`Secure`) when `COOKIE_SECURE` is on, which production validation requires.
- CSRF uses a signed double-submit cookie on every state-changing request, including `multipart`.
- Denials are audited in their own committed transaction after the refused request is rolled back.
- Responses are `no-store`, carry CSP without inline scripts, `X-Content-Type-Options`,
  `Referrer-Policy`, and HSTS outside development.
- Sign-in failures are indistinguishable: same message, same status, same body.

## Documentation

| Document | Purpose |
| --- | --- |
| [FEATURES.md](FEATURES.md) | The 25 features with routes, roles and the test that proves each |
| [DEFECTS.md](DEFECTS.md) | Defects found and fixed, and the open gaps stated plainly |
| [DECISIONS.md](DECISIONS.md) | Every decision where the brief was silent, with the reason |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md) | Guided tour per role, including the edge cases |
| [RISKS.md](RISKS.md) | Residual risks, honestly |
| [DISPLAY_GUIDE.md](DISPLAY_GUIDE.md) | Live-demo materials: the section 16 scenario, architecture one-pager, labelled release status, test evidence and the mentor Q&A |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each version |
