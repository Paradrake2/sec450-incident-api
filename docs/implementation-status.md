# Implementation status and decisions

Updated October 31, 2026. This log records repository work separately from the proposed roadmap in `SEC_Project_Codex_Implementation_Plan.md`.

## Baseline and scope

- Starting branch: `main`; clean working tree.
- Starting commit: `705dd115c174f7c64f317567364429d5c930694e`, matching the plan's pinned review.
- Working branch: `codex/database-foundation`.
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

This validated B-01/B-02 checkpoint is tracked on `codex/database-foundation` for incremental GitHub review. No schema migration, merge, or deployment was performed. The Render deployment SHA and live PostgreSQL behavior have not been verified by this foundation slice.

The baseline ran in the workspace `.venv` with Python 3.12.6, FastAPI 0.162.0, SQLAlchemy 2.1.6, pytest 9.2.0, HTTPX 0.28.1, and Ruff 0.17.3. CI remains configured for Python 3.11; local success does not claim a new CI run or PostgreSQL validation. The configured static command is `python -m ruff check --select E4,E7,E9,F63,F7,F82 .`.

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

This seam does not introduce a test-session header, a session registry, authentication, or cleanup. Those remain T-01 design work. Startup still uses `create_all` and legacy backfills; managed migrations and PostgreSQL concurrency validation remain F-01 work.

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
| Authentication | Current API and reviewer strings are unauthenticated. Product access/data boundaries need design before protected product use. | F-02 |
| Hosting | Keep existing Render configuration; AWS remains deferred. Shared service merge/deployment review is a separate step. | Future release |

## Next slice

T-01: implement root system `POST /test/setup` and `GET /test/report` with a documented isolation/lifecycle strategy after resolving the pod-specific setup and session-routing choices. The original instructor protocol has been checked. B-02's store-selection seam enables that work; it does not itself implement cross-team session isolation.
