# Phase 2 incident API contract baseline

[`phase2-v1.0.0.openapi.json`](phase2-v1.0.0.openapi.json) is a normalized JSON
transcription of the OpenAPI specification in Phase 2 interface documentation,
Section 5.1. It retains OpenAPI 3.1.0, API version 1.0.0, the nine common
operations, original request/response schemas, and declared status codes.
Formatting was normalized without adding API semantics.

This historical shared specification is a reference baseline, not proof of the
latest cross-team agreement or either deployed service's conformance. Keep it
separate from the application's generated OpenAPI and the system/tester control
protocol. A changed common contract needs an explicit version and cross-team
agreement; implementation differences must not silently rewrite the baseline.

## Important original semantics

- Paths are relative to the `/api/v1` server base. The baseline server hostname
  is a placeholder, not a verified target URL.
- Reset deletes test-created incidents **and restores shared fixture state**.
  Phase 2 specifies a version-controlled synthetic dataset but does not define
  its contents in the wire contract.
- Create `source` and review-request `rationale` are optional strings; explicit
  null is not allowed. Their corresponding response properties permit null.
- Create and review requests forbid additional properties. The related-incident
  request does not forbid them. Response objects also permit extra properties.
- Some declared errors have no response-body schema, including POST-create 415
  and health 503. The baseline does not invent schemas for those statuses.
- The disclosure schema requires `final: false`. Its `draft` property is a
  string; AC-07 separately requires a non-empty draft.

The Phase 2 validation model specifies media-type 415, incident-existence 404,
field-validation 400, then state 409. It also shows downstream failure 503,
while Section 5.1 generally declares unexpected application failures as 500.
This ambiguity is recorded for reconciliation without changing the OpenAPI.
The state model permits escalation from NOT_MATERIAL to MATERIAL_CONFIRMED;
it depicts CLOSED but defines no closing HTTP operation.

Session correlation, shared fixture contents and assessment labels, coverage
inventories, cleanup/concurrency rules, and testing URLs require explicit
agreement. A matching response establishes only that observed response's
structural conformance, not complete cross-team behavior or disclosure quality.

[`phase2_schema.py`](../phase2_schema.py) loads the baseline, lists its operations,
and checks selected responses using JSON Schema types and date-time formats.
It accepts only internal JSON-pointer references and emits static diagnostics
without payload values. Schema checks do not replace state-transition,
side-effect, fixture, and human-inspection acceptance criteria.
