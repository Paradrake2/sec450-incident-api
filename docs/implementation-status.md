# Implementation status

The Phase 4 delivery is merged and deployed at release `8f6ce364`. It combines the incident API, an isolated course system, and a configured HTTP test runner. The deployed self-test returned **41 passed, 5 skipped, 0 failed or errored**, with **4/4 control checks passing**. Cross-team testing in both directions remains incomplete.

This document describes implemented capabilities and their limits. The [delivery runbook](phase4-delivery.md), [test and acceptance mapping](phase4-test-mapping.md), and [system models](system-models.md) provide operational and design details.

## Capability status

| Capability | Implemented behavior | Requirements / implementation |
| --- | --- | --- |
| Incident intake and storage | Creates an incident in `RECEIVED`, preserves submitted fields, and normalizes occurrence instants to UTC. | REQ-01, REQ-04, REQ-08; `main.py`, `request_models.py` |
| Retrieval and filtering | Lists incidents, filters by state, and retrieves an incident with its assessment, review, and related IDs. | REQ-02, REQ-04; `main.py` |
| Advisory assessment | Deterministic screening of plain-text logs. Positive flags enter `POTENTIALLY_MATERIAL`; negative flags enter `UNDER_REVIEW`. Assessment alone cannot confirm materiality. | REQ-03, REQ-04; `triage.py`, `main.assess` |
| Human review and history | Accepted decisions update state and append a review record in one transaction. A later material decision can escalate `NOT_MATERIAL`; material downgrades and invalid repeated decisions are rejected. Reviewer identity is submitted text, not verified identity. | REQ-04, REQ-05; `main.review`, `incident_reviews` |
| Related incidents | Stores each undirected pair once; retrieval is symmetric. Linking does not change assessment or review state. Self-links, duplicates, missing incidents, and closed-state writes are rejected. | REQ-06, REQ-08; `incident_relations` |
| Disclosure drafts | Requires a stored human `MATERIAL` review. Each draft is retained as a separate version with `final: false`. No filing is performed. | REQ-05, REQ-07, REQ-08; `disclosure_templates.py`, `disclosure_drafts` |
| Reset | Product-root reset deletes incident, review, relation, and draft records. Isolated-system reset also restores candidate fixtures in the selected store. Shared fixture agreement remains open. | REQ-08; `main.reset`, `course_fixtures.py` |
| Course system | Deployed. Setup creates isolated storage and seeds candidate fixtures before publishing the session. Reports explicitly mark unavailable code coverage as unknown. | T-01; `course_system.py`, `system_sessions.py` |
| Automated HTTP runner | Deployed. Supports target setup, one run per session, classified results, and immutable completed or aborted reports. | T-02, T-03; `tester_app.py`, `tester_core.py`, `tester_http.py`, `incident_suite.py` |
| Source-report preservation | Operator-only importer preserves source snapshots and retrieval observations separately from incident state. Automatic report-to-incident mapping remains unimplemented. | `report_import_store.py`; [import and migration guide](team102-api-import.md) |

The historical common interface remains OpenAPI **3.1.0**, contract version **1.0.0**, preserved independently in [the versioned contract](../contracts/phase2-v1.0.0.openapi.json). All nine common operations remain in the test inventory. Review and disclosure history routes are implementation extensions, not additional common operations.

## Database access seam

Database-backed routes obtain a request-scoped SQLAlchemy session through `main.get_engine` and `main.get_session`. Product requests select the configured application engine. Isolated course requests receive a server-owned engine and fixture callback through trusted request state; request parameters cannot select a database. Invalid or unprepared course contexts never fall back to product storage.

`main.initialize_database(engine)` creates the four incident tables and runs legacy review and relationship backfills against that explicit engine. Configure the application engine before entering its lifespan when startup must use an alternate store. A request dependency override does not redirect startup or initialize its selected store automatically. Uncommitted changes roll back when a request session closes; routes commit accepted mutations before returning success.

