# Company data, review access, and analysis design

Working design, October 31, 2026. This document proposes the next product data boundary. The tables, access controls, importer, and AI output below are not implemented by documenting them. Preserve the existing `/api/v1` contract while introducing reviewed product extensions.

Current decision: use Team 102's read-only Query API and application-owned
PostgreSQL storage. Shared PostgreSQL is deferred pending a future joint
agreement. A bounded manual source-report preservation/import slice is now
implemented; full evidence/incident mapping, product permissions, and AI output
remain future work. See [API import implementation](team102-api-import.md).

## Evidence and unresolved integration agreement

| Input | What it establishes | What it does not establish |
| --- | --- | --- |
| Current application source | Incident logs, deterministic assessments, submitted reviewer strings, review history, symmetric links, and saved non-final drafts exist. | Authenticated reviewers, evidence revisions, editable draft history, AI extraction, and isolated course sessions do not yet exist. |
| User's October 31 clarifications | Use the read-only Query API for now and defer the shared PostgreSQL proposal. Keep imported source evidence in our own database. Reviewer access will use application accounts with administrator-approved roles. | Team 102's complete response schema, source correction/version semantics, and cross-team testing contract still need confirmation. |
| Supplied `SEC450 Query API.md` | Team 102 documents authenticated read-only reports, clusters, events, and integrity checks at `https://seclog.redinbo.win`. Reports include source summaries, timelines, raw log lines, detection rules, upstream AI assessments, and integrity information. | It documents neither PostgreSQL access nor our incident API. A live response/schema and successful authenticated integration have not been verified by this design review. |
| Architecture drawing | Security tools, tickets, business inputs, and reviewers contribute to one traceable incident workflow. | It does not require every contributor to write the same application tables. |

The Query API is the selected source adapter. Choose one authoritative delivery
route for each source event; future alternative routes must share the same
source identity and deduplication rules. The guide's polling example is not a
configured background integration: incremental retrieval and source correction
semantics remain unconfirmed.

## Deferred shared PostgreSQL design

If the teams later agree on shared infrastructure, one PostgreSQL instance can
host separate schemas and roles. The source-owned ingestion/app-owned workflow
boundary below is a retained proposal, not the current implementation direction.

```text
Security / ticket / business sources
               |
      ingestion.source_events  (preserved source records)
               |
     importer validates and maps records
               |
       application evidence -> facts / advisory analysis
               |
 authenticated human review -> versioned disclosure drafts
```

Do not migrate existing public application tables into new schemas merely to match this diagram. Introduce managed migrations and a populated-data upgrade test before changing their location or structure.

| Role | Proposed access |
| --- | --- |
| Source writer | Insert through an agreed ingestion operation; read only its allowed source records if needed. No updates/deletes of accepted source records and no writes to incident states, reviewer decisions, drafts, or app role assignments. |
| Importer | Read the ingestion feed; write validated evidence, import receipts, and audit events through the application ingestion service. No authority to make human determinations. |
| Application runtime | Use application tables under a non-owner role with the permissions required by domain services. No role-management or migration privileges. |
| Migration/operator role | Own controlled schema changes and recovery tasks. Keep separate from source writer and normal runtime credentials. |

Acceptance criteria: source credentials cannot edit an application review or an accepted source record; importer retries preserve one logical delivery; an importer failure leaves no partial incident/evidence/receipt/audit mutation. "Append-only" here describes enforced application/runtime behavior, not resistance to a database owner or administrator.

## Minimum ingestion contract

Agree the envelope with Team 102 before creating shared tables. PostgreSQL types below are proposed. Keep original content as `text` or `bytea`; `jsonb` is useful for validated fields but does not preserve the original JSON byte representation.

