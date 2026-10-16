# SEC450 Incident Evidence API — Phase 3

This is a **coursework demo**, not a real cybersecurity compliance tool. Use **synthetic logs only**. It follows the Phase 2 v1.0.0 HTTP operation paths. The Phase 3 basic assessment uses deterministic keyword screening and is **not** an SEC materiality determination. Review, linking and disclosure are basic Phase 4 prototypes, not finished legal workflows.

## Get running locally (Windows PowerShell)

Install Python 3.11 and Git. Open this folder in VS Code and start a PowerShell terminal.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
pytest -q
uvicorn main:app --reload
```

If PowerShell prevents the activation script, skip activation and run ` .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`, then ` .\.venv\Scripts\python.exe -m pytest -q` and ` .\.venv\Scripts\python.exe -m uvicorn main:app --reload`.

Browse to `http://127.0.0.1:8000/docs` for the interactive API explorer. `GET http://127.0.0.1:8000/health` returns `{"status":"ok"}`.

## Deploy on Render with persistent Postgres

1. Create a **new private GitHub repository** such as `sec450-incident-api`. Upload **the contents of this folder**, not the enclosing folder, to the repository root (including hidden `.github/`). Use the GitHub website's *Add file > Upload files* or Git if you prefer.
2. At [dashboard.render.com](https://dashboard.render.com), choose **New > Blueprint**, link the GitHub repository, and apply its `render.yaml` configuration. This creates the Python web service and a Render PostgreSQL database with `DATABASE_URL` set automatically. Confirm pricing and availability before approval. If the selected plans are unavailable, deploy the equivalent database and service manually using the `buildCommand`, `startCommand` and `DATABASE_URL` values in `render.yaml`.
3. Wait for the **Deploy live** status. In the service page copy your actual `https://<your-service>.onrender.com` URL. That becomes your public origin; the API's base URL is `https://<your-service>.onrender.com/api/v1`.
4. Verify with `/health` and `/docs`, then run the sample request below. Post the actual base URL, not an invented one, to your team and the course registry.
5. On each push to your GitHub branch, Render redeploys, and GitHub Actions runs `pytest` (see Actions tab). Capture the Render deployment identifier/commit SHA and CI link for Gradescope.

**Important operational limitations:** Render's Free web service spins down when idle; its local filesystem is ephemeral, so the app uses PostgreSQL rather than SQLite in deployment. The Free Postgres database expires after 30 days and has no backups. Upgrade/migrate before expiration if the course needs longer availability. `POST /api/v1/test/reset` is intentionally **public and destructive** because the Phase 2 test contract explicitly requires unauthenticated reset. Do not upload secrets, real logs, or personal data. Review pricing rather than assuming it stays free.

## Example in PowerShell

```powershell
$base = "https://YOUR-ACTUAL-RENDER-URL.onrender.com/api/v1"
Invoke-RestMethod "$base/health"
$body = @{ title="Synthetic intrusion report"; log="Possible ransomware and unauthorized access to a test server"; occurredAt="2026-10-16T12:00:00Z"; source="synthetic-fixture" } | ConvertTo-Json
$incident = Invoke-RestMethod -Method Post -Uri "$base/incidents" -ContentType "application/json" -Body $body
$incident
$assessment = Invoke-RestMethod -Method Post -Uri "$base/incidents/$($incident.id)/assessment"
$assessment
Invoke-RestMethod "$base/incidents/$($incident.id)"
```

## Interface

| Method | Path (after `/api/v1`) | Output/status |
|---|---|---|
| GET | `/health` | `200 {"status":"ok"}` |
| POST | `/test/reset` | `204` clears all test incidents |
| POST | `/incidents` | `201` creates an incident in `RECEIVED` |
| GET | `/incidents?state=...` | `200` list, `400` invalid state |
| GET | `/incidents/{incidentId}` | `200` incident, `404` missing |
| POST | `/incidents/{incidentId}/assessment` | `200` basic assessment, `409` invalid state |
| POST | `/incidents/{incidentId}/review` | `200` human decision, `400/404/409` errors |
| POST | `/incidents/{incidentId}/related` | `200` symmetric relation, `404/409` errors |
| POST | `/incidents/{incidentId}/disclosure` | `200` non-final draft, `409` before material confirmation |

Full schema at `/openapi.json` (FastAPI's generated schema uses the root paths `/api/v1/...`, so the service origin is the server base when loading that document). Interactive Swagger UI is `/docs`.

### Phase 3 / acceptance criterion mapping

- `REQ-01` / `AC-01`: incident ingestion, validation, database storage, retrieval.
- `REQ-02` / `AC-02`: get individual and listed incidents; optional state filter.
- `REQ-03` / `AC-03`: basic deterministic screening (`ransomware`, `data breach`, etc.); legal appropriateness is **not** validated.
- `REQ-04` / `AC-04`: `RECEIVED` → `UNDER_REVIEW` or `POTENTIALLY_MATERIAL`; review decisions can move the state further. `CLOSED` has no dedicated close operation because Phase 2 did not specify one.
- `REQ-08` / `AC-08`: structured errors and HTTP 400/404/409/415 for primary failure cases.
- Basic prototypes: `REQ-05`/`AC-05`, `REQ-06`/`AC-06`, `REQ-07`/`AC-07`; these are not production-quality implementations.

## Known limitations (disclose in Gradescope)

1. Rule-based keyword screening does not establish whether an event meets the SEC materiality standard, and false positives/negatives are expected. Human reviewer authorization is not verified.
2. The API is unauthenticated per the shared contract; anyone with its public URL can view, insert, or reset synthetic data. No sensitive data allowed.
3. No immutable audit trail or real source collectors, no SEC submission integration. The draft is intentionally a conservative fill-in template, not a completed Item 1.05 disclosure.
4. The `CLOSED` state is enumerated but no close endpoint exists in the common specification. `GET /health` does not currently test DB readiness.
5. Only a starter suite of two integration tests is bundled. These tests do not establish full Phase 2 cross-team conformance, quality of assessments, or full acceptance-criterion coverage. Run the instructor's OpenAPI contract tests and report actual results.
6. The reset endpoint empties the test database rather than reloading a pre-agreed shared fixture set. If the pod expects seeded reset fixtures, align and implement them before cross-testing.

## Submission checklist

- Copy deployed origin/base URL and repository commit SHA to the class registry.
- In Gradescope include HTTP response + screenshot or pasted excerpt of Render logs (`method=POST ... status=201`).
- Note your actual test counts and any instructor contract mismatches; never claim that an unrun test passed.
- Arrange for a podmate to test against your deployed URL and run your tests against theirs. Record both results.
