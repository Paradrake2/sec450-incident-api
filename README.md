# SEC450 Incident Management API

**Cybersecurity Incident Evidence and SEC Disclosure Support**

A REST API for receiving, storing, assessing, and reviewing cybersecurity incidents. The backend preserves submitted evidence and supports human review and preliminary disclosure drafting. The selected Team 102 integration uses its read-only Query API and preserves source reports in our application-owned PostgreSQL database. Shared PostgreSQL is deferred. The existing API also accepts incident JSON directly.

> **Coursework prototype — synthetic data only.** This is not a production incident-response or legal compliance system. Automated screening does **not** establish legal materiality, and disclosure output is not an SEC filing.

## Live service

| Resource | URL |
| --- | --- |
| Deployed service | https://sec450-incident-api.onrender.com |
| API base URL | https://sec450-incident-api.onrender.com/api/v1 |
| Interactive Swagger UI | https://sec450-incident-api.onrender.com/docs |
| Machine-readable OpenAPI schema | https://sec450-incident-api.onrender.com/openapi.json |
| Health check | https://sec450-incident-api.onrender.com/health |

**Technology:** Python 3.11, FastAPI, Uvicorn, SQLAlchemy, PostgreSQL (Render), pytest, and GitHub Actions.

## Current implementation

The backend supports incident submission, persistent storage, retrieval, state tracking, and deterministic assessment. Human reviews have append-only history, related-incident links are stored symmetrically, and each generated non-final disclosure draft is preserved. The configured `delivery_app` entry point adds isolated course-system sessions and a real HTTP incident suite. The standalone `main:app` still exposes an unconfigured tester foundation. Swagger provides interactive developer/tester pages; a product dashboard, reviewer UI, evidence revisions, authenticated reviewer access, and deadline tracking remain planned work. See the [local walkthrough](docs/local-walkthrough.md) and [implementation status and decisions](docs/implementation-status.md). Local verification is complete for the recorded checkpoint; deployed and cross-team completion remain separate.

The system supports organizational processes relevant to **Regulation S-K Item 106** (cybersecurity risk management and governance disclosures) and **Form 8-K Item 1.05** (material cybersecurity incident reporting). The company, through authorized personnel, retains responsibility for materiality decisions and any SEC filing.

## API reference

Except for `/health`, paths below are relative to `/api/v1`.

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/health` | Returns `{ "status": "ok" }`; also available at site-root `/health`. |
| `POST` | `/test/reset` | Deletes **all** incidents, reviews, links, and drafts in the configured database (`204`). **Destructive and unauthenticated.** |
| `POST` | `/incidents` | Creates an incident; returns `201` with state `RECEIVED`. |
| `GET` | `/incidents` | Lists incidents; optional `?state=RECEIVED` filter. |
| `GET` | `/incidents/{incidentId}` | Retrieves an incident by ID. |
| `POST` | `/incidents/{incidentId}/assessment` | Performs deterministic keyword-based screening. |
| `POST` | `/incidents/{incidentId}/review` | Records a human `MATERIAL` or `NOT_MATERIAL` decision and preserves review history. |
| `GET` | `/incidents/{incidentId}/reviews` | Retrieves recorded review decisions in chronological order (extension). |
| `POST` | `/incidents/{incidentId}/related` | Stores an undirected incident link; retrieval is symmetric. |
| `POST` | `/incidents/{incidentId}/disclosure` | Generates and preserves a non-final legal-review template after a stored human `MATERIAL` review. |
| `GET` | `/incidents/{incidentId}/disclosures` | Retrieves saved disclosure drafts in chronological order (extension). |

Typical response codes are `200` (success), `201` (created), `204` (reset), `400` (invalid data), `404` (not found), `409` (invalid state/conflict), and `415` (unsupported content type). Application-defined errors generally use JSON of the form `{"error":"...","message":"..."}`; see Swagger/OpenAPI for schemas and examples.

### Submit a sample incident

Use synthetic incident content. In **Windows PowerShell**:

```powershell
$base = "https://sec450-incident-api.onrender.com/api/v1"
$body = @{
    title = "Synthetic SSH brute-force attempt"
    log = "Multiple failed logins and unauthorized access attempts against a test host"
    occurredAt = "2000-01-01T00:00:00Z"
    source = "SSH"
} | ConvertTo-Json

$incident = Invoke-RestMethod -Uri "$base/incidents" -Method Post -ContentType "application/json" -Body $body
$incident | Format-List