| Field | Type / requirement | Purpose |
| --- | --- | --- |
| `id` | UUID, server assigned | Stable ingestion row identity. |
| `source_system` | Text, required | Producer identity assigned from trusted integration configuration. |
| `source_event_id` | Text, required | Stable producer event/report identity; report ID needs the source-system namespace. |
| `source_revision` | Text, required by the agreed version policy | Distinguish an explicit upstream correction from a retry. An initial fixed revision is acceptable if the source is immutable. |
| `event_type`, `schema_version` | Text, required | For example `incident_report` with an independently versioned payload contract. |
| `occurred_at`, `observed_at` | Timestamp with timezone, nullable | Source occurrence and observation times; absent values stay unknown. |
| `received_at` | Timestamp with timezone, required, server recorded | Reliable arrival time distinct from the source clock. |
| `original_content`, `content_sha256` | Text/bytes and digest, required | Preserved source representation and corruption/change check. A digest does not establish source authenticity. |
| `payload` | Validated `jsonb`, required | Structured source fields plus preserved vendor extensions in a bounded namespace. |
| `supersedes_event_id` | UUID, nullable | Explicit correction link; never silently overwrite the earlier record. |

Require uniqueness on `(source_system, source_event_id, source_revision)`. A retry with the same identity and content returns the prior receipt. The same identity with different content is a conflict requiring correction or quarantine; it must not silently replace evidence. Identical text from different source events remains distinct evidence.

Keep import receipts/checkpoints in application-owned storage: ingestion row ID, importer version, imported evidence ID, completed time, and failure category. The source row's arrival order and the import transaction determine progress; occurrence time alone can miss late-arriving events. Agree bounded backfill, ordering, retention, interruption recovery, and poison-record handling before operating a poller. Never advance a successful checkpoint past unrecorded failures without an explicit recoverable policy.

The Query API's `report_id`, `previous_cluster_id`, `collection_gaps`, `clock_anomaly`, `raw_lines`, and integrity result should retain their original meaning. Preserve upstream `ai_assessment` as attributed source evidence. An upstream integrity claim is not our independent verification, and an upstream AI recommendation is not a human materiality determination.

## Application record design

Implement these incrementally rather than creating all tables at once. Keep current incident IDs, shared lifecycle states, review rows, relation rows, and draft responses compatible.

| Record | Minimum product fields and behavior |
| --- | --- |
| Incident | Existing title/log/source/state/timestamps; add nullable discovery time, current evidence revision reference, operational owner, and a version for concurrent edits. Preserve the submitted log. |
| Evidence | Incident ID, ingestion/source reference, source type, original text/bytes, content digest, observed/received/submitted times, authenticated submitting actor, and optional correction link. Security, ticket, business-impact, and reviewer-supplied records use explicit types. |
| Evidence revision | Incident ID, monotonic revision, immutable evidence membership, recorded time, creating actor. Analysis and human reviews refer to an exact revision. |
| Structured fact | Field name, typed value, status (`supported`, `unknown`, `conflicting`), evidence/location references, and correction provenance. Candidate fields: affected systems/services, scope, incident timeline, availability/disruption, data categories, exfiltration claims, business/financial impact, and recovery status. |
| Analysis run | Incident/evidence revision, status, engine (`deterministic` or configured provider), model/rule/prompt/output-schema versions, bounded validated output, started/completed times, and failure category. Preserve each run. |
| Human review | Reuse review history; extend with authenticated actor ID, role/permission basis, evidence revision, effective determination time, recorded time, rationale, and explicit correction/supersession relationship. A display name remains separate from identity. |
| Draft revision | Reuse saved draft versions; add parent revision, generation/edit action, author ID, evidence/analysis/review references, template/model version, text, and edit reason. Editing creates a version; it cannot erase the generated original. |
| Audit event | Actor, server time, request ID, action, entity/revision references, and relevant before/after references; saved in the same transaction as the mutation. Avoid duplicating raw sensitive evidence into general logs. |

Separate `submitted_summary`, generated `incident_summary`, and reviewer-authored narrative. Every generated claim needs evidence references; a human edit records its author and basis. A headline/summary is useful context, but cannot replace original evidence or express unverified impact as fact.

For structured impact, preserve units, currency, measurement period, estimate/source, and whether a value is confirmed or estimated. Do not reduce missing values to zero. Occurrence, discovery, observation, receipt, determination, draft creation, and recorded filing times are distinct. Store timestamps with an explicit timezone and retain source clock warnings.

## Advisory analysis output and evaluation

The shared assessment response stays `{potentiallyMaterial, explanation, indicators}` and its deterministic course behavior stays repeatable. A product analysis envelope can add context without changing that public oracle:

