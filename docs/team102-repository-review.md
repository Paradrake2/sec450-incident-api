# Team 102 repository and live testing discovery

Reviewed October 31, 2026. Source review and live observations are separate.
No third-party source code was executed or modified.

## Source identity and version

The public repository is [dredinbo15/SEC450](https://github.com/dredinbo15/SEC450).
Its testing document explicitly identifies Team 102, Pod C, and names the
already known `seclog.redinbo.win` hostname. This review pins source to
`1ef6b270b686ecc845c4946a84514475e01205af`; it does not identify the deployed SHA.

The [testing contract](https://github.com/dredinbo15/SEC450/blob/1ef6b270b686ecc845c4946a84514475e01205af/docs/testing-interface.md)
documents these bases:

| Role | Base URL | Controls |
| --- | --- | --- |
| System | `https://seclog.redinbo.win` | `POST /test/setup`, `GET /test/report` |
| Tester | `https://seclog.redinbo.win/tester` | `POST /test/setup`, `POST /test/run`, `GET /test/report` |

The `/test` controls require no caller credential. The reviewed
[implementation](https://github.com/dredinbo15/SEC450/blob/1ef6b270b686ecc845c4946a84514475e01205af/sec450/api/testing.py)
registers a system session without deleting collected data. It documents a
temporary server-issued read-only key for its own tester runs. The ordinary
`/v1` data feed still requires its API key. Setup is registration, not a shared
fixture reset; sessions/reports are in memory and not durable across restarts.

## Direct live observations

The discovery used fixed HTTPS URLs, no redirects, and no saved API key.
No global reset or collected-data modification was requested.

| Request | Observed HTTP status |
| --- | --- |
| `GET /health` | 200 |
| `GET /test` | 200; expected system/tester control inventory |
| `GET /tester/test` | 200 |
| `GET /api/v1/health` | 404 |
| `POST /test/setup` with a unique synthetic session and empty setup | 201; session echoed |
| `GET /test/report` for that session | 200; passed our strict system-report model |

A separate external invocation of **Team 102's tester against Team 102's own
system** completed at `2026-11-01T03:25:07.965569+00:00` (UTC): setup 201,
pre-run report 409, run 200, stored report 200, and system report 200. The suite
report passed our strict report model, matched the run response, and stayed
unchanged on another retrieval. A second run and duplicate system setup both
returned 409 with valid classified protocol-error bodies.

It contained **28 results: 27 pass, 1 skip, no fail/error**. `filter-source-ip`
was skipped because no recent event had a client IP to filter on. Reported
coverage was 8/8 operations and 7/30 acceptance criteria, with code coverage
null/null. These are Team 102's own executed-operation/criterion inventories,
not our incident coverage and not counts of passed requirements. Their eight
operation inventory also does not include the newer `/v1/logs` read.

This verifies the observed course-control sequence and response format, not a
bidirectional cross-team incident acceptance run. The tested deployment SHA,
all session/abort edge cases, and full coverage instrumentation remain
unverified. Full local evidence is excluded from Git under `.team-local/`.

The reviewed source also caps its in-memory registries at 500 entries and can
evict old sessions/reports; whole-suite abort handling saves a generic suite
error rather than prior per-case progress. These are source-level limitations
to reconcile with retention/partial-report expectations, not behaviors tested
by this successful live flow.

## What their source provides

The reviewed [application routes](https://github.com/dredinbo15/SEC450/blob/1ef6b270b686ecc845c4946a84514475e01205af/sec450/api/app.py)
provide health, read-only events/logs/clusters/reports/integrity, and the course
controls. They do not implement our nine-operation `/api/v1/incidents` workflow.
Swagger/OpenAPI are deliberately disabled in that source. The live 404 at
`/api/v1/health` is consistent with this scope; no destructive route probes
were made.

The [fixture README](https://github.com/dredinbo15/SEC450/blob/1ef6b270b686ecc845c4946a84514475e01205af/tests/fixtures/README.md)
documents six failed SSH logins in 50 seconds, expected to trigger detector
R1/high at its five-failure/five-minute threshold. The
[scenario generator](https://github.com/dredinbo15/SEC450/blob/1ef6b270b686ecc845c4946a84514475e01205af/tests/scenarios.py)
provides ground truth for brute force, scans, injection, and large transfers.
These fixtures are versioned by the source commit, not a separate fixture
release found in the inspected files. They are detector oracles, not an agreed
materiality/review/disclosure fixture set. Detection severity must not be
silently treated as a materiality label.

Team 102 already has an optional
[report-to-incident adapter](https://github.com/dredinbo15/SEC450/blob/1ef6b270b686ecc845c4946a84514475e01205af/sec450/incidents.py).
Its [example configuration](https://github.com/dredinbo15/SEC450/blob/1ef6b270b686ecc845c4946a84514475e01205af/config.example.yaml#L122-L128)
targets our deployed incident POST, with forwarding and backfill disabled by
default. Actual deployed enablement was not inspected. The adapter maps source
reports into title/log/occurredAt/source and tracks deliveries; this does not
give our receiver an idempotency guarantee after an ambiguous timeout. Before
adding pull-to-incident mapping, agree push/pull ownership and stable source-ID
deduplication. Our current snapshot importer creates no incidents.

## Consequences for our next testing stage

The course-control URLs no longer need to be requested. The remaining pod
question is whether the agreed project contract requires interchangeable
nine-operation incident services or producer/consumer integration with distinct
suites. The supplied Phase 2 contract and their current project API differ;
availability of matching course controls does not resolve that difference.

Continue our known incident-contract corrections and isolated system-session
work locally. Then configure approved HTTP transport and explicitly agreed
cases for the intended peer interface. Do not run the incident reset suite
against their log feed, or call their self-check a successful test of our API.
Shared materiality labels, fixture restoration, session/coverage rules, and
real bidirectional results still require completion.
