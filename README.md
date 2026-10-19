# SEC450 Incident Management API

**Team 105 (Indy, Pod C) · Phase 3 — Cybersecurity Incident Evidence & SEC Disclosure Support**

A REST API for receiving, storing, assessing, and reviewing cybersecurity incidents. The project is designed to work with Pod C's Team 102 log detection service: their system **pushes** selected incident reports to ours; ours preserves the submitted evidence and supports subsequent review and preliminary disclosure drafting.

> **Coursework prototype — synthetic data only.** This is not a production incident-response or legal compliance system. Automated screening does **not** establish legal materiality, and disclosure output is not an SEC filing.

## Live service

| Resource | URL |
| --- | --- |
| Deployed service | https://sec450-incident-api.onrender.com |
| API base URL | https://sec450-incident-api.onrender.com/api/v1 |
| Interactive Swagger UI | https://sec450-incident-api.onrender.com/docs |
| Machine-readable OpenAPI schema | https://sec450-incident-api.onrender.com/openapi.json |
| Health check | https://sec450-incident-api.onrender.com/health |

**Technology:** Python 3.11, FastAPI, Uvicorn, SQLAlchemy, PostgreSQL (Render), pytest, and GitHub Actions (when configured in the repository).

## Phase 3 milestone

Phase 3 delivers a public backend for basic incident management: incident submission, persistent storage, retrieval, state tracking, and preliminary rule-based assessment. It also includes **prototype** human review, related-incident links, and disclosure-draft endpoints for further development in subsequent phases.

The system supports organizational processes relevant to **Regulation S-K Item 106** (cybersecurity risk management and governance disclosures) and **Form 8-K Item 1.05** (material cybersecurity incident reporting). The company, through authorized personnel, retains responsibility for materiality decisions and any SEC filing.

## API reference

Except for `/health`, paths below are relative to `/api/v1`.

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/health` | Returns `{ "status": "ok" }`; also available at site-root `/health`. |
| `POST` | `/test/reset` | Deletes **all** incident records in the test database (`204`). **Destructive and unauthenticated.** |
| `POST` | `/incidents` | Creates an incident; returns `201` with state `RECEIVED`. |
| `GET` | `/incidents` | Lists incidents; optional `?state=RECEIVED` filter. |
| `GET` | `/incidents/{incidentId}` | Retrieves an incident by ID. |
| `POST` | `/incidents/{incidentId}/assessment` | Performs deterministic keyword-based screening. |
| `POST` | `/incidents/{incidentId}/review` | Records a human `MATERIAL` or `NOT_MATERIAL` decision (prototype). |
| `POST` | `/incidents/{incidentId}/related` | Links two incidents symmetrically (prototype). |
| `POST` | `/incidents/{incidentId}/disclosure` | Generates a non-final legal-review template only for confirmed material incidents (prototype). |

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

Screening searches the submitted plain-text log for configured keywords (for example, `ransomware`, `data breach`, `unauthorized access`). It flags potential review needs, **not** actual SEC materiality. Only the human-review endpoint can set `MATERIAL_CONFIRMED` or `NOT_MATERIAL`; a confirmed material incident cannot be downgraded via the current rules. The specification includes a `CLOSED` state, but the prototype has no close operation.

### Cross-team integration with Team 102

The intended direction of incident delivery is **Team 102 → Team 105**:

1. Team 102 detects a security incident from its log-processing application.
2. Team 102 converts the relevant technical report into our required JSON fields (`title`, `log`, `occurredAt`, optional `source`).
3. Team 102 sends `POST https://sec450-incident-api.onrender.com/api/v1/incidents`.
4. Our API returns an incident ID and stores the record with state `RECEIVED`.
5. Either team can use that ID to verify retrieval and subsequent assessment.

Team 102 also publishes a **separate read-only Query API** at https://seclog.redinbo.win, using authenticated endpoints such as `/v1/reports`. Our deployed application **does not automatically poll or ingest** that query API. Pushing reports and testing each other's services are separate concerns; a report-to-incident mapping is still an integration specification issue.

## Run locally

From the repository root, with Python 3.11 installed:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Then visit http://127.0.0.1:8000/docs. Without `DATABASE_URL`, the project uses a local SQLite file (`incidents.db`); on Render, it uses PostgreSQL via `DATABASE_URL`. The database tables are created at service startup.

## Deployment

