"""Run the deployed course tester controls and save reproducible JSON evidence.

This invokes an existing tester. It never calls a product reset directly and
never reads source API credentials. Target mutation policy belongs to that
tester's configured suite. Local mode is an explicit operator choice.
"""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

import httpx

from tester_http import PublicHTTPTransport
from tester_targets import ApprovedTargets
from testing_protocol import SessionResponse, SuiteReport


ORIGIN = "https://sec450-incident-api.onrender.com"
TESTER_BASES = (ORIGIN + "/tester", "https://seclog.redinbo.win/tester")
TARGET_BASES = (ORIGIN + "/system", "https://seclog.redinbo.win")
MAX_BYTES = 2 * 1024 * 1024


async def run_acceptance(tester_base, target_base, *, local=False, transport=None):
    """Return schema-checked evidence; injectable transport is for offline tests."""
    testers = (*TESTER_BASES, *(("http://127.0.0.1:8002/tester",) if local else ()))
    targets = (*TARGET_BASES, *(("http://127.0.0.1:8002/system",) if local else ()))
    tester_base = ApprovedTargets(testers, allow_loopback=local).normalize_and_require(tester_base)
    target_base = ApprovedTargets(targets, allow_loopback=local).normalize_and_require(target_base)
    transport = transport or PublicHTTPTransport(allowed_loopback=("127.0.0.1",) if local else ())
    checks = []

    async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                 timeout=120, headers={"Accept": "application/json", "Accept-Encoding": "identity"}) as client:
        async def request(method, path, **kwargs):
            async with asyncio.timeout(125):
                async with client.stream(method, tester_base + path, **kwargs) as response:
                    if 300 <= response.status_code < 400:
                        raise ValueError("Tester redirects are not accepted")
                    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                        raise ValueError("Compressed tester responses are not accepted")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_BYTES:
                            raise ValueError("Tester response exceeds evidence size limit")
                        body.extend(chunk)
                    return response.status_code, bytes(body)

        status, body = await request("POST", "/test/setup", json={"target_url": target_base})
        if status != 201:
            raise ValueError("Tester setup did not return 201; no run was requested")
        session = SessionResponse.model_validate_json(body).session_id
        status, _ = await request("GET", "/test/report", params={"session_id": session})
        checks.append({"check": "report_before_run", "expected": 409, "actual": status, "pass": status == 409})
        run_status, run_body = await request("POST", "/test/run", json={"session_id": session})
        status, saved_body = await request("GET", "/test/report", params={"session_id": session})
        if status != 200:
            raise ValueError("Tester has no saved report after the run")
        report = SuiteReport.model_validate_json(saved_body)
        if report.session_id != session or report.target_url != target_base:
            raise ValueError("Saved report does not match the requested session/target")
        same = run_status == 200 and SuiteReport.model_validate_json(run_body) == report
        checks.append({"check": "run_and_saved_report", "expected": 200, "actual": run_status, "pass": same})
        status, _ = await request("POST", "/test/run", json={"session_id": session})
        checks.append({"check": "repeat_run", "expected": 409, "actual": status, "pass": status == 409})
        status, again_body = await request("GET", "/test/report", params={"session_id": session})
        stable = status == 200 and SuiteReport.model_validate_json(again_body) == report
        checks.append({"check": "report_immutable", "expected": 200, "actual": status, "pass": stable})
    counts = {name: 0 for name in ("pass", "fail", "skip", "error")}
    counts.update(Counter(result.outcome for result in report.results))
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "evidence_kind": "local_tcp" if local else "deployed_http",
        "tester_base": tester_base, "target_base": target_base,
        "control_checks": checks, "counts": counts,
        "conformance_established": False,
        "qualification": "Observed checks only; skipped scope/fixtures/labels and unknown coverage remain unresolved.",
        "report": report.model_dump(mode="json"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tester-base", default=TESTER_BASES[0])
    parser.add_argument("--target-base", default=TARGET_BASES[0])
    parser.add_argument("--local", action="store_true", help="Use the fixed loopback delivery on port 8002")
    parser.add_argument("--output", type=Path, default=Path(".team-local/acceptance/latest.json"))
    args = parser.parse_args(argv)
    if args.local:
        args.tester_base, args.target_base = "http://127.0.0.1:8002/tester", "http://127.0.0.1:8002/system"
    try:
        evidence = asyncio.run(run_acceptance(args.tester_base, args.target_base, local=args.local))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    except Exception:
        print("Acceptance could not complete; verify the selected deployment and its tester configuration. No error payload or credentials were printed.")
        return 2
    print(json.dumps({"evidence": str(args.output), "counts": evidence["counts"],
                      "control_checks_passed": sum(check["pass"] for check in evidence["control_checks"]),
                      "control_checks_total": len(evidence["control_checks"])}))
    return int(evidence["counts"]["fail"] > 0 or evidence["counts"]["error"] > 0
               or not all(check["pass"] for check in evidence["control_checks"]))


if __name__ == "__main__":
    raise SystemExit(main())
