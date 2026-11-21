"""Offline fixtures: never contact Team 102 or require SEC450_KEY in CI."""

import httpx

from team102_acceptance import BASE, CHECKS, run_checks


def mock_api(request):
    assert request.method == "GET"
    assert str(request.url).startswith(BASE + "/")
    path = request.url.path
    key = request.headers.get("Authorization")
    if path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if key != "Bearer test-key":
        return httpx.Response(401, headers={"WWW-Authenticate": "Bearer"}, json={"error": "invalid key"})
    if path == "/v1/events" and request.url.params.get("limit") == "0":
        return httpx.Response(400, json={"error": "limit invalid"})
    if path == "/v1/reports/abc":
        return httpx.Response(400, json={"error": "report id invalid"})
    if path == "/v1/events":
        return httpx.Response(200, json={"query": {}, "count": 1, "collection_gaps": [],
                                         "events": [{"event_id": 1, "ts": "2026-11-20T00:00:00Z", "source": "ssh_auth"}]})
    if path == "/v1/logs":
        return httpx.Response(200, json={"query": {}, "count": 1, "collection_gaps": [],
                                         "logs": [{"event_id": 1, "raw_id": 2, "ts": "2026-11-20T00:00:00Z",
                                                   "source": "ssh_auth", "host": "test", "client_ip": None,
                                                   "username": None, "text": "synthetic log"}]})
    if path == "/v1/reports":
        return httpx.Response(200, json={"query": {}, "count": 1, "collection_gaps": [],
                                         "reports": [{"report_id": 7, "cluster_id": 2,
                                                      "generated_at": "2026-11-20T00:00:00Z", "body": {}}]})
    if path == "/v1/reports/7":
        return httpx.Response(200, json={"report_id": 7, "cluster_id": 2,
                                         "generated_at": "2026-11-20T00:00:00Z", "body": {}})
    if path == "/v1/integrity":
        return httpx.Response(200, json={"batches": {"status": "intact", "batches": 5},
                                         "audit": {"status": "intact", "entries": 9}})
    raise AssertionError("Unexpected request: " + path)


def test_all_checks_pass_against_team102_fixture():
    report = run_checks("test-key", transport=httpx.MockTransport(mock_api))
    assert list(item["test_id"] for item in report["results"]) == list(CHECKS)
    assert report["counts"] == {"pass": 10, "fail": 0, "skip": 0, "error": 0}
    assert "test-key" not in str(report)
    assert "synthetic log" not in str(report)


def test_without_key_runs_only_public_and_negative_auth_checks():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return mock_api(request)

    report = run_checks(transport=httpx.MockTransport(handler))
    assert report["counts"] == {"pass": 3, "fail": 0, "skip": 7, "error": 0}
    assert paths == ["/health", "/v1/events", "/v1/events"]


def test_missing_report_skips_detail_without_inventing_failure():
    def handler(request):
        if request.url.path == "/v1/reports":
            return httpx.Response(200, json={"query": {}, "count": 0, "collection_gaps": [], "reports": []})
        return mock_api(request)

    report = run_checks("test-key", transport=httpx.MockTransport(handler))
    assert report["counts"] == {"pass": 9, "fail": 0, "skip": 1, "error": 0}
    detail = next(r for r in report["results"] if r["test_id"] == "reports.detail")
    assert detail["outcome"] == "skip"


def test_mismatched_schema_is_a_failure_without_leaking_data():
    def handler(request):
        if request.url.path == "/v1/logs":
            return httpx.Response(200, json={"unexpected": "private log data"})
        return mock_api(request)

    report = run_checks("test-key", transport=httpx.MockTransport(handler))
    assert report["counts"]["fail"] == 1
    assert "private log data" not in str(report)
    assert next(r for r in report["results"] if r["test_id"] == "logs.list")["outcome"] == "fail"


def test_upstream_403_is_reported_not_treated_as_schema_mismatch():
    def handler(request):
        if request.url.path == "/v1/logs":
            return httpx.Response(403, text="sensitive upstream error detail")
        return mock_api(request)

    report = run_checks("test-key", transport=httpx.MockTransport(handler))
    entry = next(r for r in report["results"] if r["test_id"] == "logs.list")
    assert entry["outcome"] == "fail" and "HTTP 403" in entry["message"]
    assert "sensitive upstream error detail" not in str(report)
