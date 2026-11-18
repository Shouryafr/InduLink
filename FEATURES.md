# Feature inventory

Every feature in the prototype, the route that serves it, who may use it, and the
test that fails if it breaks. "Proof" names the test; "route guard" means the
authorization matrix covers the route and the screen is reached in the crawl, but
no behaviour-specific assertion exists yet — those are the honest partials, and
they are also listed in [DEFECTS.md](DEFECTS.md#open-gaps).

Run everything: `make test` (445 tests). Scenarios alone: `make scenarios`.
The live-demo script for the canonical challenge is [DISPLAY_GUIDE.md](DISPLAY_GUIDE.md).

## Accounts and access

| ID | Feature | Routes | Roles | Proof | Status |
| --- | --- | --- | --- | --- | --- |
| F01 | Registration, sign-in, sign-out, password help | `/register`, `/sign-in`, `/sign-out`, `/forgot-password` | public | `tests/integration/test_auth_flow.py` | done |
| F02 | CSRF on every state-changing request | all POST routes | all | `test_auth_flow.py::TestCsrf` | done |
| F03 | Server-side role authorization and object ownership | every protected route | all | `tests/authz/test_permissions.py::test_route_matrix`, `::test_denial_is_audited` | done |
| F04 | Public site: landing, how it works, privacy, terms, search | `/`, `/how-it-works`, `/how-recommendations-work`, `/privacy`, `/terms`, `/search` | public | `tests/integration/test_accessibility.py`, `test_copy_and_ribbon.py` | done |

## Profile, registry and evidence

| ID | Feature | Routes | Roles | Proof | Status |
| --- | --- | --- | --- | --- | --- |
| F05 | Student profile, visibility and availability | `/student/profile`, `/students/{id}` | student owns; mentor/industry/admin per visibility | `test_permissions.py::TestStudentProfilePrivacy`, `test_accessibility.py` | done |
| F06 | Skill registry, aliases and proposals | `/admin/skills`, `/mentor/registry`, `/student/profile/skills` | admin curates; mentor declares; student adds/proposes | `test_integrity.py::TestConstraints`, route guard | partial (alias/proposal flows proven only at route level) |
| F07 | Evidence submission across six categories | `/student/evidence` | student | `tests/scenarios/test_scenarios.py::test_scenario_b_...` | done |
| F08 | Evidence review with decision and note | `/mentor/evidence/{id}/review`, `/mentor/evidence` | mentor, admin (never own) | `test_scenario_b_...`, `test_permissions.py::TestEvidenceReviewPermissions` | done |
| F09 | Challenge authoring with weighted, versioned requirements | `/industry/projects/new` | industry | `test_integrity.py::TestProjectVersioning`, `test_scenario_c_...` | done |
| F10 | Approval and the project state machine | `/admin/projects`, `/industry/projects/{id}/state` | admin approves; owner moves | `test_scenario_c_...`, `test_failure_paths.py::TestIllegalTransition` | done |

## Matching

| ID | Feature | Routes | Roles | Proof | Status |
| --- | --- | --- | --- | --- | --- |
| F11 | Deterministic ranking with hard constraints before soft scores | `/industry/projects/{id}/candidates` | owning partner, admin | `tests/unit/test_matching.py` (property tests), `test_demo_dataset.py::TestDeliberateEdgeCases::test_a_hard_constraint_failure_never_outranks_a_high_soft_score` | done |
| F12 | Why panel: per-requirement contribution, eligibility and reasons | `/industry/projects/{id}/candidates` | owning partner, admin | `test_scenario_c_...` asserts the panel renders; scoring reasons in `tests/unit/test_matching.py` | done |
| F13 | Six separate skill fields and "no data" never shown as zero | `/student`, `/industry/projects/{id}/candidates` | student, partner | `tests/unit/test_evidence_policy.py`, `test_demo_dataset.py::TestDeliberateEdgeCases` | done |
| F14 | Team suggestion, hard-constraint refusal, confirmation | `/industry/projects/{id}/team` | owning partner, admin | `test_scenario_e_...`, `tests/integration/test_concurrency.py`, `test_demo_dataset.py::...test_a_team_that_breaks_a_hard_constraint` | done |
| F15 | Mentor request, fit list and one confirmed mentor per project | `/industry/projects/{id}/mentor`, `/mentor/requests` | partner requests; mentor decides | `test_scenario_d_...`, `test_concurrency.py::test_contested_mentor_slot_...` | done |
| F16 | Mentor expertise registry, capacity and unavailability | `/mentor/registry` | mentor | route guard; capacity rules in `services/mentor_profiles.py` exercised by matching tests | partial |
| F17 | What-If simulation on a read-only path | `/industry/projects/{id}/what-if` | owning partner, admin | `test_scenario_f_...` (domain checksum unchanged) | done |

## Outcomes

| ID | Feature | Routes | Roles | Proof | Status |
| --- | --- | --- | --- | --- | --- |
| F18 | Skill passport: create from a confirmed place, publish, export | `/student/passport`, `/student/passport/{id}/publication`, `/student/passport/{id}/export` | student owns; admin oversees | `test_scenario_g_...` | done |
| F19 | Evaluations and directed feedback with confidentiality | `/industry/projects/{id}/review`, `/student/feedback` | partner writes; student reads own | route guard; seed engagement minimum in `test_demo_dataset.py::TestMinimums` | partial |
| F20 | Appeals against a contribution or evaluation | `/student/appeals`, `/admin/appeals/{id}` | student files; admin resolves | route guard; seed engagement minimum | partial |
| F21 | Notification ledger and inbox | `/notifications`, `/notifications/read-all` | all signed-in | `test_copy_and_ribbon.py` loads the inbox; notifications written by team/mentor/evidence flows | partial |
| F22 | Audit log view (append-only table) | `/admin/audit` | admin | `test_integrity.py::TestAuditAppendOnly` | done |
| F23 | Demo reset, admin-only, audited, measured | `/admin/demo`, `/admin/demo/reset` | admin in demo mode | measured by `tools/measure.py` (1.52 s); reset path exercised here, no dedicated test | partial |
| F24 | Search across visible projects and people | `/search` | all signed-in | route guard | partial |
| F25 | Accessibility floor and honest-claims copy guardrails | every template | — | `test_accessibility.py`, `test_copy_and_ribbon.py` | done |

## Reading the "partial" rows

A partial row is not a broken feature: the screen renders, the role guard is
tested, and the seeded dataset exercises it. What is missing is a test that would
fail if the behaviour regressed. They are counted in the coverage gap in
[DEFECTS.md](DEFECTS.md#open-gaps) rather than hidden.
