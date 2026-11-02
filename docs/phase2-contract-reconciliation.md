# Phase 2 contract reconciliation

Phase 2 interface documentation, Section 5.1, defines OpenAPI 3.1.0, API
version 1.0.0, with nine operations under `/api/v1`. This historical baseline
supports implementation and tests. It does not establish which version each
service deploys or whether later fixture and assessment decisions are agreed.
Design requirements, reported results and directly observed behavior remain
separate evidence.

## Operation and acceptance-criterion inventory

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
is automated; disclosure content quality requires inspection. Health aliases
and review/disclosure history routes are extensions, not extra common operations.

## Differences and unresolved choices

| Topic | Baseline and implementation | Required treatment |
| --- | --- | --- |
| Reset fixtures | Section 5.1 requires removal of test-created data and restoration of shared fixtures. Normal reset deletes records; isolated course reset restores candidate fixtures. | Agree a shared versioned fixture set and restoration checks. An empty database or candidate dataset alone does not prove shared restoration. |
| Optional request fields | Request `source` and review `rationale` may be omitted but allow strings, not explicit null. Response schemas separately allow null. | Corrected: explicit null returns `400`; omission and nullable responses remain supported. |
| Request timestamps | Create `occurredAt` is a date-time string. RFC 3339 requires a timezone. | Corrected: require an RFC 3339 string with timezone while preserving UTC normalization and legacy stored timestamps. |
| Related-request extras | The inline request schema does not forbid additional properties. | Corrected: accept and ignore extras without persisting unrelated data or changing link meaning. |
| Validation precedence | The Phase 2 validation model orders media, existence, fields, then state. Phase 3 documentation also describes existence before field validation. | Corrected for decoded fields using selected-session dependencies. Malformed JSON remains an explicit envelope-syntax `400` before dependencies. |
| Positive assessment example | Phase 2 illustrates an employee-database access log with `potentiallyMaterial: true`; the heuristic returns false for that example. | Keep it as a diagnostic mismatch, separate from approved fixture labels. Resolve the example/rule discrepancy and agree positive/negative labels. |
| Internal-error status | The Phase 2 validation model shows downstream `503`; Section 5.1 declares application `500` and health `503`. | Use operation-specific OpenAPI as the response baseline and record the model ambiguity. Do not claim database-readiness monitoring that does not exist. |
| CLOSED | CLOSED is in the enum, but Section 5.1 defines no operation entering it. | Document the unreachable state rather than inventing a shared close endpoint. |
| Shared decisions | Phase 2 records agreement while also identifying rules, transitions and fixtures requiring further specification. | Distinguish baseline agreement from unverified later decisions and missing artifacts. |

Phase 2 testing documentation specifies synthetic, version-controlled data,
ordered dependent operations, no required authentication for the test deployment
and no live SEC dependency. Examples are not the promised fixture package.
Session routing, cleanup/concurrency, coverage inventories and measured request
limits require separate specification alongside the system/tester protocol.

## Reproducible local evidence

```powershell
.\.venv\Scripts\python.exe -m contract_audit --format markdown
```

The audit exercises real routes against an isolated disposable store and emits
all nine operations with observed success/error checks and `pass`, `fail`,
`partial` or `not_tested` results. It accepts no remote target URL and must not
reset a normal or shared application store. Missing checks, unexercised server
errors and shared fixture restoration remain explicitly unverified.

The initial 44-check audit found six request differences: explicit-null
source/rationale, a timezone-free timestamp, related-request extras, and
lookup-before-field-validation on review/related. Request-alignment fixes resolve
those observed cases. The historical assessment example remains a separate
classification mismatch. Decoded-field precedence does not mean malformed JSON
receives `404` instead of a syntax error.

The configured delivery now provides isolated system sessions, a bounded HTTP
gateway and project checks. See [the release runbook](phase4-delivery.md) and
[AC/test mapping](phase4-test-mapping.md). Those mechanisms do not themselves
establish shared fixture agreement, measured coverage or bidirectional deployed
conformance; record actual versioned runs and remaining differences separately.

## Reference documentation

- Phase 2 interface and acceptance documentation: contract, criteria, examples,
  reset semantics and unresolved integration risks.
- Phase 2 design/testing documentation: isolated-reset intent and outstanding
  assessment/fixture agreement.
- Phase 3 implementation documentation: historical deployment/test claims,
  validation-order decisions and limitations.
- Phase 1 requirements documentation: broader evidence/review goals, separate
  from the testing wire contract.

The preserved machine-readable baseline is in
[contracts/](../contracts/README.md). Implementation plans do not silently
amend the shared interface or establish verified deployment behavior.
