# Interface and contract conformance audit

The reference contract is the Phase 2 interface specification, Section 5.1:
OpenAPI 3.1.0, API version 1.0.0, with AC-01 through AC-08. Its normalized
baseline is preserved in [contracts/](../contracts/README.md). The system/tester
control protocol is a separate interface.

Run `python -m contract_audit --format markdown` for observed local results.
See [contract reconciliation](phase2-contract-reconciliation.md) for the
baseline's ambiguities and remaining agreement requirements. This document
separates source implementation from evidence about a deployed release.

## Common interface operation status

The nine common operations below are relative to `/api/v1`. These entries
describe source behavior; they are not a new deployed-endpoint test report.
In the composed delivery, isolated course requests use `/system/api/v1`.

| Operation | Requirements | Source behavior | Response / qualification |
| --- | --- | --- | --- |
| `GET /health` | — | Also available at root `/health` | `200 {"status":"ok"}`; health does not probe database readiness, and the declared `503` path is not demonstrated by normal checks. |
| `POST /test/reset` | REQ-08 | Clears the selected store | `204`; isolated course sessions also restore candidate fixtures. Shared fixture agreement remains incomplete. |
| `POST /incidents` | REQ-01/04/08 | Creates a RECEIVED incident | `201 Incident`; structured `400`, `415`, `500`. |
| `GET /incidents` | REQ-02 | Lists records with optional state filter | `200 [Incident]`; structured invalid-state `400` and unexpected `500`. |
| `GET /incidents/{incidentId}` | REQ-02/04 | Retrieves a stored incident | `200 Incident`; structured `404`, `500`. |
| `POST /incidents/{incidentId}/assessment` | REQ-03/04/08 | Deterministic advisory screening | `200 Assessment`; structured `404`, `409`, `500`. |
| `POST /incidents/{incidentId}/review` | REQ-04/05/08 | Records a decision and preserves history | `200 Incident`; structured `400`, `404`, `409`, `415`, `500`. Submitted reviewer text is not an authenticated identity. |
| `POST /incidents/{incidentId}/related` | REQ-06/08 | Stores a normalized symmetric link | `200 Incident`; structured `400`, `404`, `409`, `415`, `500`. Additional properties are accepted and ignored. |
| `POST /incidents/{incidentId}/disclosure` | REQ-05/07/08 | Preserves a non-final draft after a stored MATERIAL review | `200 DisclosureDraft`, `final:false`; structured `404`, `409`, `500`. |

## Contract alignment

- Explicit response models describe success fields and structured errors in
  generated OpenAPI. Validation failures return `400`; misleading generated
  `422` responses are removed from the schema.
- Request fields `source` and `rationale` may be omitted but reject explicit
  null. Corresponding response fields remain nullable.
- Incident timestamps require RFC 3339 strings with timezones and normalize to
  UTC. Numeric epochs, date-only strings and naive timestamps are rejected.
- Related requests accept extra properties without storing them; create and
  review requests reject unknown properties.
- Review and related operations resolve the primary incident before validating
  decoded body fields. Media checks precede lookup. Malformed JSON is a
  separate envelope-syntax `400` before dependency resolution.
- Unexpected exceptions are logged server-side and return a generic structured
  `500` without internal exception details. Local failure-injection tests also
  check transaction state preservation.

The local audit has 44 observed checks. The request-alignment fixes resolve
its six earlier request differences. A passing audit does not establish every
possible failure response, shared fixture restoration, classification quality,
or bidirectional deployed conformance.

## Remaining specification and product limits

- **Shared reset fixtures (REQ-08):** agree the versioned dataset, ownership and
  restoration checks. Isolated reset restores the project's candidate fixtures;
  the normal product reset deletes normal records.
- **CLOSED lifecycle (REQ-04):** the enum contains CLOSED without a shared close
  operation. Document the limitation rather than invent a common endpoint.
- **Reviewer authorization (REQ-05):** reviewer identifiers are request data,
  not proof of an authorized human. Use synthetic data and disclose this limit.
- **Assessment fixtures (REQ-03):** agree expected positive/negative labels;
  deterministic local examples are not a definitive cross-team oracle.
- **Shared protocol details:** record current interface scope/version, fixture
  labels, session routing, coverage inventories, cleanup and timing limits.

## Reproduce validation

Use the disposable-store regression harness:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m contract_audit --format markdown
```

The local audit accepts no remote target. Do not redirect destructive local
reset tests at a normal or shared application database. Use the configured
isolated system and [release runbook](phase4-delivery.md) for HTTP acceptance,
recording the exact release and actual results separately from local checks.
