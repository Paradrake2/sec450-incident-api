# Local PostgreSQL development and testing

PostgreSQL is the normal local development database for this setup, matching
the database configured for Render. SQLite remains available for a fast
dependency-free test run and as the application's fallback when `DATABASE_URL`
is absent. This configuration does not connect to Team 102's company-data
database or change Render.

## Start with Docker Compose

Install Docker Desktop and start its Linux engine. From the repository root:

```powershell
if (!(Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
docker compose up -d --wait postgres
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --env-file .env --reload
```

The local service binds only `127.0.0.1:55432`. It creates application database
`sec450` and separate test database `sec450_test`. The `sec450` role and example
password are throwaway development credentials. They are not a production role
or authentication design. Set the password and both URLs consistently when
changing the example. URL-encode special characters inside connection URLs.
Existing shell environment variables take precedence over values loaded from
`.env` by Uvicorn; clear an old `DATABASE_URL` before starting a different store.

The database lives in the Compose named volume. Stop it with
`docker compose stop postgres`; normal stops preserve records. Initialization
SQL runs only on a new empty volume. Changing `.env` does not change an existing
database user's password or rerun initialization. Use PostgreSQL administration
commands to update an existing development database rather than deleting its
volume.

Do not run the Compose service while another PostgreSQL instance occupies port
55432. Use one local database runtime at a time. `docker compose config --quiet`
validates Compose syntax without printing expanded connection settings.

## Use an existing native PostgreSQL installation

Use a local instance bound to loopback and create separate `sec450` and
`sec450_test` databases with separate non-superuser development roles. The
application role needs permission to create its current tables; the test role
needs permission to create/drop schemas in the test database. Set the two URLs
in an ignored `.env` file, for example the shapes in `.env.example`, and use the
same Uvicorn command above. The example Compose credentials and its local
initialization role are independent of these native roles.

Choose an unused local port; this repository's example uses 55432. Do not reuse a
shared company database, Render database, or existing administrator-owned
database as the test database. The setup does not move records from an existing
`incidents.db`; leave that file intact. Any future data migration needs its own
reviewed import and verification step.

### This workspace's native instance

On October 31, 2026, an isolated PostgreSQL 17.4 cluster was initialized under
the ignored `.venv/postgres/data` directory. It uses `127.0.0.1:55432`, separate
application/test databases, and separate non-superuser roles. Generated
connection credentials are saved only in ignored local files, including `.env`.
Existing PostgreSQL databases and SQLite records were not modified. Preserve
the cluster directory when rebuilding the Python environment.

Check its status, or restart it after stopping the process or rebooting:

```powershell
& 'C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe' -D '.venv\postgres\data' status
# Run start only when this cluster is stopped and port 55432 is available.
& 'C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe' -D '.venv\postgres\data' -l '.venv\postgres\server.log' -o '-h 127.0.0.1 -p 55432' start
.\.venv\Scripts\python.exe -m uvicorn main:app --env-file .env --reload
```

Stop the application before stopping its database. A normal database stop
preserves the cluster:

```powershell
& 'C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe' -D '.venv\postgres\data' -m fast stop
```

## Run PostgreSQL regressions

Tests use only `TEST_DATABASE_URL` for PostgreSQL selection. They never infer a
test destination from `DATABASE_URL`. The test helper requires a loopback host,
a dedicated database name ending `_test` or starting `test_`, and no URL query
options that can reroute the connection. Each disposable store receives a new
random schema and a schema-specific search path. Cleanup drops only those
generated schemas, including when a test fails. These are local regression
stores, separate from the future course testing session interface.

Pytest does not automatically load `.env`. In PowerShell, read just its test URL
without printing it:

```powershell
$testLine = Get-Content -LiteralPath .env | Where-Object { $_ -match '^TEST_DATABASE_URL=' } | Select-Object -First 1
if (!$testLine) { throw 'Add TEST_DATABASE_URL to .env first.' }
$env:TEST_DATABASE_URL = $testLine.Substring('TEST_DATABASE_URL='.Length)
.\.venv\Scripts\python.exe -m pytest -q
```

For the SQLite regression path, remove the explicit test URL first:

```powershell
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest -q
```

The GitHub Actions static-check workflow runs both SQLite and PostgreSQL 17
regressions. Its PostgreSQL database belongs to that ephemeral CI job; it never
uses a deployed service or calls a public reset endpoint. See the
[implementation status](implementation-status.md) for actual local and CI
validation evidence. Configuration alone does not establish a passing CI run.

Local validation on October 31, 2026 used Python 3.12.6 and PostgreSQL 17.4:

- PostgreSQL: **97 passed**, 3 existing dependency deprecation warnings, 14.10s.
- SQLite: **95 passed, 2 skipped**, the same 3 warnings, 12.75s. The two skipped
  tests require PostgreSQL to exercise competing transactions.
- The PostgreSQL suite includes actual competing assessment/review requests,
  isolated schema cleanup, and offset/naive timestamp regressions. Public timestamp
  serialization and persistence normalize to UTC; supplied timezone offsets
  preserve their instant on both backends. Naive occurrence and legacy review
  times retain the existing interpretation as UTC; original legacy JSON remains
  unchanged. PostgreSQL validation used a non-UTC server timezone.
- Compose configuration passed `docker compose --env-file .env.example config
  --quiet`. Docker containers were not started for this local validation.

These are local results. The added PostgreSQL CI job requires its own recorded
run before claiming GitHub Actions success.

## Operational limits

Current startup still creates missing tables and backfills legacy snapshots.
Managed migrations, backups/recovery verification, protected product access,
and the shared ingestion contract remain separate milestones. A database
switch does not authenticate a reviewer or turn advisory screening into a human
materiality decision.

References: [PostgreSQL official container initialization](https://hub.docker.com/_/postgres),
[Compose environment precedence](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/),
and [PostgreSQL service containers in GitHub Actions](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers).
Conditional transitions rely on PostgreSQL's default
[Read Committed update rechecks](https://www.postgresql.org/docs/17/transaction-iso.html#XACT-READ-COMMITTED)
and SQLAlchemy's [affected row counts](https://docs.sqlalchemy.org/en/20/tutorial/data_update.html#getting-affected-row-count-from-update-delete).
