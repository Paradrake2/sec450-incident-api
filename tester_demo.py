"""Local, synthetic course-tester walkthrough; no network target or database.

This demonstrates protocol orchestration, not incident API conformance.
Normal main:app configuration is untouched. All coverage remains unknown.
"""

import argparse
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tester_app import create_tester_app
from tester_core import TesterCoordinator
from tester_interfaces import SuiteCase
from testing_protocol import CoverageMeasure, TestResult


DEMO_TARGET = "https://synthetic.invalid/sec450"
SCENARIOS = ("pass", "fail", "abort")


class DemoGateway:
    """An in-memory substitute; never resolves or contacts the logical URL."""

    def __init__(self):
        self.prepared = set()

    async def prepare(self, target_url, session_id):
        self.prepared.add((target_url, session_id))
        return session_id

    async def code_coverage(self, target_url, session_id):
        return CoverageMeasure(
            covered=None, total=None,
            basis="Synthetic demo has no target code instrumentation; no coverage measured",
        )


def create_demo_app(scenario="pass") -> FastAPI:
    if scenario not in SCENARIOS:
        raise ValueError("Unknown demo scenario")
    gateway = DemoGateway()

    async def handshake(context):
        prepared = (context.target_url, context.session_id) in gateway.prepared
        return TestResult(
            test_id="DEMO-01", outcome="pass" if prepared else "fail",
            message="Synthetic session preparation checked",
        )

    async def synthetic_assertion(context):
        if scenario == "abort":
            raise RuntimeError("Synthetic private adapter detail must not enter the report")
        return TestResult(
            test_id="DEMO-02", outcome="fail" if scenario == "fail" else "pass",
            message=("Intentional synthetic assertion failure" if scenario == "fail"
                     else "Synthetic assertion passed"),
        )

    async def follow_up(context):
        return TestResult(
            test_id="DEMO-03", outcome="pass", message="Synthetic follow-up executed",
        )

    coordinator = TesterCoordinator(gateway=gateway, cases=(
        SuiteCase("DEMO-01", handshake),
        SuiteCase("DEMO-02", synthetic_assertion),
        SuiteCase("DEMO-03", follow_up),
    ))
    application = FastAPI(title="Local synthetic tester walkthrough")

    @application.get("/")
    async def overview():
        return {
            "mode": "synthetic_demo", "scenario": scenario,
            "target_url": DEMO_TARGET, "docs": "/tester/docs",
            "scope": "Tester lifecycle only; no real incident API or cross-team conformance checks",
            "storage": "Memory only; sessions disappear on restart",
            "coverage": "Unknown; no measurements or pod inventories supplied",
        }

    application.mount("/tester", create_tester_app(coordinator, approved_targets=(DEMO_TARGET,)))
    return application


def walkthrough(scenario="pass") -> dict:
    """Exercise the HTTP facade and verify saved success/failure/abort behavior."""
    trace = []

    def checked(response, expected, step):
        if response.status_code != expected:
            raise RuntimeError(f"Demo {step} returned {response.status_code}; expected {expected}")
        trace.append({"step": step, "status": response.status_code})
        return response.json()

    with TestClient(create_demo_app(scenario)) as client:
        setup = checked(client.post("/tester/test/setup", json={"target_url": DEMO_TARGET}), 201, "setup")
        session_id = setup["session_id"]
        checked(client.get("/tester/test/report", params={"session_id": session_id}), 409, "report_before_run")
        expected_run_status = 500 if scenario == "abort" else 200
        checked(client.post("/tester/test/run", json={"session_id": session_id}), expected_run_status, "run")
        report = checked(client.get("/tester/test/report", params={"session_id": session_id}), 200, "saved_report")
        expected = {
            "pass": ["pass", "pass", "pass"],
            "fail": ["pass", "fail", "pass"],
            "abort": ["pass", "error", "skip"],
        }[scenario]
        if [result["outcome"] for result in report["results"]] != expected:
            raise RuntimeError("Demo report did not preserve the expected outcomes")
        checked(client.post("/tester/test/run", json={"session_id": session_id}), 409, "repeat_run")
        again = checked(client.get("/tester/test/report", params={"session_id": session_id}), 200, "unchanged_report")
        if again != report:
            raise RuntimeError("Demo saved report changed after retrieval or repeat run")
        return {"mode": "synthetic_demo", "scenario": scenario, "verified": True, "steps": trace, "report": report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="pass")
    parser.add_argument("--serve", action="store_true", help="Serve the synthetic demo on loopback port 8001")
    args = parser.parse_args(argv)
    if args.serve:
        import uvicorn
        uvicorn.run(create_demo_app(args.scenario), host="127.0.0.1", port=8001, workers=1)
    else:
        print(json.dumps(walkthrough(args.scenario), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
