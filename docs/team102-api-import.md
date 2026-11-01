# Team 102 API report preservation

The selected integration reads Team 102's Query API and keeps source evidence
in our application-owned PostgreSQL database. Shared database access is
deferred. This operator-only slice preserves reports; it does not expose a new
public endpoint or automatically create incidents or human decisions.

## Scope and contract limits

The supplied Query API guide documents authenticated, read-only access to
`https://seclog.redinbo.win/v1/reports/{id}`. It describes `report_id` and a
report `body`, but does not provide a complete detail-response example. The
current isolated parser requires a JSON object with a matching numeric
`report_id` and object `body`; unknown fields are preserved. One authenticated
live detail sample matched this minimal envelope. The complete body contract,
identity/correction semantics, and production import behavior remain unverified.
The importer rejects unsupported shapes rather than guessing.

The teammate expected their report GET to match our `IncidentResponse`. The
read-only probe checked one live response without applying the importer's
envelope assumption: its root had `report_id` and `body`, and neither the root
nor inspected body matched our incident schema. No incident fields were inferred.

The importer fetches one explicit ID at a time. There is no background poller,
list cursor, or completeness guarantee. The guide's 24-hour default/31-day
window/1,000-item list limit does not define a reliable incremental protocol
for new or corrected reports. That agreement is needed before automatic sync.

Upstream summaries, integrity results, and AI assessments remain attributed
source evidence. They cannot set an incident state, submit a human review,
approve a draft, or establish independently verified facts. Mapping into
application incidents/evidence revisions follows response-schema confirmation
and the protected product boundary.

## Read-only schema probe

Install the development dependencies, including JSON Schema and RFC 3339
date-time validation support:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

When Team 102 provides access, put `SEC450_KEY` in the project's ignored `.env`
file or local environment. Do not paste the key into chat or commit it. No
database URL or migration is needed for the probe:

```powershell
.\.venv\Scripts\python.exe -m team102_probe
```

By default, the probe performs `GET /v1/reports?limit=1`, selects one supported
identifier from the response, then performs one detail GET. This makes at most
two requests and does not paginate or retry. An empty collection produces a
`no_sample` result; the guide's default list window may simply contain no
reports. To bypass discovery and make one detail request, provide a known
numeric or UUID-style identifier:

```powershell
.\.venv\Scripts\python.exe -m team102_probe --report-id 123
```

The JSON summary compares the response against the repository's
`contract_schemas.IncidentResponse` JSON Schema, with strict JSON types and
date-time format checks. It checks the root and a bounded set of known wrappers,
so a direct match is distinguished from an incident nested inside an envelope.
A separate guide comparison checks only a numeric `report_id` and object
`body`; it does not validate the report body's contents. If both formats are
found, the classification is `ambiguous`, and both match paths are reported.

Output contains known field names/types, missing required fields, sanitized
validation paths and keywords, and request counts. It omits report values,
unknown field names, API keys, and raw error bodies. The probe does not save or
import reports, contact a database, or change incident state. A matching sample
supports response-shape compatibility for that sample only; it does not prove
the entire API, endpoint behavior, error responses, or common contract conforms.

| Exit code | Meaning |
| --- | --- |
| `0` | An incident schema match was found, including a nested or ambiguous match; inspect the classification and paths. |
| `1` | Configuration, identifier, or HTTP/response error prevented the comparison. |
| `2` | Guide-envelope match only, or an unsupported response shape. |
| `3` | No report sample was available. |

Synthetic tests cover the probe's validation behavior. On October 31, 2026,
one authenticated list GET and one detail GET returned a `guide_report` match,
with no incident-schema matches. Known body fields included summary, timeline,
ai_assessment, matched_rule, requester, data_destination, raw_lines, integrity,
collection_gaps, and previous_cluster_id. Only redacted diagnostics were emitted;
the payload was not saved or imported. Source-to-incident field mapping remains
deliberate follow-up work; this sample is not full contract conformance evidence.

## Explicit database migration