# Assessment operates on the ID returned by creation
Invoke-RestMethod -Uri "$base/incidents/$($incident.id)/assessment" -Method Post
Invoke-RestMethod -Uri "$base/incidents/$($incident.id)"
```

`POST /incidents` requires:

| Field | Type | Required? | Constraint |
| --- | --- | --- | --- |
| `title` | string | Yes | Nonblank, 1–200 characters |
| `log` | string | Yes | Nonblank, plain-text evidence |
| `occurredAt` | RFC 3339 date-time string | Yes | Timezone required; example: `2000-01-01T00:00:00Z` |
| `source` | string | No | Up to 100 characters; omit when unknown, explicit null is invalid |

Review `rationale` is likewise optional but must be a string when supplied.
Related-incident requests accept additional properties without storing them.
Review/related requests check incident existence before decoded body fields;
malformed JSON is rejected as a syntax error before dependency resolution.

### Assessment and lifecycle

```text
POST /incidents
      |
      v
   RECEIVED
      |
      | POST /assessment
      +-------------------------------+
      |                               |
      v                               v
POTENTIALLY_MATERIAL             UNDER_REVIEW
      |                               |
      +---------- human review -------+
                    |
         +----------+----------+
         v                     v
 MATERIAL_CONFIRMED         NOT_MATERIAL
         |                     |
  /disclosure allowed     Can be reviewed again
```

Screening applies deterministic weighted rules to the submitted plain-text log, with basic nearby-negation handling (for example, `ransomware`, `data breach`, `unauthorized access`). See [triage rules and limits](docs/triage.md). It flags potential review needs, **not** actual SEC materiality. Only the human-review endpoint can set `MATERIAL_CONFIRMED` or `NOT_MATERIAL`; a confirmed material incident cannot be downgraded via the current rules. The specification includes a `CLOSED` state, but the prototype has no close operation.

### Cross-team integration with Team 102

The current API supports direct incident intake, which earlier integration
notes described as **Team 102 → Team 105**:

1. Team 102 detects a security incident from its log-processing application.
2. Team 102 converts the relevant technical report into our required JSON fields (`title`, `log`, `occurredAt`, optional `source`).
3. Team 102 sends `POST https://sec450-incident-api.onrender.com/api/v1/incidents`.
4. Our API returns an incident ID and stores the record with state `RECEIVED`.
5. Either team can use that ID to verify retrieval and subsequent assessment.

The selected approach is now Team 102's **read-only Query API** at
https://seclog.redinbo.win, with our own PostgreSQL storage for preserved source
reports and application records. Shared database access is deferred and was
not established as an inter-team agreement.

An operator-only importer can fetch one explicitly selected report and retain
an immutable content snapshot plus retrieval provenance. It uses migrations
and has no public import endpoint, automatic polling, or automatic incident
creation. The complete source body contract and incident mapping still need
confirmation; importer tests use synthetic responses.
The read-only `python -m team102_probe` command can discover one report and
compare its detail response with our incident schema without importing data.
It prints redacted schema diagnostics and makes at most two GET requests;
`--report-id` bypasses discovery. One authenticated live sample matched the
guide's numeric `report_id`/object `body` envelope, not our `IncidentResponse`.
This establishes that sample's shape, not full source-contract conformance.
The API key belongs only in ignored local secrets.
Use `python -m team102_probe --resource logs` to inspect the separate log feed
with one bounded HTTPS GET; it does not import logs or infer incident records.
See [API import setup and limits](docs/team102-api-import.md) and
[company-data and reviewer-access design](docs/company-data-design.md).
The report-feed integration and course cross-team testing remain separate.

## Run locally

From the repository root, with Python 3.11 installed, create the environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Use PostgreSQL for normal local development. With Docker Desktop running:

```powershell
if (!(Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
docker compose up -d --wait postgres
.\.venv\Scripts\python.exe -m uvicorn main:app --env-file .env --reload
```

Then visit http://127.0.0.1:8000/docs. The development database binds to `127.0.0.1:55432`, with separate application and test databases. A native local PostgreSQL instance can use the same URL configuration; see [PostgreSQL setup and test isolation](docs/postgresql.md). Without `DATABASE_URL`, the application retains its SQLite fallback (`incidents.db`). Render already configures PostgreSQL through `DATABASE_URL`. Existing SQLite records are not migrated by this setup. Tables are created at service startup.

