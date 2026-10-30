# Phase 4 — Disclosure generation and draft history (REQ-07 / AC-07)

## Scope and the shared API

The existing `POST /api/v1/incidents/{incidentId}/disclosure` keeps its original
request and response format, returning exactly `incidentId`, `draft`,
`generatedAt`, and `final: false`. Successful requests return HTTP 200.
There is **no final disclosure or SEC filing feature**. All test cases use
synthetic data. The system never authenticates reviewers and cannot assert
that a real legal materiality determination has been made.

The endpoint requires both `state == MATERIAL_CONFIRMED` and a stored latest
human `MATERIAL` decision. Any other current state, missing review, or
contradictory review results in structured HTTP 409 `INVALID_STATE`, with no
draft stored. An unknown ID returns structured HTTP 404. An incident flagged
by automated triage alone does *not* qualify; a negative triage assessment
followed by a human `MATERIAL` review *does* qualify.

## Draft template and limitations

The `disclosure_templates.py` pure function produces a non-final document
containing:

1. Reported incident nature (submitted log, explicitly unverified)
2. Scope and affected systems **unknown until independently confirmed**
3. Submitted occurrence timestamp and recorded intake timestamp, clearly
   distinguished from *discovery* or *materiality determination* dates
4. Latest human reviewer decision and rationale, marked as user-supplied and
   not proof of identity; financial and operational impact marked unverified
5. Outstanding factual verification and legal signoff steps

Arbitrary incident logs and reviewer rationale are quoted and prefixed so they
cannot masquerade as the template's own statements. Log excerpts are limited
to 2,000 characters; any truncation is clearly disclosed. Titles and sources
are converted to single lines. The template **does not infer missing facts**,
predict financial impact, calculate a filing deadline, or assert actual legal
materiality. A draft is a *starting point*, not a compliant SEC filing.

## Persistence and versioning

Every successful draft generation inserts a new row into `disclosure_drafts`
with a unique UUID, incident foreign key, non-final draft text, and generation
timestamp. Records persist across service restarts. The table is created on
startup by SQLAlchemy `create_all`; it does **not** modify existing tables or
delete existing incidents, human reviews, or incident relations.

The previous implementation returned draft text without storing it, so
**previously generated drafts cannot be recovered**. There is no backfill.
The `POST /api/v1/test/reset` operation deletes draft records before their
parent incidents as part of the synthetic-data test reset.

Optional extension (not part of the agreed common pod API):

```
GET /api/v1/incidents/{incidentId}/disclosures
```

It returns an oldest-first array of saved draft versions, each adding an `id`
to the original four-field response. A known incident with no drafts returns
`[]` (200); an unknown incident returns structured 404. Discuss the extension
with podmates before making it a shared contract requirement.

## Testing and deployment

`python -m pytest -q` includes `test_disclosure.py` AC-07 tests for positive and
negative eligibility, escalation after review, response shape, fact handling,
log truncation, durable history, multiple versions, missing IDs, and reset.
Testing is isolated to temporary local SQLite; it does not reset the deployed
Render database. No new third-party dependencies are needed.

Deployment creates the new table automatically under the current single-worker
configuration. As with the other Phase 4 schema additions, use managed database
migrations and reviewer authentication before considering real-world use.