Install dependencies and select the application database through the ignored
`.env` file or `DATABASE_URL` environment variable. Then run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m migration_tools upgrade
```

The CLI requires a configured URL; it has no SQLite fallback. It supplies one
explicit connection to Alembic. Revision `0001_baseline` freezes the existing
four incident/review/relation/draft tables. A complete legacy schema is adopted
only after its columns, types, nullability, constraints, and indexes match that
baseline. Partial/drifted schemas or report tables without migration history
are refused before stamping. No incident rows are overwritten by adoption.

Revision `0002_report_imports` adds private source snapshots and observations.
These models have separate metadata, so legacy application startup cannot
silently create them. Import checks the current migration revision before
contacting the source. Normal application startup still uses its existing
schema creation/backfill behavior for the four legacy tables; moving that
entire lifecycle to migrations remains separate work.

Fresh upgrade, populated legacy adoption, repeated upgrade, schema-drift
rejection, transactional rollback, and report-table downgrade are tested on
disposable stores. A programmatic downgrade to `0001_baseline` drops imported
report records and preserves legacy records. No downgrade CLI or automatic
production migration is provided. Backups and deployment migration review
remain operator responsibilities for any shared/deployed database.

## Synthetic local demonstration

The committed example is illustrative and synthetic, not an actual Team 102
response or confirmation of its schema:

```powershell
.\.venv\Scripts\python.exe -m team102_import --report-id 17 --fixture examples/team102-report.synthetic.json
```

No API key or network request is used in fixture mode. Fixture records use a
separate source namespace and explicit retrieval method, so they cannot be
mistaken for fetched API evidence. Running the same command again reuses the
content snapshot and records another observation.

For an explicitly requested real report, configure `SEC450_KEY` through ignored
local secrets, confirm the parser/schema, and run:

```powershell
.\.venv\Scripts\python.exe -m team102_import --report-id 123
```

The importer prints only receipt IDs, source report ID, retrieval method, and
whether new content was retained. It does not print source payloads, API keys,
database credentials, or raw upstream errors. No automatic request retries
are performed, and a 429 provides bounded `Retry-After` metadata for a later
operator-controlled retry. Product authentication/access controls are still
pending; use synthetic data for this prototype until protected operation is
established. The preservation milestone used synthetic fixtures; the later
read-only probe fetched one live sample without importing or saving its payload.

## Evidence and retry behavior

- `source_report_snapshots` stores the complete first response bytes for each
  distinct semantic report, its raw/canonical SHA-256 digests, source identity,
  and recorded import time. Parsing does not replace the original bytes.
- Identity includes source namespace, report ID, and canonical JSON content.
  Key order/JSON layout changes do not create a duplicate snapshot; meaningful
  content changes under the same report ID append another snapshot.
- `source_report_observations` records each fetch/import, its UTC fetched time,
  source URL, safe upstream request ID when available, retrieval method, and
  raw response digest. Later equivalent wire formatting is fingerprinted; its
  duplicate bytes are not retained again. The original first representation
  remains available on the snapshot.
- No upstream version/current pointer is invented. Observations preserve what
  was seen without claiming which distinct source content is authoritative.
- A database uniqueness constraint and conflict-safe insert deduplicate
  concurrent imports. Snapshot and observation writes share one transaction;
  failure returns no success receipt and leaves no partial new snapshot.
- Digests detect content differences; they do not prove source authenticity or
  make the database resistant to an administrator.

## HTTP boundary

The client has one fixed HTTPS origin, sends the API key only as a Bearer
header, verifies TLS, ignores environment proxies, and never follows redirects.
It performs only GET requests to the fixed report-list and report-detail paths.
Shape-neutral reads support the probe, while imports retain strict provisional
report-envelope validation. Explicit HTTPX operation timeouts
and a 2 MiB decoded body limit bound individual waits and accepted payloads;
the timeout is not a measured end-to-end service guarantee. All response-size,
JSON/schema, authentication, rate-limit, upstream, timeout, and transport
failures have sanitized classifications. Error response bodies are not read.

HTTP and storage tests use synthetic HTTPX transports and disposable databases.
They exercise redirects, missing access, rate limits, malformed/truncated data,
streamed size limits, duplicate source IDs/content, changed content, concurrent
inserts, and transaction failures. The course tester's controls and target
adapters remain independent of this report-feed client.

Implementation references: [HTTPX clients](https://www.python-httpx.org/advanced/clients/),
[Alembic shared connections](https://alembic.sqlalchemy.org/en/latest/cookbook.html#sharing-a-connection-across-one-or-more-programmatic-migration-commands),
and [SQLAlchemy SQLite conflict inserts](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#insert-on-conflict-upsert).