Database-backed routes share a request-scoped session dependency. Alternate local stores can be selected through `app.state.database_engine` before startup; request dependency overrides alone do not redirect startup/backfills. See [database access and validation notes](docs/implementation-status.md#database-access-seam).

## Deployment

The repository includes `render.yaml` defining the Render Python web service, startup command, and PostgreSQL connection. Render's deployed configuration supplies `DATABASE_URL` as an environment variable; do not commit credentials. Both `psycopg2-binary` and `psycopg[binary]` are currently included. SQLAlchemy selects the driver from the URL, so verify the deployed URL/driver before removing either dependency. Merge and deployment review remain separate from local implementation.

The web service starts using:

```bash
uvicorn delivery_app:create_app --factory --workers 1 --host 0.0.0.0 --port $PORT
```

The delivery entry point preserves the normal API and adds an isolated `/system`
and configured `/tester`. Public startup requires explicit `COURSE_TEST_DATABASE_URL`;
the proposed Blueprint uses generated schemas in the existing physical database
with `COURSE_TEST_ALLOW_SHARED_DATABASE=1`. This is internal test isolation, not
a shared database agreement with Team 102. Verify actual hosting settings before
deploying. See [the Phase 4 runbook](docs/phase4-delivery.md).

Render free-tier instances may spin down when idle, resulting in a cold-start delay. Database plans may have expiration, storage, and availability restrictions. Check the current service/database plan before relying on long-term retention. Deployment should be tied to a specific Git commit SHA when reporting test evidence.

## Testing and validation

### Automated tests included with the application

- Root-level `test_api.py`, `test_triage.py`, `test_review.py`, `test_related.py`, `test_disclosure.py`, `test_contract.py`, and `test_database.py` cover API behavior, deterministic screening, histories, relations, disclosure versions, the documented common contract, and database selection/isolation.
- `.github/workflows/main.yml` runs pytest; `.github/workflows/pytest.yml` runs syntax verification, the configured Ruff selection, and SQLite/PostgreSQL regressions on pushes/pull requests.
- Root-level `tests.yml` is outside `.github/workflows/` and is not an active GitHub Actions workflow. `python -m course_acceptance` invokes the deployed tester and saves actual control/result evidence.

To run the local tests:

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest -q
```

Without `TEST_DATABASE_URL`, API tests use temporary SQLite stores. Set an explicit loopback PostgreSQL test URL to run the same suite against disposable schemas in a dedicated test database; see [PostgreSQL test commands](docs/postgresql.md#run-postgresql-regressions). Tests do not inherit the application's `DATABASE_URL` and must not target the shared Render database.

The [Phase 4 delivery](docs/phase4-delivery.md) configures real HTTP checks for all
nine incident operations. Its system setup/report live under `/system/test`, and
tester setup/run/report under `/tester/test`. Run it locally on port 8002:

```powershell
.\.venv\Scripts\python.exe -m uvicorn delivery_app:create_local_app --factory --env-file .env --host 127.0.0.1 --port 8002 --workers 1
# In another terminal:
.\.venv\Scripts\python.exe -m course_acceptance --local
```

Open [the local tester docs](http://127.0.0.1:8002/tester/docs) and
[isolated incident API docs](http://127.0.0.1:8002/system/docs). Use target base
`http://127.0.0.1:8002/system`. Setup and reset seed candidate synthetic fixtures
in session-owned stores. The normal root `/api/v1/test/reset` remains destructive
and is not the course setup endpoint. The suite includes 41 executable checks
and five explicit gaps; it does not establish shared labels or full pod
conformance. See [models](docs/system-models.md), [AC/test mapping](docs/phase4-test-mapping.md)
and [implementation evidence](docs/implementation-status.md).

To walk through setup, run, and saved-report retrieval locally, use the separate
synthetic demo:

```powershell
.\.venv\Scripts\python.exe -m tester_demo
.\.venv\Scripts\python.exe -m tester_demo --scenario fail
.\.venv\Scripts\python.exe -m tester_demo --scenario abort
.\.venv\Scripts\python.exe -m tester_demo --serve
```

Server mode exposes [interactive tester docs](http://127.0.0.1:8001/tester/docs).
The demo uses three synthetic cases with no external requests, database access,
API keys, or invented coverage measurements. It demonstrates the tester
lifecycle, including ordinary failures and saved aborts; it does not run the
incident API or establish cross-team conformance. See the [local tools](docs/cross-team-testing.md#local-tools)
for request bodies and expected responses. The normal `main:app` tester remains
unconfigured; use the delivery entry point for real project checks. Coverage
inventories, pod fixture/scope agreement and deployed cross-team evidence remain
pending.

The recovered Phase 2 common contract is now preserved separately from generated
OpenAPI. To audit all nine real incident operations in a disposable local store:

```powershell
.\.venv\Scripts\python.exe -m contract_audit --format markdown
```

This command reports observed success/error checks, requirement mappings, and
unverified statuses; it does not accept a remote target or claim cross-team
conformance. Exit `1` means observed differences were found, while `2` indicates
configuration failure. Missing shared fixtures and coverage stay unverified.
See [contract reconciliation and known differences](docs/phase2-contract-reconciliation.md)
and the [historical contract baseline](contracts/README.md). The local pytest
suite checks the auditor itself; a passing regression run does not erase the
contract differences reported by the audit.

Team 102's course system/tester URLs and detector fixtures were located in its
public repository and the live control flow was checked. These resolve the
previously unknown control URLs; the peer's query API still differs from our
nine incident operations. See the [repository and live-observation record](docs/team102-repository-review.md)
for pinned sources, test results, push-integration findings, and remaining pod
scope/fixture questions.

The verified own-service HTTP suite reports **41 passes and five explicit gap
skips**, with four control checks covering report readiness, run/report
agreement, repeat-run rejection and immutable retrieval. This result does not
establish complete bidirectional contract coverage. Keep individual run reports
and operational details in private release evidence.

When recording test evidence, identify the tested commit, actual outcomes,
relevant CI checks and known gaps. HTTP availability alone is not a full
functional check.

## Requirements traceability

| Requirement | Current implementation | Acceptance criterion |
| --- | --- | --- |
| REQ-01 | Submit/store plain-text incident logs | AC-01 |
| REQ-02 | Retrieve and list incidents | AC-02 |
| REQ-03 | Preliminary automated screening; quality not validated | AC-03 |
| REQ-04 | Observable incident states and most transitions | AC-04 |
| REQ-05 | Human review prototype; no reviewer authentication | AC-05 |
| REQ-06 | Persistent symmetric related incident links | AC-06 |
| REQ-07 | Preserved non-final disclosure templates | AC-07 |
| REQ-08 | Structured error handling; full conformance pending | AC-08 |

## Known limitations and security warnings

- **Public destructive reset:** `POST /api/v1/test/reset` is unauthenticated and removes all incident records; never store real data here.
- **No authentication or role authorization:** Anybody reaching the public demo API can read or submit test incidents and provide a reviewer identifier. This is not appropriate for production cybersecurity evidence.
- **Heuristic screening:** Weighted regex rules can miss significant incidents or flag benign text; no independently validated accuracy and no legal decision authority.
- **Drafts are templates:** No final Form 8-K is produced, filed, or approved. Nature, scope, timing, and impact require legal confirmation.
- **Lifecycle and integrity gaps:** `CLOSED` has no route. Review and draft histories exist; conditional state updates protect competing assessments/reviews, with transaction races verified on PostgreSQL. A complete mutation audit trail and tamper-evident storage remain planned. `/health` does not prove database readiness.
- **Testing gaps:** Full cross-team conformance and performance/security testing are not established. Isolated reset restores Team 105 candidate fixtures, not a pod-agreed dataset. Code/component/system coverage is explicitly unknown. Test sessions/reports live in one process, capped at 32 per registry, and disappear after restart; run one worker/instance.

## Repository layout

```text
main.py                       FastAPI routes and persistence models
delivery_app.py               Product + isolated system + configured real tester
course_system.py              Isolated course system controls and routing
system_sessions.py            Session-owned disposable stores
incident_suite.py             Ordered incident API checks and explicit gaps
course_acceptance.py          Repeatable HTTP tester invocation/evidence client
triage.py                     Deterministic assessment rules
disclosure_templates.py       Non-final disclosure templates
contract_schemas.py           Shared response models
requirements.txt              Production dependencies
requirements-dev.txt          Development/testing dependencies
render.yaml                   Render deployment configuration
test_*.py                     Local automated tests
.github/workflows/main.yml    Pytest workflow
.github/workflows/pytest.yml  Syntax, Ruff, and pytest workflow
docs/                         Behavior, contract audit, implementation status
README.md                     This documentation
```

**Scope:** Educational prototype for incident management, evidence handling, and SEC disclosure support.
