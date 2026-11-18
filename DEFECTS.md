# Defects

Defects found while completing the prototype, with severity (1 = corrupts data or
blocks a screen, 2 = a feature gives a wrong or unsafe answer, 3 = visible
mistake, 4 = cosmetic or documentation).

**Open severity 1 or 2 defects: none.** The rows below are fixed, and each has a
test that fails if it returns.

| ID | Sev | Symptom | Cause | Fix | Test that locks it |
| --- | --- | --- | --- | --- | --- |
| D-001 | 1 | `make seed` crashed before writing anything | `data/seed_synthetic.py` used `RecommendationSnapshot` without importing it | imported the model | `tests/integration/test_demo_dataset.py::TestSeedGuards::test_seed_is_idempotent` |
| D-002 | 1 | Two simultaneous team confirmations could create **two** confirmed teams on one project | check-then-write with no database constraint; the mentor slot had one, the team did not | partial unique index `uq_teams_confirmed_project` (migration `4d1b8e6c2a77`) and an `IntegrityError` → `Conflict` translation in `services/teams.py` | `tests/integration/test_concurrency.py::test_double_team_confirmation_leaves_one_team` |
| D-003 | 2 | The synthetic-data ribbon rendered on **no** page, including the demo | Jinja macros imported with `{% from %}` cannot see the rendering context, so `demo_mode` was always undefined inside `demo_ribbon()` | the macro takes the flag as an argument; `base.html` passes it | `tests/integration/test_copy_and_ribbon.py::test_ribbon_is_on_every_demo_page` |
| D-004 | 2 | The seed could give a mentor, faculty reviewer and student the same full name ("Dr Harpreet Singh" vs "Harpreet Singh") | the reserved-name set compared titles, so `Dr X` never matched `X` | `_plain_name()` strips titles before reserving | `test_demo_dataset.py::TestNameAudit::test_every_name_is_from_the_pools_and_unique` |
| D-005 | 3 | A seeded industry contact broke the Sikh naming convention ("Simran Sandhu") | hand-written persona name with a Punjabi first name and a non-Kaur surname | renamed to a pool first name with a pool surname ("Naina Sandhu") | same name audit, Kaur/Singh rule |
| D-006 | 3 | The required "student with declared skills and no evidence" edge case did not exist: project-coverage seeding gave every student evidence | the coverage loop skipped only the private-profile student | the loop also skips the declared-only student | `test_demo_dataset.py::TestDeliberateEdgeCases::test_a_student_with_no_evidence` |
| D-007 | 3 | `admin/demo.html` claimed "append-only guarantees are never bypassed"; `industry/whatif.html` titled a section "How the guarantee works" | copy that reads as an absolute promise | reworded to describe the mechanism and the check | `test_copy_and_ribbon.py::test_no_banned_claims_in_templates_or_docs` |
| D-008 | 4 | README listed demo credentials for phase-1 accounts that no longer exist in the seed | stale phase-1 documentation | replaced with the seeded personas and the reserved domain | `test_demo_dataset.py::TestNamesInText::test_no_stale_demo_names_or_domains` |
| D-009 | 4 | The accessibility audit missed controls labelled implicitly (an `<input>` wrapped in its `<label>`), reporting false positives on the team screen | the parser only recorded `label[for]` | the audit tracks label nesting; the markup is unchanged because it was already correct | `tests/integration/test_accessibility.py` |

## Fixed earlier in the build, kept for the record

- Requirement rows used list syntax SQLite could not parse; replaced by a SQL literal helper.
- Several refused actions redirected silently; every redirect now carries a reason that the target screen renders as a banner.
- Matrix tests signed in with the demo password instead of their own fixture password.
- The 500 page crashed while rendering the chrome; the middleware now keeps an identity snapshot so the error page renders after rollback.

## Open gaps

These are not defects in behaviour; they are things the brief asks for that are
**not** proven here. They are reported rather than waived.

| Gap | Detail |
| --- | --- |
| Coverage thresholds | `core/` 97% (gate 90%, met). `services/` 69% against an 85% target — **not met**. The partial features in [FEATURES.md](FEATURES.md) are where the missing lines sit. |
| PostgreSQL CI | The suite runs on SQLite only. `postgresql_where` clauses exist on both partial indexes and no SQLite-only SQL is used in services, but a PostgreSQL run is unverified. |
| Automated browser accessibility | No axe run and no Playwright journeys. Accessibility is checked structurally plus a manual keyboard pass; see [RISKS.md](RISKS.md). |
| Responsive verification | Checked by inspection and CSS breakpoints at 360/768/1024/1440, not by an automated viewport screenshot suite. |
| Demo reset test | Measured (1.52 s) and exercised through the route, but no assertion guards the behaviour. |
| Features F06, F16, F19–F21, F23, F24 | Route guard and screen render are tested; behaviour-specific tests are missing. |
