"""Deployed-control evidence is tested offline without source credentials."""

import asyncio
import gzip
import json
from copy import deepcopy

import httpx
import pytest

from course_acceptance import MAX_BYTES, TARGET_BASES, TESTER_BASES, run_acceptance
from tester_interfaces import ProtocolFailure

SID = "synthetic-acceptance-session"
ERROR = {"message": "Synthetic lifecycle conflict", "source": "tester"}


def report(target=TARGET_BASES[0]):
    unknown = {"covered": None, "total": None, "basis": "Not instrumented"}
    return {
        "session_id": SID,
        "target_url": target,
        "results": [
            {"test_id": "synthetic.pass", "outcome": "pass", "message": "Checked"},
            {
                "test_id": "synthetic.skip",
                "outcome": "skip",
                "message": "Unagreed fixture",
            },
        ],
        "coverage": {name: dict(unknown) for name in ("code", "component", "system")},
    }


def scripted(responses):
    requests = []

    def handle(request):
        requests.append(request)
        index = len(requests) - 1
        assert index < len(responses), "An unexpected additional request was made"
        response = responses[index]
        if isinstance(response, httpx.Response):
            return response
        status, body = response
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handle), requests


def lifecycle(saved=None, *, before=409, run_status=200, run_body=None, again=None):
    saved = report() if saved is None else saved
    return [
        (201, {"session_id": SID}),
        (before, ERROR),
        (run_status, saved if run_body is None else run_body),
        (200, saved),
        (409, ERROR),
        (200, saved if again is None else again),
    ]


def invoke(transport, *, tester=TESTER_BASES[0], target=TARGET_BASES[0], local=False):
    return asyncio.run(run_acceptance(tester, target, local=local, transport=transport))


def test_successful_control_walkthrough_retains_qualified_evidence_without_auth(
    monkeypatch,
):
    private = "SEC450_SYNTHETIC_CREDENTIAL_MUST_NOT_BE_FORWARDED"
    monkeypatch.setenv("SEC450_KEY", private)
    monkeypatch.setenv("HTTPS_PROXY", "http://unreachable.synthetic.example:1234")
    transport, requests = scripted(lifecycle())
    evidence = invoke(
        transport, tester=TESTER_BASES[0].upper().replace("/TESTER", "/tester") + "/"
    )
    assert evidence["tester_base"] == TESTER_BASES[0]
    assert evidence["target_base"] == TARGET_BASES[0]
    assert evidence["evidence_kind"] == "deployed_http"
    assert evidence["counts"] == {"pass": 1, "fail": 0, "skip": 1, "error": 0}
    assert evidence["conformance_established"] is False
    assert evidence["recorded_at"]
    assert evidence["report"] == report()
    assert len(evidence["control_checks"]) == 4
    assert all(row["pass"] for row in evidence["control_checks"])
    assert [(row.method, row.url.path) for row in requests] == [
        ("POST", "/tester/test/setup"),
        ("GET", "/tester/test/report"),
        ("POST", "/tester/test/run"),
        ("GET", "/tester/test/report"),
        ("POST", "/tester/test/run"),
        ("GET", "/tester/test/report"),
    ]
    assert json.loads(requests[0].content) == {"target_url": TARGET_BASES[0]}
    assert json.loads(requests[2].content) == {"session_id": SID}
    assert json.loads(requests[4].content) == {"session_id": SID}
    assert all(
        row.url.params["session_id"] == SID for row in requests if row.method == "GET"
    )
    assert all("authorization" not in row.headers for row in requests)
    assert private not in json.dumps(evidence)
    assert all(private not in str(row.headers) for row in requests)


@pytest.mark.parametrize(
    ("tester", "target", "local"),
    [
        ("https://unapproved.example/tester", TARGET_BASES[0], False),
        (TESTER_BASES[0], "https://unapproved.example/system", False),
        (TESTER_BASES[0], TARGET_BASES[0] + "/child", False),
        (TESTER_BASES[0] + "?token=synthetic", TARGET_BASES[0], False),
        ("http://127.0.0.1:8002/tester", "http://127.0.0.1:8002/system", False),
        ("http://localhost:8002/tester", "http://127.0.0.1:8002/system", True),
        ("http://127.0.0.1:8003/tester", "http://127.0.0.1:8002/system", True),
    ],
)
def test_unapproved_configuration_fails_before_any_request(tester, target, local):
    transport, requests = scripted([])
    with pytest.raises(ProtocolFailure) as failure:
        invoke(transport, tester=tester, target=target, local=local)
    assert failure.value.status_code == 400
    assert requests == []


