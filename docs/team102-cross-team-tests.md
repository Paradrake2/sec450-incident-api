# Team 102 cross-team Query API checks

This is a **separate, manual read-only check** of Team 102's *own* documented API, not a replay of our incident-management contract. It is based on the Team 102 `docs/testing-interface.md` and `sec450/api/app.py` snapshot shared on 2026-11-21.

The tests target only `https://seclog.redinbo.win`, issue at most **10 HTTPS GET requests**, follow no redirects, verify TLS, do not retry, and cap each JSON response at 2 MiB. They do not reset, submit, mutate, import, or retain Team 102's events, logs, or reports. Running the suite exercises normal authentication and audit logging on their side. Run manually, not on every CI commit, and respect their rate limit of 60 requests/minute per key.

## Run from PowerShell (repository root)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m team102_acceptance --output .venv/test-results/team102-readonly.json
```

No virtual-environment activation is required, avoiding Windows PowerShell execution-policy restrictions. The report prints and is saved to the ignored `.venv` folder. The exit code is 0 when there are no failed/error checks (even if authenticated checks skip); 1 if any check fails/errors. **Always read the pass/skip totals.**

Without a key, three checks run: `GET /health` (200 + status), `GET /v1/events` without credentials (401 + Bearer challenge), and `GET /v1/events` with an intentionally invalid credential (401 + Bearer challenge). The other seven checks are **skipped**, not passed.

For the seven authenticated checks, request a read-only key from Team 102 and place it in the ignored project `.env` as `SEC450_KEY=...`, or set it in your current PowerShell session (do **not** put the secret in commands you share or commit):

```powershell
$env:SEC450_KEY = Read-Host "Team 102 API key"
.\.venv\Scripts\python.exe -m team102_acceptance --output .venv/test-results/team102-readonly.json
Remove-Item Env:SEC450_KEY
```

Authenticated checks validate their actual endpoints and minimum documented shapes:

| ID | Team 102 API behavior checked |
| --- | --- |
| `events.list` | `GET /v1/events?limit=5`: 200; query/count/gaps/events envelope and basic event fields |
| `events.invalid-limit` | `GET /v1/events?limit=0`: 400 with error object and no event data |
| `logs.list` | `GET /v1/logs?limit=5`: 200; query/count/gaps/logs envelope and basic log fields |
| `reports.list` | `GET /v1/reports?limit=5`: 200; count and report-ID/body envelope |
| `reports.detail` | `GET /v1/reports/{id}` for one listed report: 200; matching ID and body; **skips** if none available |
| `reports.invalid-id` | `GET /v1/reports/abc`: 400 with error object |
| `integrity.intact` | `GET /v1/integrity`: 200, batch and audit chain statuses both `intact` |

A list may legitimately be empty; the tests do not claim live traffic is always available. Checks establish response behavior for a bounded read, not the accuracy of detection, completeness of data collection, full schema conformance, or automatic incident forwarding. Reports save only statuses and generalized validation messages, never source payloads or secrets.

Run the deterministic, **offline** fixture tests as part of development or CI:

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_team102_acceptance.py
```

**Separate integration boundary:** Team 102's forwarding of a detected report to Team 105's `POST /api/v1/incidents` is a different test. This suite intentionally does not test that POST or modify Team 105's database. Continue to validate that integration with synthetic incident submission and subsequent GET retrieval as a separate workflow.
