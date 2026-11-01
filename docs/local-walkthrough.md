# Run and understand the local Phase 4 delivery

This walkthrough uses the configured delivery on **port 8002**. Port 8000 is the
normal API; port 8001 is the older synthetic tester demo. The interfaces are
Swagger developer/tester pages. No product dashboard or reviewer workspace has
been built yet.

## Start the service when it is not already running

Use PowerShell in the repository root. An existing native PostgreSQL instance
or Docker Compose must already be listening on 127.0.0.1:55432; use only one
database runtime. See [PostgreSQL setup](postgresql.md). This workspace already
has a native instance, so do not start a second Compose database on that port.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Use the dedicated test URL from ignored `.env` for course storage. This copies
the setting into the process environment without printing its credential value:

```powershell
$courseDbLine = Get-Content -LiteralPath .env | Where-Object { $_ -match '^TEST_DATABASE_URL=' } | Select-Object -First 1
if (!$courseDbLine) { throw 'Configure the dedicated local TEST_DATABASE_URL first.' }
$env:COURSE_TEST_DATABASE_URL = $courseDbLine.Substring('TEST_DATABASE_URL='.Length)
$env:COURSE_TEST_ALLOW_SHARED_DATABASE = '0'
.\.venv\Scripts\python.exe -m uvicorn delivery_app:create_local_app --factory --env-file .env --host 127.0.0.1 --port 8002 --workers 1
```

Keep that terminal open. Ctrl+C stops this foreground server gracefully. If
port 8002 is already occupied by the project delivery, use it instead of starting
another copy. If this workspace's background walkthrough owns it, its local-only
helper and stop instructions are recorded in the private team handoff.

Uvicorn loads the normal application's `DATABASE_URL` from `.env`; existing
shell variables take precedence. The normal API initializes that configured
development store. Course requests instead use fresh session-owned schemas in
the explicit test database. With no course URL, the local factory deliberately
uses temporary SQLite rather than inheriting either database URL. PostgreSQL
was used for the recorded local acceptance run.

## Choose the correct page

| Page | What you can do |
| --- | --- |
| http://127.0.0.1:8002/tester/docs | Prepare a tester session, run the automated incident checks, retrieve its report |
| http://127.0.0.1:8002/system/test/docs | Prepare an isolated system session manually; retrieve its system coverage report |
| http://127.0.0.1:8002/system/docs | Exercise all incident operations in the prepared isolated session |
| http://127.0.0.1:8002/docs | Exercise the normal application store; it is separate from course sessions |

The three operations on the tester page are controls for the suite. The
individual incident cases are result IDs in its report, not separate Swagger
endpoints. The remaining `test_*.py` tests run with pytest, outside Swagger.

## Run the automated checks in Swagger

1. On the tester page, expand `POST /test/setup`, click **Try it out**, replace
   the body below, and click **Execute**:

   ```json
   {"target_url":"http://127.0.0.1:8002/system"}
   ```

   Expect 201 and a new `session_id`. The tester has also prepared the target's
   isolated store. No separate manual system setup is needed for this run.

2. Expand `POST /test/run`, select **Try it out**, and use the returned ID:

   ```json
   {"session_id":"paste-the-returned-id"}
   ```

   Expect 200 with `results` and `coverage`. A working own target currently has
   **41 passed, 5 skipped, 0 failed, 0 errors**. Each result has a `test_id`, outcome, message
   and optional error source. Setup creates a new session each time; a session
   can run only once.

3. Expand `GET /test/report`, enter the same ID as the query parameter, and
   execute. Expect 200 and the same saved report. This does not rerun tests.
   Report-before-run and repeat-run return 409; an unknown ID returns 404.

The five gap results concern shared reset fixtures, agreed classification
labels, CLOSED state, injected server failures and disclosure quality. They
are visible skips. Candidate fixtures and passing HTTP checks do not establish
full pod conformance. Code/component/system coverage counts remain unknown,
with reasons; the41 passes are not line-coverage or agreed AC-coverage counts.

