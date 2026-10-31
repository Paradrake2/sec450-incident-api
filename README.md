# SEC450 Incident Management API

**Team 105 (Indy, Pod C) — Cybersecurity Incident Evidence & SEC Disclosure Support**

A REST API for receiving, storing, assessing, and reviewing cybersecurity incidents. The backend preserves submitted evidence and supports human review and preliminary disclosure drafting. Integration with Pod C's Team 102 log detection service is being designed around a proposed shared PostgreSQL company-data source; the existing API also accepts incident JSON directly.

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

The backend supports incident submission, persistent storage, retrieval, state tracking, and deterministic assessment. Human reviews have append-only history, related-incident links are stored symmetrically, and each generated non-final disclosure draft is preserved. A mounted tester protocol foundation supports injected-adapter setup/run/report checks; its default configuration makes no outbound requests. System sessions, the real project suite, a product frontend, evidence revisions, authenticated reviewer access, and deadline tracking remain planned work. See [implementation status and decisions](docs/implementation-status.md).

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
    occurredAt = "2026-10-18T14:30:00Z"
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
| `occurredAt` | ISO 8601 date-time | Yes | Example: `2026-10-18T14:30:00Z` |
| `source` | string | No | Up to 100 characters |

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

The user clarified on October 31 that the desired direction is a common
PostgreSQL company-data store that Team 105 reads, and confirmed that this is
still a design proposal. Shared schema, source identity, ingestion ownership,
and permissions remain to be agreed with Team 102. See
[company-data and reviewer-access design](docs/company-data-design.md).

Team 102 also documents a **separate read-only Query API** at
https://seclog.redinbo.win, using authenticated endpoints such as `/v1/reports`.
Our deployed application does not automatically poll or ingest that API, and
no shared-database importer exists yet. The API intake capability, the proposed
company-data source, and course cross-team testing are separate interfaces.

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
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Render free-tier instances may spin down when idle, resulting in a cold-start delay. Database plans may have expiration, storage, and availability restrictions. Check the current service/database plan before relying on long-term retention. Deployment should be tied to a specific Git commit SHA when reporting test evidence.

## Testing and validation

### Automated tests included with the application

- Root-level `test_api.py`, `test_triage.py`, `test_review.py`, `test_related.py`, `test_disclosure.py`, `test_contract.py`, and `test_database.py` cover API behavior, deterministic screening, histories, relations, disclosure versions, the documented common contract, and database selection/isolation.
- `.github/workflows/main.yml` runs pytest; `.github/workflows/pytest.yml` runs syntax verification, the configured Ruff selection, and SQLite/PostgreSQL regressions on pushes/pull requests.
- Root-level `tests.yml` is outside `.github/workflows/` and is not an active GitHub Actions workflow. No deployed-service test script is present in this repository.

To run the local tests:

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest -q
```

Without `TEST_DATABASE_URL`, API tests use temporary SQLite stores. Set an explicit loopback PostgreSQL test URL to run the same suite against disposable schemas in a dedicated test database; see [PostgreSQL test commands](docs/postgresql.md#run-postgresql-regressions). Tests do not inherit the application's `DATABASE_URL` and must not target the shared Render database. The [tester protocol foundation](docs/cross-team-testing.md) is mounted at `/tester`, with its schema at `/tester/openapi.json`. Default setup fails closed until a trusted target adapter and agreed project suite are configured. Root system setup/report and cross-team validation are still pending; `/api/v1/test/reset` is not an isolated session-setup endpoint. Current baseline and implementation validation are recorded in [the status log](docs/implementation-status.md).

**Recorded Phase 3 deployed-service check:** October 18, 2026, approximately 23:57 UTC — **23 run, 23 passed, 0 failed**. Checks included service availability, creation/retrieval, state filtering, positive/negative assessments, human review, relations, draft generation, and failure responses (`400`, `404`, `409`, `415`). The run was made against `https://sec450-incident-api.onrender.com`. This is a **custom deployed API test suite**, not a claim that the complete instructor-provided OpenAPI acceptance suite or security/performance tests have passed. The matching `sec450-test-results.json` can be retained as submission evidence.

**Cross-team manual checks:** Team 102 reported **4/4** successful checks against this service (connectivity, create, list, retrieve) on October 18, 2026. Team 105's initial six queries against Team 102's separate read-only API yielded one successful health check and five authenticated calls blocked by `401` (no valid test credentials); those protected functions were therefore not verified. Neither result proves complete bidirectional contract coverage.

When documenting testing for grading, include the exact deployed commit, test timestamp, pass/fail counts, available CI-run link, and known gaps. Do not represent HTTP availability checks as full functional tests.

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
- **Testing gaps:** The instructor's full acceptance suite, comprehensive cross-team coverage, and performance/security testing are not yet established. `POST /api/v1/test/reset` clears records instead of re-seeding a pod-agreed fixture database.

## Repository layout

```text
main.py                       FastAPI routes and persistence models
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

**Team:** SEC450 Indy, Pod C, Team 105  
**Scope:** Educational prototype for incident management, evidence handling, and SEC disclosure support.
