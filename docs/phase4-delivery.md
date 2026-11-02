# Phase 4 system and automated test runner

This is the release candidate's configuration and runbook, not a claim that it
has been deployed. The chosen increment is incident triage, human review,
related incidents and preserved non-final disclosure drafts, plus a runnable
course testing interface. [Current models](system-models.md) describe the
implementation. [Acceptance mapping](phase4-test-mapping.md) records test oracles
and remaining gaps.

## Entry points

Run `delivery_app:create_app --factory` for public delivery, or the explicit
`delivery_app:create_local_app --factory` for loopback development. The old
`main:app` entry point still exposes an unconfigured tester foundation; it is
not the configured course delivery. `tester_demo` remains a separate synthetic
lifecycle demonstration on port 8001.

| Base/path | Purpose |
| --- | --- |
| `/api/v1`, `/docs`, `/openapi.json` | Existing normal application and its incident API documentation |
| `/system` | Isolated course system base; project operations are beneath `/system/api/v1` |
| `/system/test/setup` | POST `{ "session_id": "unique-id", "setup": {} }`; 201 echoes the ID |
| `/system/test/report?session_id=...` | GET system coverage report; code coverage is explicitly unknown |
| `/system/test/docs`, `/system/test/openapi.json` | System control documentation/schema |
| `/system/docs`, `/system/openapi.json` | All incident operations against isolated course state |
| `/tester` | Automated tester base |
| `/tester/test/setup` | POST `{ "target_url": "https://sec450-incident-api.onrender.com/system" }`; 201 returns a new session |
| `/tester/test/run` | POST `{ "session_id": "returned-id" }`; one run, 200 suite report |
| `/tester/test/report?session_id=...` | GET the saved completed/aborted suite report |
| `/tester/docs`, `/tester/openapi.json` | Tester control documentation/schema |
| `/version` | Validated commit SHA from `RENDER_GIT_COMMIT` or operator `BUILD_COMMIT`; null if unknown |

A report before a run returns 409; a second run returns 409. Unknown sessions
return 404. Setup/case errors carry a classified source. Tester failures freeze
partial results; report retrieval does not rerun cases. The product's incident
errors and the system control errors remain separate schemas.

## Local walkthrough

Install `requirements-dev.txt`. For the full delivery with the normal development
database and disposable SQLite course stores:

```powershell
.\.venv\Scripts\python.exe -m uvicorn delivery_app:create_local_app --factory --env-file .env --host 127.0.0.1 --port 8002 --workers 1
```

To exercise PostgreSQL course sessions, set `COURSE_TEST_DATABASE_URL` explicitly
to the dedicated local test database in ignored `.env`. This is separate from
`TEST_DATABASE_URL`, which selects pytest storage. An omitted course URL in local
mode uses temporary SQLite. The public factory refuses an omitted course URL.

