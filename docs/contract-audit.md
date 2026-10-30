# Phase 4 — Stage 3 interface and contract conformance audit

**Prepared:** 2026-10-30  
**Repository:** `Paradrake2/sec450-incident-api`  
**Audited common contract:** Team 105's Phase 2 Section 5.1, OpenAPI 3.1.0, version 1.0.0, supplemented by AC-01–AC-08 and the Phase 4 Q3 rubric.  
**Important verification limit:** The separate Google Docs common specification cannot be read through this environment; independently compare that document and check pod agreement before claiming definitive contract conformance.

## Delivery / integration blocker: PR #4 merge target

GitHub PR #3 was merged to `main` as `53f3fa634e9d8a73a05f13d5b4199c4b0b98e6ea`. PR #4 was subsequently merged to **`phase4/related-incident-storage`**, not `main`, as `8f985d60936facdcdf0e892f333158b91206a187`. Consequently `main` has PR #3's relationship code but **does not contain** `disclosure_templates.py`, `disclosure_drafts`, `test_disclosure.py`, or the new draft-history API. PR #4 being marked “Merged” by GitHub does not mean it reached `main`.

**Recovery before this contract patch:** Open a new PR from `phase4/disclosure-drafts` (head) to `main` (base) if that branch still exists and its diff contains only the disclosure changes. Alternatively apply `recover_disclosure_to_main.patch` on the current `main` tree, run CI, and open a recovery PR. Do not force-push `main`. Confirm the actual Render deployment commit after merge.

## Common interface operation status

Nine operations are required, below relative to `/api/v1`. The audit is from repository source and local tests, **not a fresh deployed-endpoint suite**. “Expected” means as defined by Phase 2 Section 5.1 and acceptance criteria.

| Operation | REQ | Implementation on `main` | Success response | Error response / issue |
|---|---|---|---|---|
| `GET /health` | — | Yes, also root-level `/health` | `200 {"status":"ok"}` | `503` is documented but database readiness is not monitored; returns OK without probing database |
| `POST /test/reset` | REQ-08 | Partial | `204`, no body | **Deviation:** deletes all incidents, does not restore a specified common fixture state; 500 was not structured |
| `POST /incidents` | REQ-01/04/08 | Yes | `201 Incident` | `400` and `415` structured; default 500 was not structured; Swagger had undocumented 422 |
| `GET /incidents` | REQ-02 | Yes | `200 [Incident]` | Invalid state returns 400, generic server failures previously lacked JSON |
| `GET /incidents/{incidentId}` | REQ-02/04 | Yes | `200 Incident` | `404` structured; generated Swagger used `{incident_id}` rather than `{incidentId}` |
| `POST /incidents/{incidentId}/assessment` | REQ-03/04/08 | Yes | `200 Assessment` | `404`, `409` structured; default 500 not structured |
| `POST /incidents/{incidentId}/review` | REQ-04/05/08 | Yes | `200 Incident` | `400`, `404`, `409` structured; Swagger advertised 422 and generic string success schema |
| `POST /incidents/{incidentId}/related` | REQ-06/08 | Yes (PR #3 on main) | `200 Incident`; symmetric relationship | `400`, `404`, `409` structured; `LinkRequest` rejects unknown properties although common schema does not explicitly prohibit them |
| `POST /incidents/{incidentId}/disclosure` | REQ-05/07/08 | Phase 3 prototype on main; Phase 4 feature in PR #4 branch | `200 DisclosureDraft`, `final:false` | `404`, `409` structured; Phase 4 persisted draft feature **not yet on main** |

## Prepared Stage 3 corrections (preview only)

1. **Explicit API response models:** `IncidentResponse`, `AssessmentResponse`, `DisclosureResponse`, `ErrorResponse` (and optional history schemas) make `/openapi.json` and Swagger show the expected field types and documented error codes instead of generic `"string"` success responses. Runtime response shapes are preserved.
2. **Match declared path parameter spelling:** Swagger and OpenAPI use `{incidentId}`, as in the shared Phase 2 document; the actual URL structure and routing behavior are unchanged.
3. **Remove misleading generated 422 documentation:** the application uses a custom Pydantic validation handler returning structured `400`, not FastAPI's default 422. This change affects documentation only, not validation behavior.
4. **Structured internal errors:** unexpected unhandled exceptions are logged server-side and return `500` with `{"error":"INTERNAL_ERROR","message":"Unexpected server-side failure"}` without leaking exception details. Tests verify assessment state is unchanged on failure.
5. **Six new `test_contract.py` tests:** nine operation definitions, documented response models, end-to-end success flow, structured error matrix and rollback, simulated unexpected 500, and timestamp preservation.

## Known unresolved items requiring pod decision / later work

- **RESET-FIXTURES (REQ-08):** Phase 2 promises resetting to a shared fixture state. The supplied plan does not establish a verified fixture seed and `POST /test/reset` currently only deletes records. Decide exact fixture dataset and ownership with Team 102 before changing reset semantics; otherwise report it as a deviation.
- **LIFECYCLE-CLOSED (REQ-04):** `CLOSED` is an enum value but there is no agreed close operation. Do not unilaterally add a new common endpoint; either specify closure in the pod contract or update the model with an explicit limitation.
- **REQUEST-EXTRA-FIELDS (REQ-08):** Link request uses `extra="forbid"`, while Phase 2 doesn't set `additionalProperties:false` for that request. Agree whether extra fields should be accepted/ignored or prohibited before changing behavior.
- **AUTHORIZATION (REQ-05):** The shared interface has no authentication, so a user-supplied reviewer identifier is not verification of an authorized human. Continue restricting deployment to synthetic test data and disclose this limitation.
- **SHARED-SPEC:** Reconcile with the instructor's live common OpenAPI Google Doc, not only the Phase 2 copy. Any actual change must include version, date, pod agreement, and if required instructor sign-off. The preview here targets formatting and existing error semantics, not an expansion of common scope.

## Validation and instructions

The Stage 3 candidate was tested locally against an isolated temporary SQLite database using Python 3.13:

```bash
python -m pytest -q
python -m compileall -q main.py contract_schemas.py test_contract.py
```

**Result:** `69 passed, 0 failed, 2 existing HTTPX deprecation warnings`. Six new contract-focused tests were added to the 63 existing tests. Ruff was not installed in this local environment; obtain the repo's `Ruff` result through GitHub Actions after opening the PR. No public Render database was reset or mutated.

Before submission, rerun the full suite against the exact deployed commit **on a dedicated synthetic test environment** with agreed fixtures, and obtain cross-team evidence from Team 102. Do not run the local tests unchanged against shared Render: they invoke the destructive reset operation.