def test_explicit_local_mode_accepts_only_fixed_loopback_bases():
    target = "http://127.0.0.1:8002/system"
    transport, requests = scripted(lifecycle(saved=report(target)))
    evidence = invoke(
        transport, tester="http://127.0.0.1:8002/tester", target=target, local=True
    )
    assert evidence["evidence_kind"] == "local_tcp"
    assert all(row["pass"] for row in evidence["control_checks"])
    assert {row.url.host for row in requests} == {"127.0.0.1"}
    assert all("authorization" not in row.headers for row in requests)


@pytest.mark.parametrize("setup_status", [400, 404, 409, 500, 503])
def test_setup_failure_never_triggers_a_run(setup_status):
    transport, requests = scripted([(setup_status, ERROR)])
    with pytest.raises(ValueError, match="no run was requested"):
        invoke(transport)
    assert len(requests) == 1 and requests[0].url.path.endswith("/test/setup")


@pytest.mark.parametrize(
    "body", [{}, {"session_id": " "}, {"session_id": SID, "extra": True}]
)
def test_invalid_setup_receipt_never_triggers_a_run(body):
    transport, requests = scripted([(201, body)])
    with pytest.raises(ValueError):
        invoke(transport)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://unapproved.example"}),
        httpx.Response(
            201, content=gzip.compress(b"{}"), headers={"content-encoding": "gzip"}
        ),
        httpx.Response(201, content=b"x" * (MAX_BYTES + 1)),
    ],
)
def test_redirect_compression_and_oversize_are_rejected_without_followup(response):
    transport, requests = scripted([response])
    with pytest.raises(ValueError):
        invoke(transport)
    assert len(requests) == 1


def test_abort_preserves_saved_report_and_records_failed_run_control():
    saved = report()
    saved["results"][0] = {
        "test_id": "synthetic.interrupted",
        "outcome": "error",
        "message": "Synthetic interrupted case",
        "source": "tester",
    }
    transport, requests = scripted(lifecycle(saved, run_status=500, run_body=ERROR))
    evidence = invoke(transport)
    assert evidence["report"] == saved
    assert evidence["counts"] == {"pass": 0, "fail": 0, "skip": 1, "error": 1}
    checks = {row["check"]: row for row in evidence["control_checks"]}
    assert checks["run_and_saved_report"]["actual"] == 500
    assert checks["run_and_saved_report"]["pass"] is False
    assert checks["repeat_run"]["pass"] is True
    assert checks["report_immutable"]["pass"] is True
    assert len(requests) == 6


@pytest.mark.parametrize("field", ["session_id", "target_url"])
def test_saved_report_for_a_different_session_or_target_is_rejected(field):
    saved = report()
    saved[field] = "different-session" if field == "session_id" else TARGET_BASES[1]
    transport, requests = scripted(lifecycle(saved))
    with pytest.raises(ValueError, match="does not match"):
        invoke(transport)
    assert len(requests) == 4


def test_mutated_saved_report_is_recorded_as_control_failure():
    changed = report()
    changed["results"][0]["message"] = "Changed after retrieval"
    transport, requests = scripted(lifecycle(again=changed))
    evidence = invoke(transport)
    checks = {row["check"]: row for row in evidence["control_checks"]}
    assert checks["run_and_saved_report"]["pass"] is True
    assert checks["report_immutable"]["pass"] is False
    assert evidence["report"] == report()
    assert len(requests) == 6


def test_run_response_differing_from_saved_report_is_control_failure():
    changed = report()
    changed["results"][0]["outcome"] = "fail"
    transport, requests = scripted(lifecycle(run_body=changed))
    evidence = invoke(transport)
    checks = {row["check"]: row for row in evidence["control_checks"]}
    assert checks["run_and_saved_report"]["pass"] is False
    assert checks["report_immutable"]["pass"] is True
    assert evidence["report"] == report()
    assert len(requests) == 6


def test_missing_saved_report_after_run_is_rejected_without_repeating_run():
    responses = lifecycle()
    responses[3] = (404, ERROR)
    transport, requests = scripted(responses)
    with pytest.raises(ValueError, match="no saved report"):
        invoke(transport)
    assert len(requests) == 4


def test_unmeasured_coverage_and_duplicate_test_ids_are_not_silently_accepted():
    invalid = report()
    invalid["coverage"]["code"] = {
        "covered": 1,
        "total": None,
        "basis": "Invalid unmatched measurement",
    }
    invalid["results"].append(deepcopy(invalid["results"][0]))
    transport, requests = scripted(lifecycle(invalid))
    with pytest.raises(ValueError):
        invoke(transport)
    assert len(requests) == 4
