# Cross-team testing protocol

The configured delivery in [`delivery_app.py`](../delivery_app.py) exposes an
isolated system at `/system` and an automated incident tester at `/tester`.
Use [the release runbook](phase4-delivery.md) for startup, target selection and
cross-service commands. The standalone `main:app` retains an unconfigured tester
foundation, while `tester_demo` provides a separate synthetic lifecycle demo.

## Tester controls

| Method and path | Behavior |
| --- | --- |
| `POST /tester/test/setup` | Validate an approved target and prepare it with a new session ID. |
| `POST /tester/test/run` | Claim one ready session, execute its configured cases once and save a report. |
| `GET /tester/test/report?session_id=...` | Return the stored completed/aborted report without rerunning cases. |
| `GET /tester/openapi.json` | Describe requests, results, coverage and errors. |
| `GET /tester/docs` | Interactive tester documentation. |

Control errors use `message` and classified `source` fields, separate from
incident API error schemas. Invalid control bodies, queries or POST media types
return `400`. Unknown sessions return `404`; report-before-run and repeated run
return `409`.

An unconfigured `create_tester_app()` has no gateway, cases or approved targets.
Setup fails before creating a session or making an outbound request. The
configured delivery supplies those components explicitly; an empty suite cannot
be used to produce a misleading passing report.

## Adapter boundaries

- `TargetGateway.prepare(target_url, session_id)` prepares a target, requires
  its `201` response and validates the echoed session ID.
- `TargetGateway.code_coverage(target_url, session_id)` retrieves a validated
  code-coverage measure or a classified failure.
- `SuiteCase(test_id, run)` supplies an async project check. Cases share the
  target URL, session ID and per-run state through `TestContext`.
- `ProtocolFailure(status_code, message, source)` carries a public classified
  failure. A classified case error does not automatically abort the suite.

Mock-target orchestration tests are separate from the real HTTP incident suite.
The configured plan includes 41 executable checks and five explicit gap cases;
see [the acceptance mapping](phase4-test-mapping.md) for assertions and limits.

## Session and report behavior

Sessions and serialized report snapshots live in one process. A short lock
protects run claims and publication; adapters are never awaited while holding
it. Reports remain unchanged on retrieval. A restart loses the registry, and
multiple processes do not share it, so use one worker and one service instance.
The delivery caps each registry at 32 sessions and rejects new setup at capacity
rather than evicting reports or stores.

Failed setup, timeout or a mismatched echoed ID creates no ready tester session.
A successful session becomes ready. Only one run can claim it, including when
an earlier run aborted; invalid requests do not consume the ready session.

Each case produces one ordered result. Unexpected exceptions, invalid adapter
results or run timeout preserve prior results, mark an interrupted case as
`error`/source `tester`, and mark later unexecuted cases `skip`. The partial
report is frozen before a tester-abort `500`. Cancellation also preserves a
partial report before propagating. A process crash can prevent report recovery.

Ordinary assertion failures and classified target/communication errors remain
in a normally completed report with HTTP `200`. Report retrieval does not rerun
checks or refetch coverage. Code-coverage lookup failures produce unknown counts
with reasons when classified; unexpected internal failures abort while retaining
completed cases. Component/system coverage remains unknown pending agreed
inventories and mappings. Pass counts are not coverage measurements.

The generic coordinator defaults to setup/run limits of 10/30 seconds. The
configured delivery uses 30/90 seconds, with a 15-second gateway deadline per
call. These are configured bounds, not measured hosting guarantees. No automatic
project-request retry policy is enabled.

## Isolated system behavior

The system controls are `POST /system/test/setup` and
`GET /system/test/report?session_id=...`. Setup creates and seeds a session-owned
store before publishing readiness. Course project requests use
`/system/api/v1`; the normal root API is a separate store.

Each course session uses a generated PostgreSQL schema without `public` fallback
or, in local mode without an explicit course URL, disposable SQLite. Trusted
request context selects its engine and candidate-fixture seed. Unknown or
unprepared contexts never fall back to normal data. Reset affects only the
selected course session and restores candidate fixtures; this does not establish
a pod-approved shared dataset.

An optional `X-Test-Session-Id` selects a retained store. Headerless calls use
the newest prepared session, so external manual/headerless suites must run
serially. Each store permits one active incident request. Dependency cleanup and
lease release finish before HTTP response completion, including empty and
streaming responses. Graceful shutdown cleans only owned stores; abrupt failure
can leave orphaned schemas requiring inspected operator cleanup.

## Target admission and outbound bounds

`ApprovedTargets` canonicalizes and checks the exact full base URL, including
scheme, authority and base path. A similar hostname or child path is not an
approved match. Credentials, query/fragment components and ambiguous encodings
are rejected. Additional public bases require trusted server configuration;
request data cannot change the allowlist or add credentials.

`HttpTargetGateway` checks DNS answers at connection time, rejects nonpublic or
mixed answers, connects to a checked literal address and preserves the original
TLS hostname. Redirects, proxy inheritance and source-credential forwarding are
disabled. Responses are bounded to 2 MiB. Only explicit local factories permit
approved loopback IP literals; a public hostname resolving to loopback remains
rejected.

Transport approval alone does not authorize destructive project operations.
The incident suite permits writes only to its exact configured isolated own
base, and later mutations require a successful isolated reset first. A peer
read-only feed is not assumed to implement the incident workflow and is not
reset by this suite. Interface scope, shared labels/fixtures, session behavior,
coverage, access and timing expectations still need cross-team agreement.

## Local tools

The synthetic demo makes no target calls and uses no database or source key:

```powershell
.\.venv\Scripts\python.exe -m tester_demo
.\.venv\Scripts\python.exe -m tester_demo --scenario fail
.\.venv\Scripts\python.exe -m tester_demo --scenario abort
.\.venv\Scripts\python.exe -m tester_demo --serve
```

Server mode exposes http://127.0.0.1:8001/tester/docs. Setup uses the fixed target
`https://synthetic.invalid/sec450`; it is never resolved. The demo exercises
three synthetic lifecycle cases, not the incident API or peer interoperability.

The separate local audit checks all nine incident operations in a disposable
store and accepts no remote target:

```powershell
.\.venv\Scripts\python.exe -m contract_audit --format markdown
```

For the real configured local HTTP delivery and saved acceptance evidence,
follow [the release runbook](phase4-delivery.md). Unit/regression checks include:

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_testing_protocol.py test_tester_core.py test_tester_targets.py test_tester_app.py
```

The control protocol specifies setup/run/report behavior independently of the
incident contract. FastAPI's [mounted-app documentation](https://fastapi.tiangolo.com/advanced/sub-applications/)
and Pydantic's [serialization documentation](https://docs.pydantic.dev/latest/concepts/serialization/)
describe the framework mechanisms used here. A working control exchange or
local regression run is not proof of complete cross-team conformance.