Open [the local tester](http://127.0.0.1:8002/tester/docs) and run setup with
`{"target_url":"http://127.0.0.1:8002/system"}`, then run and report using its
returned ID. [The isolated incident API](http://127.0.0.1:8002/system/docs)
shows the other operations. Prepare a session first; headerless calls select
the latest prepared session. The optional `X-Test-Session-Id` selects a retained
session explicitly. Do not use the root product reset for this walkthrough.

The executable acceptance client performs setup, pre-run report, run, saved
report, rejected repeat run and immutable report retrieval:

```powershell
.\.venv\Scripts\python.exe -m course_acceptance --local --output .venv/test-results/local.json
```

It prints counts and saves the schema-checked report. Exit 0 means no observed
case failures/errors and all control checks passed; 1 means observed failures;
2 means configuration/communication/evidence validation could not complete.
Skipped checks do not become passes, and exit 0 does not establish full pod
conformance. The default evidence folder must remain excluded from Git.

## Isolation, fixtures and limits

Each successful system setup creates a new temporary SQLite store or generated
PostgreSQL schema `sec450_course_<uuid>`. PostgreSQL sessions use that schema
alone, with no `public` fallback. Host, port, database and resolved host address
are explicit; ambient libpq routing defaults cannot redirect the store.
Initialization and fixture seeding complete before the session is published.
Unknown, unprepared or invalid session contexts never fall back to normal data.

`fixtures/incident-v1.json` is **Team 105's candidate**, not a pod-approved
dataset. Setup and isolated reset seed the same two stable IDs in RECEIVED state.
Reset also removes the selected session's reviews, relations and drafts. The
normal root reset retains its historical destructive behavior. Candidate labels
exercise the deterministic rule implementation; they do not validate legal
materiality or settle the historical contract's classification disagreement.

The configured suite has **46 results: 41 executable checks and five explicit
gap cases**. It exercises all nine incident operations, response schemas,
positive/negative screening, review transitions, relations, draft eligibility
and request/error boundaries. The five gaps are shared reset fixtures, agreed
classification labels, CLOSED state, injected server failures and disclosure
content quality. Code/component/system coverage counts remain null with reasons;
executed case counts are not a substitute for those measures.

Both registries retain at most 32 sessions per process. Capacity rejects new
setup with 503 rather than evicting saved reports or stores. Use **one worker
and one service instance**. Reports and session IDs are process-local, disappear
after restart and are not backed by a durable registry. Setup timeout is 30s,
suite timeout 90s, and each gateway call 15s; these are configured bounds, not
measured hosting guarantees. One incident request per selected session is
accepted at a time; overlapping requests return 409. For compatibility, run
headerless external suites serially. Parallel setup/request routing is not a
pod agreement.

Graceful shutdown disposes session engines and drops only owned generated
schemas. A hard crash can leave orphaned schemas; an operator must inspect
ownership/session activity before removing one. Cleanup never drops a whole
database or enumerates/deletes arbitrary schemas. A dedicated course database
and role are preferred; using one physical database with separate schemas
does not provide a separate credential/security boundary.

## Target policy and cross-team runs

The public delivery approves its own `/system` and Team 102's confirmed base
`https://seclog.redinbo.win`. Additional bases require trusted server-side
`TESTER_ALLOWED_TARGETS` as a JSON string array. Request bodies cannot add
targets or credentials. Only the exact own `/system` base permits project writes,
and a successful isolated reset is required before later writes. Team 102's
logs/report service is not reset or written by our incident suite. Its separate
protocol setup still registers a test session.

The HTTP gateway checks DNS at connection time, rejects non-public/mixed
addresses, connects to the checked literal IP and preserves the original TLS
hostname. It rejects redirects, limits responses to 2 MiB, uses deadlines, and
does not inherit proxy or source API credentials. Local mode approves only exact
loopback IP literals for the fixed walkthrough; public names resolving to
loopback remain rejected.

After deployment, run our tester against our isolated system:

```powershell
python -m course_acceptance --output .venv/test-results/deployed-self.json
```

Record `GET https://sec450-incident-api.onrender.com/version` immediately before
and after a deployed run, and retain both responses with its evidence. Verify
the SHA agrees with the hosting release/CI and is unchanged across the run;
the acceptance report alone does not identify the deployed commit.

Both cross-team directions can then be recorded with the same client:

```powershell
python -m course_acceptance --target-base https://seclog.redinbo.win --output .venv/test-results/ours-to-team102.json
python -m course_acceptance --tester-base https://seclog.redinbo.win/tester --target-base https://sec450-incident-api.onrender.com/system --output .venv/test-results/team102-to-ours.json
```

Expect project-operation differences until the pod settles scope: Team 102
serves a read-only logs/report API, while our historical contract defines nine
incident operations. Our full case plan remains visible, with writes skipped
for unapproved isolated targets. Record mismatches/skips and owners honestly;
neither a peer self-check nor a successful control exchange proves functional
interoperability. Do not send `SEC450_KEY` to either course tester.

## Render release checklist

`render.yaml` now proposes this startup command:

```bash
uvicorn delivery_app:create_app --factory --workers 1 --host 0.0.0.0 --port $PORT
```

It references the existing Render database for `DATABASE_URL` and explicit
`COURSE_TEST_DATABASE_URL`, opts into a shared physical database with
`COURSE_TEST_ALLOW_SHARED_DATABASE=1`, and fixes `PUBLIC_BASE_URL` to the existing
origin. The database role must be allowed to create/drop its own schemas. Remote
course connections add `sslmode=require` if absent; use a verified certificate
configuration when required by deployment policy. No credential value is in
the YAML. The source-report importer remains operator-only and is not executed
at startup; its migration/adoption procedure remains separate.

Before release, review the PR and green CI, verify the existing service's actual
branch/start command/env settings and whether it is Blueprint-managed, then
deploy the reviewed SHA. Merely merging a YAML change does not prove a manually
configured service uses it. Render documents [Blueprint configuration](https://render.com/docs/blueprint-spec)
and [environment variable changes](https://render.com/docs/configure-environment-variables).

After release, verify `/version` against the intended commit and hosting logs;
Render defines [RENDER_GIT_COMMIT](https://render.com/docs/environment-variables)
as the deployed commit. Capture actual self/cross reports and an operational log
excerpt, then update/read back the service registry with system base
`https://sec450-incident-api.onrender.com/system` and tester base
`https://sec450-incident-api.onrender.com/tester`. These are proposed registry
values until the release is verified. Document the fixture status, serial
sessions, limits, schemas, gaps and access notes there.

Rollback uses the prior reviewed SHA and matching start command/env settings.
Do not delete product data or shared database objects as a rollback step.
Release identity, deployed acceptance results, both cross-team directions and
registry updates remain outstanding until supported by actual evidence.
