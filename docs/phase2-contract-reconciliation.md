# Phase 2 contract reconciliation

Reviewed October 31, 2026 against the supplied phase submissions and current source.

The original Section 5.1 contract is now recovered: OpenAPI 3.1.0, application
version 1.0.0, with nine operations under `/api/v1`. This is a concrete historical
baseline for implementation and tests. It does not establish that both teams
currently deploy that version or that later fixture and assessment decisions
were agreed. Assignment prompts, submitted design answers, reported results,
and directly observed behavior are separate evidence.

## Recovered operation and acceptance-criterion inventory

| Method and path | Acceptance criteria exercised |
| --- | --- |
| `GET /api/v1/health` | Reachability before functional checks |
| `POST /api/v1/test/reset` | Repeatable suite preparation; AC-08 error contract |
| `POST /api/v1/incidents` | AC-01 submission/storage; AC-04 initial state; AC-08 |
| `GET /api/v1/incidents` | AC-02 retrieval/filtering; AC-04 observable state; AC-08 |
| `GET /api/v1/incidents/{incidentId}` | AC-01/02 stored values; AC-04 state; AC-08 |
| `POST /api/v1/incidents/{incidentId}/assessment` | AC-03 advisory assessment; AC-04 transitions; AC-08 |
| `POST /api/v1/incidents/{incidentId}/review` | AC-05 human precedence; AC-04 transitions; AC-08 |
| `POST /api/v1/incidents/{incidentId}/related` | AC-06 relationships without materiality changes; AC-08 |
| `POST /api/v1/incidents/{incidentId}/disclosure` | AC-07 non-final draft; AC-05 human prerequisite; AC-08 |

AC-01 through AC-06 and AC-08 are black-box API checks. AC-07 response structure
is automated; disclosure content quality still requires inspection. Health
aliases and review/disclosure history routes are extensions, not extra common
operations. Sources: `Phase2_Gradescope.pdf`, pp.13-17 and 18-32.

## Differences and unresolved choices

| Topic | Recovered baseline and current difference | Required treatment |
| --- | --- | --- |
| Reset fixtures | Section 5.1 promises removal of test-created data **and** restoration of shared fixtures. Current reset deletes records without seeding them. | Report partial behavior; agree a versioned fixture set and restoration check. An empty database alone does not prove fixture restoration. |
| Optional request fields | Request `source` (p.24) and review `rationale` (p.26) may be omitted but their request schemas allow strings, not explicit null. Response schemas separately allow null. | Corrected: explicit null returns 400; omission and nullable responses remain supported. |
| Request timestamps | Create `occurredAt` is a date-time formatted string (p.24). RFC 3339 date-time requires a timezone. | Corrected: require an RFC 3339 string with timezone, preserving UTC normalization and legacy stored timestamps. |
| Related-request extras | Inline request schema (pp.22-23) has no `additionalProperties: false`; JSON Schema therefore permits extra properties. | Corrected: accept and ignore extra properties without persisting them or changing link meaning. |
| Validation precedence | The Phase 2 validation diagram (p.10) checks media, existence, fields, then state. Phase 3 p.13 reports agreement on 404 before 400. | Corrected for decoded-body fields through selected-session dependencies. Malformed JSON remains an envelope-syntax 400 before dependencies; this boundary is explicit and tested. |
| Positive assessment example | Phase 2 pp.29-30 illustrates an employee-database access log with `potentiallyMaterial: true`. The current heuristic returns false for that exact log. | Retain this as a diagnostic example, separate from approved fixture labels. Resolve the example/rule mismatch and obtain the shared positive/negative labels. |
| Internal-error status | The Phase 2 diagram p.10 shows downstream 503; Section 5.1 YAML documents application 500 and health 503. Phase 3 p.13 describes 503 as health-only. | Use the operation-specific YAML as the explicit response baseline and record the model ambiguity; do not claim a database-readiness check that is not implemented. |
| CLOSED | CLOSED is an enum value, but Section 5.1 has no operation that reaches it. Phase 3 pp.13,15 acknowledges this. | Document the unreachable state; do not invent a shared close endpoint. |
| Shared decisions | Phase 2 p.5 records September meetings and no disagreement, while pp.40-41 still identify rules, transitions, and fixtures as needing agreement. | Preserve the historical agreement claim without treating missing artifacts or later agreement as verified. |

Phase 2 pp.16,32 specifies synthetic, version-controlled data, ordered dependent
operations, no required authentication for the test deployment, and no live SEC
dependency. It supplies examples, not the promised fixture package. It does not
define course session routing, cleanup/concurrency, coverage inventories, or
measured request/time limits. Those remain separate from the instructor's
system/tester control protocol.

## Reproducible local evidence

```powershell
.\.venv\Scripts\python.exe -m contract_audit --format markdown
```

The local audit exercises real application routes against an isolated disposable
store and emits all nine operations with observed success/error checks and
`pass`, `fail`, `partial`, or `not_tested` results. It accepts no remote target
URL and must not reset the shared Render service. Observed cases are bounded
evidence: absent checks, unexpected-server-error paths not exercised, and shared
fixture restoration remain explicitly unverified. The illustrative assessment
example is reported separately from agreed classification coverage.

The first PostgreSQL observation produced 44 checks: 38 passed and six failed.
The failures cover explicit null source/rationale, a timezone-free timestamp,
related-request extra properties, and lookup-before-body-validation on review
and related operations. Reset fixture restoration remains partial; 500/503
checks not exercised remain unverified. The historical positive assessment
example is a separate mismatch, outside those six failures. These findings
are observed differences, not failing tests of the reporting tool itself.

The request-alignment checkpoint corrects those six observed cases; the same
44-check HTTP audit now has no failures. Shared fixture restoration,
unexercised 500/503 paths, deployed validation, and the historical classification
example remain separate. The existence-before-validation guarantee applies to
decoded body fields; FastAPI rejects malformed JSON before resolving dependencies.

This is neither complete T-01/T-02 delivery nor deployed cross-team conformance.
The request fixes are implemented; next add isolated system sessions,
real target adapters/cases, fixture and
coverage agreements, and bidirectional deployed runs with saved versioned reports.

## Source record

- `Phase2_Gradescope.pdf`: pp.5,10,13-17,18-32,40-41; recovered contract, criteria,
  examples, reset semantics, and unresolved pod risks.
- `Phase2_7-9.pdf`: pp.3-7; team design and testing plans, isolated-reset intent,
  and explicitly unresolved assessment/fixture agreement.
- `Phase3.pdf`: pp.3-9,13-15; historical contract/deployment/test claims,
  validation-order agreement, and limitations. Reported success is not a new
  observation of the current deployment.
- `Phase1.pdf`: pp.5-6; broader evidence/review vision, not a testing wire contract.

The PDFs were supplied locally and are not committed. Current user decisions
take precedence over historical implementation plans, including the earlier
rule-only runtime-AI scope.
