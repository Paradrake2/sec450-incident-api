# Phase 2 incident API contract baseline

`phase2-v1.0.0.openapi.json` is a normalized JSON transcription of the OpenAPI
document in the supplied **Phase2_Gradescope.pdf**, Section 5.1, pages 18-28.
It retains OpenAPI 3.1.0, API version 1.0.0, the nine common operations, original
request/response schemas, and declared status codes. PDF page breaks, YAML
formatting, and typographic ligatures were normalized; no API semantics were
added. The original PDF, grades, and participant information are not included.

This is the submitted historical shared specification, not proof of the latest
pod agreement or of either deployed service's conformance. Keep it separate
from the application's generated OpenAPI and from the instructor's system/tester
control protocol. A changed common contract needs an explicit version and pod
agreement; implementation differences must not silently rewrite this baseline.

## Important original semantics

- Paths are relative to the `/api/v1` server base. The server hostname is a
  placeholder, not a verified target URL.
- Reset deletes test-created incidents **and restores shared fixture state**;
  it is intended only for the cross-team test deployment. The PDF promises a
  shared version-controlled synthetic dataset but does not provide it.
- Create `source` and review-request `rationale` are optional strings; explicit
  null is not allowed. Their corresponding response properties permit null.
- Create and review requests forbid additional properties. The related-incident
  request does not forbid them. Response objects also permit extra properties.
- Some declared errors have no response-body schema, including POST-create 415
  and health 503. The baseline does not invent a body schema for those statuses.
- The disclosure schema requires `final: false`. Its `draft` property is a
  string; the separate AC-07 criterion also requires a non-empty draft.

The PDF's page 10 validation diagram supplies supplemental ordering:
media-type 415, incident-existence 404, field-validation 400, then state 409.
That diagram also shows downstream failure 503, while Section 5.1 generally
declares unexpected failures as 500. This difference is recorded for
reconciliation rather than resolved by modifying the transcribed OpenAPI.
The state model permits escalation from NOT_MATERIAL to MATERIAL_CONFIRMED;
it depicts CLOSED but supplies no closing HTTP operation.

Session correlation, fixture contents and assessment labels, coverage
inventories, cleanup/concurrency rules, and actual testing URLs still need
explicit specification. A response matching this baseline establishes only
that observed response's structural conformance, not complete cross-team
behavior or disclosure quality.

`phase2_schema.py` loads the baseline, lists its operations, and checks selected
responses using JSON Schema types and date-time formats. It accepts only
internal JSON-pointer references and emits static diagnostics without payload
values. Status/body schema checks do not replace state-transition, side-effect,
fixture, and human-inspection acceptance criteria.
