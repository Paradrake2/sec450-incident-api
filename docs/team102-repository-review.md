# Team 102 integration and testing compatibility

The source-data integration and the common testing controls are separate
interfaces. A compatible setup/run/report exchange does not establish that
both services implement the same incident operations or assessment rules.

## Documented service bases

| Role | Base URL | Controls |
| --- | --- | --- |
| System | `https://seclog.redinbo.win` | `POST /test/setup`, `GET /test/report` |
| Tester | `https://seclog.redinbo.win/tester` | `POST /test/setup`, `POST /test/run`, `GET /test/report` |

The documented `/test` controls do not require a caller credential. Ordinary
`/v1` source-data requests require their own API key. The source service's
setup registers a test session without deleting collected logs; it is not a
shared-fixture reset. Confirm behavior against the intended release before
changing any integration or test policy.

Keep source credentials separate from course-test target configuration. The
incident tester must not forward source API credentials to arbitrary targets
or use source-read access as permission to reset data.

## Project-interface compatibility

The source service exposes health, read-only events/logs/clusters/reports and
integrity information. These are different shapes and operations from this
application's nine-operation `/api/v1/incidents` workflow. A report's numeric
`report_id` and object `body` envelope is not an `IncidentResponse`; no
report-to-incident alias should be inferred from the resource names.

The common course-control routes can still be used to test protocol behavior:
setup creates a session, a run claims it once, and report retrieval returns a
stable saved report. A peer's successful self-check is not either required
cross-team direction and does not verify this application's incident behavior.
Record actual runner/target versions, outcomes, errors, skips and coverage for
each intended cross-team run. Unknown coverage is not a passed requirement.

Source detector fixtures validate detection behavior, such as brute-force or
scan rules. They do not establish agreed materiality, human-review or disclosure
oracles. Detection severity must not be silently treated as a materiality label.

## Incident delivery and preservation

The source integration includes an optional report-to-incident forwarding
adapter. Its example configuration disables forwarding and backfill; actual
runtime enablement must be confirmed separately. The adapter maps reports into
`title`, `log`, `occurredAt` and `source`, and tracks deliveries. Receiver-side
idempotency after an ambiguous timeout still requires an agreed stable source
identity and deduplication policy.

The application's manual snapshot importer is a different path: it preserves
source bytes and retrieval observations without creating incidents. Before
adding automatic source-to-incident mapping, agree push/pull ownership so one
report does not create duplicate application incidents through both routes.
See [report preservation](team102-api-import.md) and the
[company-data boundary design](company-data-design.md).

## Remaining integration agreements

- Confirm whether the common project contract expects interchangeable incident
  services or a producer/consumer workflow with distinct project interfaces.
  Shared scope must not be reduced unilaterally.
- Agree versioned fixture data, assessment labels and reset/restoration behavior.
- Define session correlation, concurrency, retention, cleanup, timeout and
  coverage inventories. Check these policies against each service's actual
  protocol behavior rather than inferring them from a successful self-check.
- Retain results for both cross-team directions and assign mismatch owners and
  resolution plans. Keep source review, configuration and deployed observations
  distinct.

The configured incident suite permits project writes only on its own exact
isolated system base after a successful reset. The peer source-data base is
approved for read-only incident compatibility probes; it is not an approved
incident-reset target. See [the release/test runbook](phase4-delivery.md) for
runner commands and the current limitations.
