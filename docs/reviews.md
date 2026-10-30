# Phase 4 — Human materiality review and override (REQ-05 / AC-05)

## Behavior

The existing `POST /api/v1/incidents/{incidentId}/review` endpoint accepts the same
`ReviewRequest`: `decision` is `MATERIAL` or `NOT_MATERIAL`, `reviewer` is a
nonblank string, and `rationale` is optional. Its successful response is the
existing `Incident` JSON shape, including `review` as the **most recent** decision.
A human review supersedes the automated advisory assessment without deleting
or modifying that assessment.

Allowed transitions:

| Current state | New decision | New state |
| --- | --- | --- |
| `POTENTIALLY_MATERIAL` | `MATERIAL` | `MATERIAL_CONFIRMED` |
| `POTENTIALLY_MATERIAL` | `NOT_MATERIAL` | `NOT_MATERIAL` |
| `UNDER_REVIEW` | `MATERIAL` | `MATERIAL_CONFIRMED` |
| `UNDER_REVIEW` | `NOT_MATERIAL` | `NOT_MATERIAL` |
| `NOT_MATERIAL` | `MATERIAL` | `MATERIAL_CONFIRMED` |

All other review operations return structured HTTP 409, including a review
before assessment, a downgrade after confirmation, a repeated unchanged
review, or any review after closure. Invalid request fields return structured
HTTP 400, and an unknown incident returns 404 when the body is otherwise valid.
`CLOSED` cannot currently be entered through the common API; that remains a
separate REQ-04 gap.

## Review history and compatibility

A separate `incident_reviews` table records each successful human decision
with a generated ID, incident ID, decision, reviewer, rationale, and review
timestamp. The incident's legacy `review` JSON column stays as the latest
snapshot to preserve existing clients. The history insert and state update
commit in the **same SQL transaction**.

An **optional extension** is provided:

```
GET /api/v1/incidents/{incidentId}/reviews
```

It returns an oldest-first list of review entries with `id`, `decision`,
`reviewer`, `rationale`, and `reviewedAt`, or 404 if the incident does not exist.
It does **not** replace or change the pod's existing common-interface endpoint.
The team should document the extension under contract changes (Phase 4 Q3.2)
and check with the pod before requiring other teams to implement it.

On startup, an idempotent backfill copies the latest available Phase 3 review
snapshot for each preexisting incident that has no history row. Past decisions
that were already overwritten **cannot be reconstructed**. The schema is
added by SQLAlchemy's existing `create_all`; no existing `incidents` columns
need to change. Test reset deletes review rows and incidents together.

## Validation

Run `python -m pytest -q`. Tests cover both human overrides, escalation after
a `NOT_MATERIAL` decision, append-only history, legacy backfill, reset,
nonexistent IDs, blank/invalid inputs, illegal transitions, and persistence
across application lifespans.

## Security and operational limitations

This remains an **unauthenticated synthetic-data prototype** as specified by
the pod contract. The `reviewer` field is user-supplied, not proof of identity;
reviewer authorization, access controls and tamper-evident audit trails remain
unimplemented. Do not submit real incident evidence or claim this system
provides authenticated legal approval. The backfill assumes one service
startup worker; for a production multi-worker deployment, move migrations
and deduplication to a separately managed schema migration process.
