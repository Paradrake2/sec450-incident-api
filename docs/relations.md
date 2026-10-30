# Phase 4: related-incident handling (REQ-06 / AC-06)

## Common contract

`POST /api/v1/incidents/{incidentId}/related` accepts the unchanged JSON shape
`{"relatedIncidentId":"<existing incident ID>"}` and returns HTTP 200 with
an ordinary `Incident` response. The `relatedIncidentIds` array is returned by
`POST /related`, `GET /incidents/{id}`, and `GET /incidents`. Neither incident's
`state`, `assessment`, nor `review` is modified when linking.

A link is **undirected**: if A is related to B, A appears in B's list and B
appears in A's list. A link is stored only once, with ordered IDs; the array of
related IDs in responses is sorted lexicographically for repeatability.

| Case | Result |
| --- | --- |
| Link two existing, non-CLOSED incidents | 200; both incidents expose relation |
| Duplicate link, including reversing source and target | 409 `DUPLICATE_RELATION` |
| Self-link | 409 `DUPLICATE_RELATION` |
| Missing source or target incident | 404 `INCIDENT_NOT_FOUND` |
| Missing / empty / non-string `relatedIncidentId` | 400 `INVALID_REQUEST` |
| Either incident is `CLOSED` | 409 `INVALID_STATE` |

The API never infers or automatically creates related incidents. No new
shared-contract endpoint is introduced.

## Storage and migration

The new `incident_relations` table has two foreign keys (`incident_a_id`,
`incident_b_id`) and a composite primary key to enforce uniqueness. The IDs are
sorted before insertion, and a check constraint enforces canonical order.
The insert is a single transaction: either the undirected relation exists or it
doesn't. A competing duplicate insert that hits the uniqueness constraint is
mapped to structured HTTP 409.

Previous releases stored relationships in per-incident JSON text arrays
(`incidents.related`), which are unsafe for concurrent updates and can become
asymmetric. At startup, an **idempotent backfill** imports valid historic links
from either incident's legacy JSON array, without modifying those arrays.
Duplicate, self-referential, and dangling historic references are skipped;
malformed JSON is logged without aborting startup. The old column remains for
non-destructive migration, but **only the relation table is authoritative** for
API responses and new links. Existing stored links remain visible after deploy.

`POST /api/v1/test/reset` removes relationship rows before deleting incidents
and review history. **Never call reset on a deployed system with valuable data**;
the demo service must use synthetic data only.

## Acceptance testing

`python -m pytest -q` runs the normal regression suite plus `test_related.py`,
which covers symmetric retrieval, preserving materiality state, duplicate and
reverse duplicate, self-links, invalid inputs, missing incidents, closed
incidents, multiple relationships, persistence after restart, legacy backfill,
and reset behavior. These tests run only against the isolated temporary SQLite
database in `test_api.py`, not against Render.

## Known limitations

- Relations are manually declared; no automatic log correlation or evidence
  reconciliation is attempted. Linking does not itself imply materiality.
- No unlink operation exists in the Phase 2 shared contract.
- The public demo API has no authentication. Treat all stored data as synthetic.
- Startup backfill assumes one worker, as in the current Render command.
  Production deployments should migrate legacy data once under migration
  locking rather than perform concurrent application-startup migrations.
- Database foreign-key enforcement in SQLite varies by connection settings;
  API handlers validate existence explicitly, and PostgreSQL enforces FK rules.
