"""Manual, read-only cross-team checks for Team 102's documented Query API.

This intentionally does NOT apply our incident-management contract to Team 102.
No network requests run on import, under pytest, or without an explicit CLI run.
Only the fixed HTTPS origin is contacted; no writes, retries, or redirects.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
import httpx

BASE = "https://seclog.redinbo.win"
MAX_BODY = 2 * 1024 * 1024
CHECKS = (
    "health.ok", "auth.missing-key", "auth.invalid-key",
    "events.list", "events.invalid-limit", "logs.list", "reports.list",
    "reports.detail", "reports.invalid-id", "integrity.intact",
)


class InvalidResponse(Exception):
    pass


def _object(value):
    return type(value) is dict


def _envelope(data, field, limit=5):
    if not _object(data) or not _object(data.get("query")):
        raise InvalidResponse("response must contain a query object")
    items = data.get(field)
    if not isinstance(items, list) or type(data.get("count")) is not int:
        raise InvalidResponse(f"response must contain {field} list and integer count")
    if data["count"] != len(items) or len(items) > limit:
        raise InvalidResponse(f"{field} count or limit does not match")
    if not isinstance(data.get("collection_gaps"), list):
        raise InvalidResponse("response must contain collection_gaps list")
    return items


def _has_keys(value, keys):
    return _object(value) and set(keys).issubset(value)


def run_checks(api_key=None, *, transport=None):
    """Return redacted results. Injectable transport supports entirely offline tests."""
    results = []
    sample_id = None

    def emit(name, outcome, message):
        results.append({"test_id": name, "outcome": outcome, "message": message})

    def attempt(name, path, *, expected=200, authenticated=False, params=None,
                validator=None, headers=None):
        if authenticated and not api_key:
            emit(name, "skip", "SEC450_KEY not configured; authenticated read not attempted")
            return None
        auth = {"Authorization": f"Bearer {api_key}"} if authenticated else {}
        if headers:
            auth.update(headers)
        try:
            with client.stream("GET", BASE + path, headers=auth, params=params) as response:
                status = response.status_code
                if status != expected:
                    outcome = "error" if status >= 500 or status == 429 else "fail"
                    emit(name, outcome, f"Expected HTTP {expected}, received HTTP {status}")
                    return None
                if status == 401 and "bearer" not in response.headers.get("WWW-Authenticate", "").lower():
                    raise InvalidResponse("401 response missing Bearer challenge")
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if mime != "application/json" and not (mime.startswith("application/") and mime.endswith("+json")):
                    raise InvalidResponse("response is not JSON")
                data_bytes = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(chunk) > MAX_BODY - len(data_bytes):
                        raise InvalidResponse("response exceeds 2 MiB limit")
                    data_bytes.extend(chunk)
                try:
                    data = json.loads(data_bytes)
                except (ValueError, UnicodeDecodeError):
                    raise InvalidResponse("response contains invalid JSON") from None
                if validator:
                    validator(data)
                emit(name, "pass", f"HTTP {expected} and Team 102 response contract matched")
                return data
        except InvalidResponse as exc:
            emit(name, "fail", str(exc))
        except httpx.TimeoutException:
            emit(name, "error", "request timed out")
        except (httpx.RequestError, httpx.DecodingError):
            emit(name, "error", "transport or decoding failure")
        return None

    def require(condition, message):
        if not condition:
            raise InvalidResponse(message)

    def check_unauthorized(data):
        require(_object(data) and isinstance(data.get("error"), str) and data["error"],
                "401 must return an error object")
        require("events" not in data and "logs" not in data,
                "unauthorized response must not return event/log data")

    def check_error(data):
        require(_object(data) and isinstance(data.get("error"), str) and data["error"],
                "400 must return an error object")
        require("events" not in data and "reports" not in data,
                "invalid query must not return result data")

    def check_events(data):
        events = _envelope(data, "events")
        for item in events:
            require(_has_keys(item, ("event_id", "ts", "source")), "event missing required fields")

    def check_logs(data):
        logs = _envelope(data, "logs")
        for item in logs:
            require(_has_keys(item, ("event_id", "raw_id", "ts", "source", "host",
                                     "client_ip", "username", "text")),
                    "log entry missing required fields")
            require(isinstance(item["text"], str), "log text must be a string")

    def check_reports(data):
        nonlocal sample_id
        reports = _envelope(data, "reports")
        for item in reports:
            require(_has_keys(item, ("report_id", "body", "cluster_id", "generated_at")),
                    "report list entry missing required fields")
            require(type(item["report_id"]) is int and item["report_id"] > 0 and _object(item["body"]),
                    "report list entry has invalid id/body")
        if reports:
            sample_id = reports[0]["report_id"]

    def check_detail(data):
        require(_has_keys(data, ("report_id", "body", "cluster_id", "generated_at")),
                "report detail missing required fields")
        require(data["report_id"] == sample_id and _object(data["body"]),
                "report detail id/body differs from list contract")

    def check_integrity(data):
        require(_object(data) and _object(data.get("batches")) and _object(data.get("audit")),
                "integrity response missing batches/audit objects")
        require(data["batches"].get("status") == "intact" and data["audit"].get("status") == "intact",
                "batch or audit chain did not verify intact")

    with httpx.Client(timeout=12, follow_redirects=False, trust_env=False,
                      transport=transport, headers={"Accept": "application/json", "Accept-Encoding": "identity"}) as client:
        attempt("health.ok", "/health", validator=lambda d: require(
            _object(d) and d.get("status") == "ok", "health response must contain status=ok"))
        attempt("auth.missing-key", "/v1/events", expected=401, validator=check_unauthorized)
        attempt("auth.invalid-key", "/v1/events", expected=401, validator=check_unauthorized,
                headers={"Authorization": "Bearer team105-invalid-" + uuid4().hex})
        attempt("events.list", "/v1/events", authenticated=True, params={"limit": 5}, validator=check_events)
        attempt("events.invalid-limit", "/v1/events", authenticated=True, expected=400,
                params={"limit": 0}, validator=check_error)
        attempt("logs.list", "/v1/logs", authenticated=True, params={"limit": 5}, validator=check_logs)
        attempt("reports.list", "/v1/reports", authenticated=True, params={"limit": 5}, validator=check_reports)
        if not api_key:
            emit("reports.detail", "skip", "SEC450_KEY not configured; authenticated read not attempted")
        elif sample_id is None:
            emit("reports.detail", "skip", "No valid report sample available from reports.list")
        else:
            attempt("reports.detail", f"/v1/reports/{sample_id}", authenticated=True, validator=check_detail)
        attempt("reports.invalid-id", "/v1/reports/abc", authenticated=True, expected=400,
                validator=check_error)
        attempt("integrity.intact", "/v1/integrity", authenticated=True, validator=check_integrity)

    assert [r["test_id"] for r in results] == list(CHECKS)
    return {"recorded_at": datetime.now(timezone.utc).isoformat(),
            "target_base": BASE, "scope": "Team 102 read-only Query API; one sampled report; no writes",
            "counts": {name: Counter(r["outcome"] for r in results)[name] for name in ("pass", "fail", "skip", "error")},
            "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run bounded, read-only Team 102 API acceptance checks")
    parser.add_argument("--output", type=Path, default=Path(".venv/test-results/team102-readonly.json"))
    args = parser.parse_args(argv)
    local_env = dotenv_values(Path(__file__).resolve().with_name(".env"))
    key = os.environ.get("SEC450_KEY") or local_env.get("SEC450_KEY") or None
    result = run_checks(key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(args.output), "counts": result["counts"], "results": result["results"]}))
    return 1 if result["counts"]["fail"] or result["counts"]["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
