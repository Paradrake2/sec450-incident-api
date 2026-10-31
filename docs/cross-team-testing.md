# Cross-team testing protocol foundation

This milestone implements the generic tester control plane. It is partial
T-02 work, with shared protocol models useful to T-01. It does not establish
pod project conformance, a deployed tester, or completed cross-team testing.

## What exists

The main application mounts an independent FastAPI tester at `/tester`:

| Method and actual path | Behavior |
| --- | --- |
| `POST /tester/test/setup` | Validate a protocol request and, when configured, prepare an approved target with a new session ID. |
| `POST /tester/test/run` | Atomically claim one ready session, run its configured cases once, and save a suite report. |
| `GET /tester/test/report?session_id=...` | Return a detached copy of a stored completed/aborted report. |
| `GET /tester/openapi.json` | Describe the three controls and concrete request, result, coverage, and error models. |
| `GET /tester/docs` | Interactive API documentation for the tester. |

The parent incident API and its error format remain separate. The mounted
app's operations use `message` and `source` errors. Invalid protocol bodies,
queries, or POST media types return 400 rather than FastAPI's default 422.

**The default mounted tester is deliberately unconfigured.** It has no gateway,
project suite, or approved targets. Setup returns 500/source `tester` with a
configuration explanation before creating a session or making any outbound
request. An empty suite cannot be configured to produce a misleading passing
report. Unknown run/report sessions return 404.

This makes the confirmed protocol mechanics inspectable and testable without
guessing the pod's project operations or enabling an arbitrary URL fetcher.
There are no live-network adapters or approved target URLs in this milestone.

## Adapter boundaries

`create_tester_app(coordinator, approved_targets=...)` creates a configured app
only when trusted application code supplies the necessary pieces:

- `TargetGateway.prepare(target_url, session_id)` prepares a target and returns
  its echoed session ID. A future HTTP adapter must require the target's 201
  success response, translate classified errors, and return only after readiness.
- `TargetGateway.code_coverage(target_url, session_id)` returns a validated code
  coverage measure for the session. The current tests provide mock gateways.
- `SuiteCase(test_id, run)` supplies an async project check. Cases share a
  `TestContext` containing the target URL, session ID, and per-run state. Future
  adapters own the agreed project requests, routing, expected outcomes, and
  prerequisite skip decisions.
- `ProtocolFailure(status_code, message, source)` carries a classified failure.
  Target setup errors retain their status/message/source. Classified case
  failures produce an error result without automatically aborting the suite.

The full configured setup/run/report flow is exercised over HTTP in
`test_tester_app.py`, using injected mock targets and synthetic checks. These
examples validate orchestration, not any target's real project behavior.

## Session and report behavior

The coordinator stores sessions in memory for one process's lifetime. A short
thread lock protects run claims and report publication across request threads;
no adapter is awaited while holding the lock. No expiry or cleanup removes
active sessions or completed reports. Multiple processes do not share this
registry, and a restart loses it; use a single worker for this implementation.

Setup creates no ready session on failure, timeout, or mismatched echoed ID.
A successful session begins ready; report retrieval returns 409 until it has
finished. Only one run can claim it, and concurrent/subsequent runs return 409,
including after an abort. Invalid requests do not consume a ready session.

Each configured case produces exactly one result, in plan order. Unexpected
case exceptions, malformed adapter results, or run timeouts preserve prior
results, mark the interrupted case as error/source tester, and mark subsequent
unexecuted cases skip. The partial report is stored before the run returns a
500/source tester. Cancellation also freezes the partial report before
propagating cancellation. A process crash can still prevent report recovery.

Normal assertion failures and classified target/communication errors remain in
a normally completed report, returned with suite HTTP 200. Report retrieval
never reruns cases or refetches target coverage. Reports are serialized to an
internal JSON snapshot; changing a returned response or an adapter-owned model
cannot change subsequent lookups.

Code coverage is taken from the injected gateway. A classified coverage
retrieval failure produces null/null counts with an explanatory basis; an
unexpected internal failure during retrieval aborts while retaining all case
results already recorded. Component and system coverage remain null/null with
explicit pending-inventory explanations. No component/AC totals are invented.

The default setup/run bounds are 10 and 30 seconds, configurable on the
coordinator. These are local implementation bounds, not registered course
timing guarantees or measured Render cold-start limits. The run bound includes
case execution and code coverage retrieval. Future network adapters also need
bounded connection/read operations and response sizes. No automatic project
request retry policy is supplied.

## Target admission and outbound limits

`ApprovedTargets` checks an explicitly configured full base URL, including its
scheme, hostname, port, and base path. It canonicalizes hostname case/IDNA,
default ports, and a final slash, then requires an exact configured match.
Credential-bearing URLs, query/fragment components, ambiguous encodings,
invalid authorities, local hostnames, and nonpublic literal addresses are
rejected. A path prefix or similar hostname does not grant access.

This is offline logical URL admission. It performs no DNS resolution and does
not guarantee an approved DNS name resolves to a public address. Before any
live outbound adapter is enabled, implement connection-time DNS/address
enforcement, redirect restrictions, timeouts, response-size bounds, and any
documented target credentials. Keep credentials out of URLs and reports.

## Pending system/project integration

- Root system `POST /test/setup` and `GET /test/report` are not implemented here.
  Existing `/api/v1/test/reset` must not be reused as global session setup.
- The PostgreSQL pytest helper is local regression infrastructure. It is not a
  system session registry or a production session-routing implementation.
- The pod must agree which project operations every target supports, schemas,
  fixtures/labels, session correlation, reset/cleanup/concurrency behavior,
  coverage inventories, access, and timing/retry rules.
- No routing header is assumed and no real project checks are selected by
  default. Team 102's read-only report feed is not assumed to implement our
  incident workflow.
- Published registry URLs, deployment, measured limits, and real bidirectional
  cross-team runs remain later review steps.

## Local validation

Use the normal disposable-store harness. With no `TEST_DATABASE_URL`, tests
use SQLite; an explicit approved local test URL opts into PostgreSQL schemas.

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_testing_protocol.py test_tester_core.py test_tester_targets.py test_tester_app.py
```

The models validate paired unknown coverage counts, strict measured counts,
error sources, result identities, and serialization. Lifecycle tests exercise
concurrent claims, partial aborts, timeouts/cancellation, stored report
immutability, and setup recovery. HTTP tests exercise mounted OpenAPI, error
translation, admission before gateway calls, normal reports, and abort lookup.
All target behavior is injected and synthetic; these tests make no external
requests or changes to a deployed service.

The protocol behavior comes from the supplied instructor Cross-team Testing
Interface. FastAPI's [independent mounted-app documentation](https://fastapi.tiangolo.com/advanced/sub-applications/)
and Pydantic's [serialization documentation](https://docs.pydantic.dev/latest/concepts/serialization/)
describe the framework mechanisms used here.