```json
{
  "schemaVersion": "1",
  "incidentId": "synthetic-incident-id",
  "evidenceRevision": 3,
  "status": "completed",
  "incidentSummary": {
    "text": "A source report describes an interrupted service; impact is unconfirmed.",
    "evidenceRefs": [{"evidenceId": "synthetic-evidence-id", "location": "lines:1-4"}]
  },
  "facts": [],
  "unknowns": ["Duration and business impact have not been established."],
  "conflicts": [],
  "advisoryAssessment": {
    "potentiallyMaterial": true,
    "explanation": "Reported disruption needs human examination.",
    "indicators": ["service disruption"],
    "evidenceRefs": [{"evidenceId": "synthetic-evidence-id", "location": "lines:1-4"}]
  }
}
```

This is an illustrative schema shape, not an agreed API or a model answer. The implemented schema must validate reference membership/locations, output bounds, required fields, and per-status requirements. `unavailable` or `failed` analysis must not fabricate an assessment. Store engine/model metadata with the run; expose the parts needed for human review.

Detection severity (`low` through `critical`), AI-recommended severity, advisory `potentiallyMaterial`, workflow state, and a human decision are separate values. `UNDER_REVIEW` describes workflow, not an AI confidence score. Analysis cannot write `MATERIAL_CONFIRMED`, submit a review, approve a draft, or record a filing. New evidence marks earlier analysis/drafts as stale; it does not silently change a historical determination.

Before enabling a provider, evaluate held-out synthetic cases for extracted field correctness, unsupported claims, reference validity, missing/conflicting evidence, negation, prompt injection, input bounds, failure handling, latency, and cost. Use deterministic CI fixtures and a provider test double; keep paid/live evaluation separate. Precision/recall for materiality screening requires agreed human labels. Do not invent a confidence percentage without a calibrated basis.

## Verified review and editing access

The current application has no authentication or role enforcement; a submitted reviewer string does not verify a reviewer. Product data must be inaccessible through unauthenticated course endpoints, including reads and reset. Use a separate synthetic course store/service or enforce the same product access boundary on every adapter reaching protected records.

The selected approach is application accounts with administrator-approved roles. Use an established authentication/password library, immutable account IDs, modern password hashing, and revocable sessions. Do not build cryptography or infer permissions from a submitted name, email address, or email domain. Login alone does not grant reviewer authority. Record the administrator, grant/revocation time, and reason for membership changes. The next access-control slice must define initial administrator provisioning, account recovery, session expiry, and reviewer approval/revocation before enabling protected data.

| Permission group | Proposed authority |
| --- | --- |
| Contributor | Submit evidence and proposed factual corrections; view only permitted incidents. No human determinations or silent history edits. |
| Reviewer | Examine source evidence, record materiality decisions with rationale, and create/review draft revisions for assigned incidents. |
| Administrator | Manage memberships and integration configuration; administrative access does not automatically imply authority to determine materiality. |

Authorize every mutation server-side and record the authenticated actor. Use version checks for competing edits; reject stale updates with a reviewable conflict. Preserve revocation and previous decisions. The team still needs to decide whether legal approval is a separate permission and whether approval requires a second reviewer. Source-provided reviewer/approval information remains attributed evidence until an authorized user explicitly accepts it through the product workflow.

## Decisions needed for the next slices

1. Confirm the selected Query API's report-detail response with a synthetic example, stable source identity, and new/corrected report semantics. The manual preservation slice does not establish a complete automatic importer or incident mapping.
2. Keep shared PostgreSQL deferred. Any later change requires joint schema, role, retention, migration, and operational ownership agreement. Configure API credentials through local/deployment secrets, never chat or Git.
3. Implement the selected application-account approach with administrator-approved roles. Agree initial administrator provisioning, account recovery, reviewer approval/revocation, and any second-reviewer requirement before protected decisions or draft editing are exposed.
4. Agree course session routing, fixture/reset/cleanup policy, component/acceptance-criterion inventories, and timing/access notes. Generated Team 105 OpenAPI describes implemented wire shapes; it does not supply those missing pod agreements.
5. Deliver PostgreSQL validation/migrations first, then an evidence import with receipts/audit, protected human review, and the course interface in small separately reviewed checkpoints. Provider-backed AI follows the evidence/revision and output-schema foundation.