PostgreSQL is supported for application and course storage. Disposable SQLite remains available for development and regression tests. Conditional assessment and review updates reject competing stale transitions with `409`; a successful review and its history append remain atomic. Timezone-bearing occurrence instants are normalized before persistence so SQLite and PostgreSQL preserve the same instant.

Managed migrations support fresh databases and validated adoption of the legacy incident schema. Partial or drifted schemas are refused rather than silently adopted. Source snapshot and observation tables are migration-owned metadata and are not created by incident API startup. Existing SQLite data is not automatically migrated into PostgreSQL. Database readiness, recovery verification, and broader operational hardening remain incomplete F-01 work; the health route is not a database-readiness probe.

## Isolated system and tester composition

`delivery_app:create_app --factory` preserves the product API at `/api/v1`, mounts the isolated system at `/system`, and mounts the configured tester at `/tester`. The [runbook](phase4-delivery.md) describes startup and configuration. The standalone `main:app` remains a separate product entry point with an unconfigured tester foundation. `tester_demo` is a synthetic lifecycle demonstration and does not test incident operations.

Each course session owns a generated PostgreSQL schema with no `public` search-path fallback, or a disposable SQLite store in explicit local mode. Public startup requires `COURSE_TEST_DATABASE_URL`. Use of the same physical database as product storage requires an explicit configuration opt-in; this does not establish a shared database with another service. Graceful shutdown cleans up only owned generated stores. Abrupt termination can leave orphaned schemas requiring operator review.

The optional `X-Test-Session-Id` selects a retained system session. Headerless requests use the latest prepared session and require serialized suites. One incident request per session is allowed at a time; setup is rejected while an incident request is active. Response completion waits for dependency cleanup and lease release, including bodyless and content-length streaming responses. Product data remains outside course resets and runs.

System and tester registries each retain at most 32 sessions without eviction. A full registry rejects setup with `503`. Run one worker and one service instance. Sessions and reports are process-local and do not survive a restart. Configured setup, suite, and individual gateway deadlines bound work; these are operational limits, not measured hosting guarantees.

The gateway permits exact configured target bases, checks DNS and network addresses at connection time, connects to a validated address, and preserves the original TLS hostname. It rejects redirects, limits response size, and does not inherit proxy settings or forward source-import credentials. Local mode permits only explicitly approved loopback literals. Every project mutation requires both the exact configured isolated own-system base and a successful reset. Transport approval alone does not permit resetting a peer service.

## Request-contract alignment

Six observed request differences were corrected without changing contract version `1.0.0`:

| Constraint | Implemented behavior |
| --- | --- |
| Optional create `source` | Omission is accepted; explicit `null` is rejected. |
| Optional review `rationale` | Omission is accepted; explicit `null` is rejected. |
| Occurrence format | `occurredAt` requires a timezone-bearing RFC 3339 string. Numeric epochs, date-only values, and naive date-times are rejected. |
| Related-body extensions | Additional properties are accepted and ignored without becoming incident data. |
| Review validation order | Primary incident lookup precedes field validation of an already decoded body. |
| Related validation order | Primary incident lookup precedes decoded-body field validation and uses the same selected request session as the handler. |

Nullable response fields remain nullable. Create and review bodies reject additional properties. Media checks run before the affected operation's lookup; malformed JSON remains an envelope-syntax `400` before dependency resolution. Related-target lookup follows valid body fields and returns `404` when the target is absent. These distinctions are documented in [the request model](system-models.md#request-and-validation-model) and checked at model, dependency, and HTTP boundaries.

## Source integration boundary

The source service exposes a read-only logs/report interface. Its observed report envelope uses a numeric `report_id` and nested object `body`; it is not interchangeable with `IncidentResponse`. A single observed response establishes that sample's shape, not full source-contract conformance. The bounded `team102_probe` command can inspect reports or logs without importing them or selecting an application database.

The importer retains the first raw representation of each semantic source version, deduplicates matching content for a source identifier, and records each retrieval as an observation. Changed content creates a new snapshot. Snapshot and observation writes are transactional; synthetic fixtures use a separate source namespace. Imports cannot modify incident, review, relation, or draft state.