The repository includes `render.yaml` defining the Render Python web service, startup command, and PostgreSQL connection. Deploy through **Render → New → Blueprint**, link this GitHub repository, review pricing/plan availability, and approve the blueprint. Ensure production dependencies include the PostgreSQL driver `psycopg[binary]` (SQLAlchemy's PostgreSQL connection requires it). Render's deployed configuration supplies `DATABASE_URL` as an environment variable; do not commit credentials.

The web service starts using:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Render free-tier instances may spin down when idle, resulting in a cold-start delay. Database plans may have expiration, storage, and availability restrictions. Check the current service/database plan before relying on long-term retention. Deployment should be tied to a specific Git commit SHA when reporting test evidence.

## Testing and validation

### Automated tests included with the application

- `tests/test_api.py` — pytest/FastAPI `TestClient` functional tests against a local temporary test database.
- `test_deployed_api.py` — optional independent HTTPS testing script used in Phase 3; if added to the repository, it targets the deployed service without resetting existing incident data.
- `.github/workflows/tests.yml` — GitHub Actions workflow (where installed), for syntax verification, Ruff static analysis, and pytest regression tests on pushes/pull requests.

To run the local tests:

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest -q
```

**Recorded Phase 3 deployed-service check:** October 18, 2026, approximately 23:57 UTC — **23 run, 23 passed, 0 failed**. Checks included service availability, creation/retrieval, state filtering, positive/negative assessments, human review, relations, draft generation, and failure responses (`400`, `404`, `409`, `415`). The run was made against `https://sec450-incident-api.onrender.com`. This is a **custom deployed API test suite**, not a claim that the complete instructor-provided OpenAPI acceptance suite or security/performance tests have passed. The matching `sec450-test-results.json` can be retained as submission evidence.

**Cross-team manual checks:** Team 102 reported **4/4** successful checks against this service (connectivity, create, list, retrieve) on October 18, 2026. Team 105's initial six queries against Team 102's separate read-only API yielded one successful health check and five authenticated calls blocked by `401` (no valid test credentials); those protected functions were therefore not verified. Neither result proves complete bidirectional contract coverage.

When documenting testing for grading, include the exact deployed commit, test timestamp, pass/fail counts, available CI-run link, and known gaps. Do not represent HTTP availability checks as full functional tests.

## Requirements traceability

| Requirement | Phase 3 implementation | Acceptance criterion |
| --- | --- | --- |
| REQ-01 | Submit/store plain-text incident logs | AC-01 |
| REQ-02 | Retrieve and list incidents | AC-02 |
| REQ-03 | Preliminary automated screening; quality not validated | AC-03 |
| REQ-04 | Observable incident states and most transitions | AC-04 |
| REQ-05 | Human review prototype; no reviewer authentication | AC-05 |
| REQ-06 | Symmetric related incident links prototype | AC-06 |
| REQ-07 | Non-final disclosure template prototype | AC-07 |
| REQ-08 | Structured error handling; full conformance pending | AC-08 |

## Known limitations and security warnings

- **Public destructive reset:** `POST /api/v1/test/reset` is unauthenticated and removes all incident records; never store real data here.
- **No authentication or role authorization:** Anybody reaching the public demo API can read or submit test incidents and provide a reviewer identifier. This is not appropriate for production cybersecurity evidence.
- **Naive screening:** Keyword matches can miss significant incidents or flag benign text; no independently validated accuracy and no legal decision authority.
- **Drafts are templates:** No final Form 8-K is produced, filed, or approved. Nature, scope, timing, and impact require legal confirmation.
- **Lifecycle and integrity gaps:** `CLOSED` has no route, no immutable audit history or tamper-evident storage is implemented, and `/health` does not prove database readiness.
- **Testing gaps:** The instructor's full acceptance suite, comprehensive cross-team coverage, and performance/security testing are not yet established. `POST /test/reset` clears records instead of re-seeding a shared fixture database.

## Repository layout

```text
main.py                    FastAPI routes, models, and database access
requirements.txt           Production dependencies
requirements-dev.txt       Development/testing dependencies
render.yaml                Render deployment configuration
tests/test_api.py           Local automated API tests
.github/workflows/tests.yml  CI checks (when present)
README.md                  This documentation
```

**Team:** SEC450 Indy, Pod C, Team 105  
**Scope:** Educational prototype for incident management, evidence handling, and SEC disclosure support.
