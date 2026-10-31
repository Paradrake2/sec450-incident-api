# Implementation status and decisions

Updated October 31, 2026. This log records repository work separately from the proposed roadmap in `SEC_Project_Codex_Implementation_Plan.md`.

## Baseline and scope

- Starting branch: `main`; clean working tree.
- Starting commit: `705dd115c174f7c64f317567364429d5c930694e`, matching the plan's pinned review.
- Foundation branch: `phase4/database-foundation`; current API-import branch: `phase4/api-report-import`.
- No repository or ancestor `AGENTS.md` was found in the inspected ancestry.
- First implementation slice: B-01 baseline/specification reconciliation and B-02 consistent database access. Later course testing, product UI, evidence, AI, and deadline work remain separate tickets.
- The architecture image describes product intent: preserve evidence, support advisory analysis and human decisions, prepare non-final drafts, and record history throughout. It does not establish implemented external integrations or authorize automatic filing.

The implementation plan, architecture image, repository source, original ten-page instructor **Cross-team Testing Interface** PDF, and teammate handoff were independently read for this slice. The user also supplied [the linked instructor document](https://docs.google.com/document/d/1GhEVTw3a2tNJY2arfDyWgWS94kVYfgC7/edit), retrieved through connected Drive as `student-facing-spec.docx` (Word document; last modified `2026-10-19T14:57:46.729Z`). It contains the same course-wide testing protocol. It is not the missing pod-specific incident API contract. Its embedded OpenAPI starter declares `3.1.1` and `info.version: 0.4.2`; these values do not establish a version for the entire instructor document.

The original instructor sources confirm separate logical system/tester interfaces, safe session preparation, one suite run per tester session, immutable completed/aborted reports, result-level error sources, and honest coverage counts. They permit serialized sessions and documented authentication. They do not define this pod's incident fixtures, session-correlation mechanism, component/AC inventories, cleanup policy, or project-call limits. The latest pod-shared project contract remains required for those choices.

## Completion and validation

| Ticket | Status | Changed areas | Validation evidence | Remaining decisions |
| --- | --- | --- | --- | --- |
| B-01 | Local baseline and source reconciliation complete; pod-specific decisions open | README and contract-audit corrections; actual root-level Python syntax targets in CI; this decision log | Before implementation: `69 passed`, 3 deprecation warnings, 5.36s; configured Ruff selection passed | Current pod contract |
| B-02 | Complete locally; ready for review | Request-scoped database/session selection; explicit-store initialization/backfills; seven isolated-store regressions in `test_database.py` | `76 passed`, 3 baseline deprecation warnings, 5.81s; syntax, configured Ruff, dependency check, and exact OpenAPI comparison passed | Session routing/storage policy for T-01 |

This validated B-01/B-02 checkpoint was committed through the user's connected
GitHub account, `Krish9Patel`, as
[`e0cfc80e6076279f8543ec8780f122fe16ceed01`](https://github.com/Paradrake2/sec450-incident-api/commit/e0cfc80e6076279f8543ec8780f122fe16ceed01)
on the branch now named `phase4/database-foundation` for incremental review. No schema migration,
merge, or deployment was performed. The Render deployment SHA and live
PostgreSQL behavior were not verified by this initial foundation slice.

The baseline ran in the workspace `.venv` with Python 3.12.6, FastAPI 0.162.0, SQLAlchemy 2.1.6, pytest 9.2.0, HTTPX 0.28.1, and Ruff 0.17.3. CI uses Python 3.11. The configured static command is `python -m ruff check --select E4,E7,E9,F63,F7,F82 .`.

After publishing the foundation checkpoint, both push-triggered workflows
completed successfully for `e0cfc80`: [API tests](https://github.com/Paradrake2/sec450-incident-api/actions/runs/41205562580)
and [API Tests and Static Checks](https://github.com/Paradrake2/sec450-incident-api/actions/runs/41205562499).
These runs precede the PostgreSQL milestone below.

Final local validation:

- Full pytest suite: **76 passed**, 3 pre-existing deprecation warnings, 5.81s, using isolated synthetic SQLite stores.
- Syntax compilation: all 11 root-level Python files passed.
- Configured Ruff selection, `pip check`, and `git diff --check`: passed.
- Generated OpenAPI: deep equality with the starting commit's schema, obtained by executing its `main.py` in a separate module. Injected dependencies add no request parameters or wire-schema changes.
- Seven new regressions verify an alternate store's full workflow/history/reset preserves every normal-store table snapshot; cross-store IDs return `404` for retrieval, histories, writes, and links; startup creates only the selected schema; matching-ID legacy backfills stay scoped and idempotent; concurrent resolver overrides route two prepared stores without changing global engines; flushed incident/review changes roll back on failure and request sessions release connections; and dependencies stay out of the wire schema.

## Database access seam

The B-02 change introduces request-scoped access while preserving `main:app` and existing `main.engine` imports:

- `main.get_engine(request)` selects `request.app.state.database_engine`, initialized to `main.engine` when the application is created.
- `main.get_session`, injected through `Depends(get_engine)`, opens a fresh SQLAlchemy session for the request and closes it on completion. Existing routes retain their explicit transaction commits; uncommitted work rolls back when the session closes.
- `main.initialize_database(database_engine)` creates tables and runs both legacy backfills against that explicit engine. Backfill functions accept an optional engine while retaining their existing no-argument calls.
- Incident CRUD, assessments, reviews/history, links, disclosures/history, and reset all use the injected session.

Configure `app.state.database_engine` before entering the application lifespan when startup and requests should use an alternate store. A FastAPI dependency override affects request handling; it does not redirect startup automatically. An override selecting another engine must explicitly initialize that store, or configure the application store before startup. This distinction prevents backfills from silently targeting a different database from the intended store.

This seam does not introduce a test-session header, a session registry,
authentication, or course-session cleanup. Those remain later work. Startup
still uses `create_all` and legacy backfills. PostgreSQL validation and
competing-state protection are recorded in the milestone below; managed
migrations remain F-01 work.

## PostgreSQL milestone - October 31, 2026

F-01 is **partially implemented**: PostgreSQL development/test configuration,
isolated regression stores, conditional state updates, and timezone handling
are complete locally. Managed schema migrations, database readiness, broader
configuration validation, and recovery verification remain outstanding.

The user supplied
[`https://sec450-incident-api.onrender.com/openapi.json`](https://sec450-incident-api.onrender.com/openapi.json).
It was fetched and its full generated schema matched the repository schema at
comparison time. It describes the deployed incident API, including history
extensions, with OpenAPI `3.1.0` and application version `1.0.0`. It has no root
`/test/setup` or `/test/report` paths. It does not establish shared pod fixtures,
session routing, cleanup, coverage inventories, or project-call limits. The
deployed SHA was not inferred from schema equality.

Delivered in this slice:

- A separate native PostgreSQL 17.4 cluster under ignored
  `.venv/postgres/data`, bound to `127.0.0.1:55432`. Application database
  `sec450` and test database `sec450_test` use separate non-superuser roles.
  Generated credentials are confined to ignored local files. No existing
  database or SQLite evidence was migrated or modified.
- Compose development configuration and `.env.example`, with a separate
  PostgreSQL test database. Uvicorn can load the ignored `.env` file; SQLite
  remains an explicit fallback/quick-test path.
- A PostgreSQL 17 CI job alongside SQLite/static checks. `TEST_DATABASE_URL`
  selects only an explicitly named loopback test database. Each regression
  store gets a generated schema, restricted search path, and scoped cleanup;
  tests never derive a destination from application `DATABASE_URL`.
- Conditional assessment/review updates reject competing stale state changes
  with `409`. A review's state change and history remain one transaction.
  Deterministic competing-request tests exercise real PostgreSQL transactions.
- Occurrence timestamps normalize to UTC before persistence, and public
  timestamp serialization normalizes to UTC. Offset and naive-time regressions cover a
  SQLite/PostgreSQL difference that previously lost the supplied instant on
  SQLite. Legacy review history normalizes parsed times without overwriting
  the original JSON snapshot. Naive times retain the existing UTC interpretation.

Validation on Python 3.12.6:

| Backend/check | Actual local result |
| --- | --- |
| PostgreSQL 17.4 full suite | **97 passed**, 3 existing dependency deprecation warnings, 14.10s |
| SQLite full suite | **95 passed, 2 PostgreSQL-only tests skipped**, 3 existing warnings, 12.75s |
| Compose configuration | `docker compose --env-file .env.example config --quiet` passed; containers were not started |
| Static/dependency checks | All 15 root Python files compiled; configured Ruff, `pip check`, and whitespace checks passed |
| Application and cleanup smoke checks | Local app uses PostgreSQL as a non-superuser; health/retrieval passed; no generated test schemas or public-schema test tables remained |

These results do not establish a new GitHub CI run, a live Render PostgreSQL
test, a migration of existing records, or a shared company-data integration.
The PostgreSQL implementation and company-data design are separate incremental
checkpoints. The PostgreSQL implementation is commit
[`fceb9da9cd4bac8e420b788ea81b0df57e623cce`](https://github.com/Paradrake2/sec450-incident-api/commit/fceb9da9cd4bac8e420b788ea81b0df57e623cce).
The supplied Team 102 Query API document describes read-only
authenticated reports, not shared database access. A public health read at
`https://seclog.redinbo.win/health` returned HTTP 530 during this session;
authenticated endpoints were not exercised.

## Decisions and open questions

| Topic | Working decision or missing evidence | Needed by |
| --- | --- | --- |
| Shared API | Preserve existing `/api/v1` JSON, statuses, enums, deterministic assessment, and human review rules. Obtain the current pod contract with an identifiable version/date before definitive conformance claims. | B-01 reconciliation; T-01–03 oracles |
| Validation precedence | Missing incident plus invalid body: current `404`/`400` precedence must be compared with the pod contract. | Shared contract changes |
| Extra fields | Existing request schemas forbid extra fields. Pod agreement for acceptance/ignore/rejection remains unverified. | Shared contract changes |
| AC-03 labels | Local deterministic fixtures do not establish shared human materiality labels. | T-03 outbound suite |
| Setup/reset fixtures | Existing `/api/v1/test/reset` deletes data; it does not prepare an isolated session or restore an agreed fixture set. Never use the shared deployed reset for local work. | T-01 |
| Session isolation | Explicit store selection is implemented and locally tested. Routing header, accepted session lifecycle, concurrency/serialization, retention, restart behavior, and cleanup need a documented pod policy. A header alone does not isolate data. | T-01 |
| Coverage | Component/AC inventories and definitions of exercise are missing. Unavailable measures must be reported honestly rather than as fabricated counts. | T-01–03 |
| Timing/access | Confirm setup/run/project time limits, retry rules, approved target URLs, and staff/podmate access. Do not assume Team 102's separate query API implements this incident API. | T-02–03 registration |
| Closure | `CLOSED` exists without a close route. Do not infer or introduce shared closure semantics. | W-01 |
| Authentication | User chose application accounts with administrator-approved roles. Current API/reviewer strings remain unauthenticated; implement a protected product data boundary before real use. | F-02 |
| Company data | Use Team 102's read-only Query API with application-owned PostgreSQL storage. Shared PostgreSQL is deferred. Confirm the detail-response schema, source IDs/corrections, and incident mapping before live automatic integration. | API ingestion/evidence work |
| Hosting | Keep existing Render configuration; AWS remains deferred. Shared service merge/deployment review is a separate step. | Future release |

## Next review stages

1. T-01 through T-03: finish isolated system setup/report, connect the tester
   lifecycle to a real approved transport, and implement the agreed outbound
   project suite. Testing is the current priority. Confirm session routing,
   fixtures, coverage, retention, access, and timing with the pod.
2. F-02: implement application accounts and administrator-approved roles, with
   contributor/reviewer/administrator permissions and a protected data boundary.
   Review authority must come from authenticated identity rather than a
   submitted name.
3. Evidence and analysis schema: agree the company-data ingestion envelope,
   preserve evidence/revisions and supported facts, then add advisory summary
   and analysis records. AI remains advisory and must not record a human
   determination. See [the working design](company-data-design.md).
The instructor protocol is checked; the deployed OpenAPI is a current API
description, not the missing pod session/fixture rules. W-01 follows these
testing and product foundations.

Managed migrations should precede new persistent product schemas. Keep each
stage as a validated commit/review checkpoint, preserving the existing shared
API and separating merge/deployment from local implementation.

## Tester protocol foundation - October 31, 2026

Implemented shared control/report models, offline exact approved-target
admission, and a process-local tester coordinator with injected adapters. The
main application mounts its independent HTTP controls and OpenAPI at
`/tester`. The default instance has no target gateway, cases, or approved URLs;
setup fails closed before any outbound request or ready-session creation.

Mock-target tests exercise the successful setup/run/report flow, error-source
propagation, concurrent one-run claims, immutable completed/aborted snapshots,
coverage validation, partial reports, timeout/cancellation recovery, and
rejection of invalid adapter results. Cancellation before any case is invoked
records only skipped cases. Component and system coverage remain null/null
with pending-inventory explanations.

This is partial T-02 work and a shared schema foundation for T-01. Root system
setup/report, system session storage/routing, real HTTP transport, the pod
project suite/oracles, inventory mappings, registration, and cross-team runs
remain pending. Offline URL admission does not resolve DNS or establish live
network safety. In-memory sessions require a single process and are lost on
restart. The internal timeout defaults are not registered Render time limits.

The parent incident OpenAPI was compared deeply with the preceding checkpoint
and is unchanged. See [cross-team testing implementation notes](cross-team-testing.md)
for exact adapter boundaries, configuration limits, and validation details.

Final local validation: **385 passed** on PostgreSQL 17.4 (7.47s); **383 passed,
2 PostgreSQL-only skips** on SQLite (7.53s). Both runs retained three existing
dependency deprecation warnings. The configured Ruff selection, syntax checks
for all 24 root Python files, and whitespace checks passed. These are regression
and mock-target results, not real outbound project-suite or deployment evidence.

## API report-preservation checkpoint - October 31, 2026

The selected integration now uses Team 102's existing read-only Query API;
shared PostgreSQL remains deferred. This work is separate from the course
testing controls and is recorded on `phase4/api-report-import`.

Implemented a fixed-origin HTTPS report client, an explicit one-report import
CLI, and private immutable snapshot/observation storage. Semantic JSON digests
deduplicate repeated content for a source ID, while changed content creates a
new snapshot and each retrieval creates an observation. Fixtures have a
separate source namespace. Imports cannot alter incident/review/draft state.

Alembic revisions freeze the four legacy tables and add the two private source
tables. Explicit migration validates complete legacy schema topology before
adoption; partial/drifted stores are refused. Fresh/adopted upgrade rollback,
data preservation, import-table downgrade, and migration/model parity are
tested. The subtype review found and fixed acceptance of CHAR as VARCHAR and
CITEXT as TEXT. New report tables are not created by legacy application startup.

Final local validation: **536 passed** on PostgreSQL 17.4 (24.38s); **533 passed,
3 PostgreSQL-only skips** on SQLite (13.32s), with three existing warnings per
run. Ruff, dependency validation, whitespace checks, and syntax for all 37
Python files including revisions passed. A validated local PostgreSQL database
was upgraded and the committed synthetic fixture imported twice: one snapshot,
two observations, and unchanged incident row count. No Render migration or live
source request occurred.

The user asked whether our deployed incident schema also describes Team 102's
reports. A fresh read of our OpenAPI confirmed `CreateIncident` requires title,
log, and occurredAt; `IncidentResponse` adds string ID, state, assessment,
review, and related IDs. The supplied Query API guide instead describes numeric
report_id and a nested body. Team 102's public `/openapi.json` returned HTTP 530
when checked. These may be different interfaces; identical live schemas have
not been established. The minimal source parser remains isolated/provisional,
and a sanitized source example has been requested. No automatic field mapping,
incident creation, polling/cursor, or live compatibility claim is supplied.

See [manual API import and migration notes](team102-api-import.md).