There is no public import endpoint, automatic polling, or automatic incident creation. A peer-side optional push adapter and any future pull mapper require agreed ownership and deduplication rules before activation. Report preservation, incident intake, and course cross-testing are separate integration concerns. Shared company-data PostgreSQL remains a design option, not an established inter-service arrangement. See [the company-data design](company-data-design.md).

## Validation evidence

| Evidence | Verified result | Scope and limitation |
| --- | --- | --- |
| Full PostgreSQL regression suite | **965 passed** | Isolated regression stores; includes API, state, history, persistence, concurrency, protocol, transport, migration, and composition checks. |
| Full SQLite regression suite | **961 passed, 4 PostgreSQL-only skips** | Local regression result; skipped backend-specific checks are not passes. |
| Local historical-contract audit | **44 passed, 0 failed or errored** | Real local routes checked against the preserved contract; unresolved fixture and failure-injection claims remain qualified. |
| Real local HTTP/PostgreSQL self-test | **41 passed, 5 skipped; 4/4 control checks passed** | Exercises the composed system over TCP, including setup, run, report retention, and rerun rejection. |
| Deployed HTTP self-test at `8f6ce364` | **41 passed, 5 skipped; 4/4 control checks passed** | Verifies the deployed own-system route and tester composition; does not establish bidirectional cross-team conformance. |
| Static and dependency checks | Passed | Python syntax, configured Ruff rules, dependency consistency, and whitespace checks. |

Use `python -m pytest -q` for regressions, `python -m contract_audit --format markdown` for the local operation audit, and `python -m course_acceptance` for deployed control and suite evidence. Backend setup and acceptance options are described in the [PostgreSQL guide](postgresql.md) and [delivery runbook](phase4-delivery.md). Keep each result associated with its tested release and backend; do not combine pytest counts with HTTP case counts.

The HTTP suite contains **46 results: 41 executable checks and five explicit gaps**. The skipped checks cover pod-approved reset fixtures, shared classification labels, reachability of `CLOSED`, unexpected server-failure injection, and disclosure content quality. Synthetic screening labels and candidate fixture restoration do not resolve shared agreement. A controlled local `500` test does not establish every declared `500` path or health `503` behavior.

Code, component, and system coverage counts remain `null` with explanatory bases where instrumentation or agreed inventories are absent. Passing cases, operation counts, and successful controls must not be presented as those coverage measures. The [acceptance mapping](phase4-test-mapping.md) identifies concrete oracles and regression anchors.

## Remaining delivery and product work

| Area | Outstanding work |
| --- | --- |
| Cross-team evidence | Execute and retain our tester-to-peer and peer-tester-to-our-system reports. Resolve or disclose mismatches and skipped cases; peer self-tests do not substitute for either direction. |
| Pod contract and fixtures | Confirm current shared scope, versioned fixtures and screening labels, session correlation, coverage inventories, access, and timing expectations. The historical incident API and peer logs/report interface currently differ. |
| Course artifacts | Verify registry system/tester values against the deployed service and assemble submission evidence for the identified release. |
| Reviewer access — F-02 | Implement application accounts, administrator-approved roles, and contributor/reviewer/administrator enforcement. Submitted reviewer text currently grants no verified authority. |
| Evidence and analysis | Agree the source-to-incident envelope, correction/version semantics, deduplication, and supported-fact schema before automatic mapping. Runtime AI extraction and evaluation remain unimplemented; future analysis must remain advisory. |
| Workflow — W-01 | Define and implement deadline handling and closure semantics. `CLOSED` is accepted as a legacy state/filter but has no public transition. |
| Product quality | Build the review interface and disclosure editing/review workflow, and complete recovery and operational verification before non-synthetic use. |

The implemented increment supports synthetic incident handling, advisory screening, recorded human decisions, preserved drafts, and runnable testing. It does not provide verified reviewer authorization, legal materiality determination, automated filing, or production reliability guarantees.