## Run the same controls from a terminal

With the server running, use a second PowerShell terminal in the repository:

```powershell
.\.venv\Scripts\python.exe -m course_acceptance --local --output .team-local/acceptance/local.json
```

This performs setup, report-before-run, run, saved report, rejected repeat run
and unchanged report retrieval. It prints case counts and **4/4 control checks**
and writes a schema-checked JSON evidence file. Exit 0 means no observed
failures/errors and all controls passed, while retaining the explicit skips.
Exit 1 means observed failures; exit 2 means setup/communication/validation could
not complete. Keep generated evidence excluded from Git.

Pytest is the separate unit/API/regression runner:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Pytest does not automatically read `.env`. Without `TEST_DATABASE_URL` in its
process environment, it uses temporary SQLite. The [PostgreSQL regression
instructions](postgresql.md#run-postgresql-regressions) select the dedicated
local test database explicitly. Do not combine pytest totals, HTTP case totals
and the separate 44-check local contract audit into one acceptance count.

## Walk through a single incident manually

1. Open the system-control page and execute `POST /test/setup` with a **new**
   session name each time (repeating an existing name returns 409):

   ```json
   {"session_id":"manual-demo-change-this-to-a-unique-value","setup":{}}
   ```

2. Open the isolated incident page. Headerless calls select that newest session;
   run this manual session serially. `GET /api/v1/incidents` should show the two
   stable candidate fixture incidents in RECEIVED state.
3. Execute `POST /api/v1/incidents`:

   ```json
   {
     "title":"Synthetic ransomware walkthrough",
     "log":"Synthetic exercise: ransomware detected; customer data exfiltration reported.",
     "occurredAt":"2026-11-01T14:00:00Z",
     "source":"local-walkthrough"
   }
   ```

   Expect 201, copy its `id`, and observe `state: RECEIVED`.
4. Execute `POST /api/v1/incidents/{incidentId}/assessment` with that ID. Then
   retrieve the incident. The synthetic positive signal should produce an
   advisory true flag and POTENTIALLY_MATERIAL state. Screening is deterministic
   rules, not runtime AI or a human materiality decision.
5. Execute `POST /api/v1/incidents/{incidentId}/review`:

   ```json
   {"decision":"MATERIAL","reviewer":"local-demo-reviewer","rationale":"Synthetic walkthrough only."}
   ```

   Expect 200 and MATERIAL_CONFIRMED. The supplied reviewer name is not an
   authenticated identity; administrator-approved accounts are future work.
6. Execute `POST /api/v1/incidents/{incidentId}/disclosure`. Expect a non-final
   draft with `final: false`. The review/disclosure history GET endpoints show
   the preserved records. No SEC filing occurs.
7. To try relations, create another incident, assess it if desired, then call
   `POST /api/v1/incidents/{incidentId}/related` with
   `{"relatedIncidentId":"the-other-id"}`. Retrieval shows the symmetric link.

The isolated reset clears only its selected session and restores candidate
fixtures. The normal root product reset deletes normal records; use `/system`
throughout this walkthrough. Registries retain 32 sessions each until restart;
capacity returns 503 rather than evicting reports/stores. Run one worker/instance.

## Architecture and delivery status

Read [the current architecture, state, data, validation and tester models](system-models.md)
and [the requirement/test/oracle mapping](phase4-test-mapping.md). The runtime
has three interfaces: normal product API, isolated system and automated tester.
Both incident interfaces reuse business handlers but select different stores.
The tester calls its approved target through HTTP; it does not invoke incident
functions directly. Team102 source import is a separate operator workflow that
preserves snapshots/observations without creating incidents automatically.

The local code/test/model checkpoint is implemented. The Phase 4 course goal
also requires a verified deployed release, own deployed acceptance, both
cross-team directions, registry entries and submission evidence. Those remain
pending until actually recorded. Do not infer completion from this local
walkthrough or the existence of hosting YAML. See [the release runbook](phase4-delivery.md)
and [current implementation evidence](implementation-status.md).
