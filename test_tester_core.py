"""Lifecycle checks use injected adapters; no project or network oracle."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from tester_core import TesterCoordinator as Coordinator
from tester_interfaces import ProtocolFailure, SuiteCase
from testing_protocol import CoverageMeasure, TestResult as CaseResult


TARGET = "https://approved.example.invalid"


class Gateway:
    def __init__(self):
        self.prepared = []
        self.coverage_calls = []
        self.coverage = CoverageMeasure(covered=3, total=5, basis="Measured fixture lines")
        self.setup_failure = None
        self.coverage_failure = None
        self.echo = None

    async def prepare(self, target_url, session_id):
        self.prepared.append((target_url, session_id))
        if self.setup_failure:
            raise self.setup_failure
        return session_id if self.echo is None else self.echo

    async def code_coverage(self, target_url, session_id):
        self.coverage_calls.append((target_url, session_id))
        if self.coverage_failure:
            raise self.coverage_failure
        return self.coverage


def case(test_id, outcome="pass", message="Fixture assertion completed"):
    async def execute(context):
        return CaseResult(test_id=test_id, outcome=outcome, message=message)

    return SuiteCase(test_id, execute)


def assert_failure(failure, status, source="tester"):
    assert failure.value.status_code == status
    assert failure.value.source == source
    assert failure.value.message.strip()


def test_unconfigured_setup_fails_before_gateway_or_session_creation():
    gateway = Gateway()
    for coordinator in (Coordinator(), Coordinator(gateway=gateway)):
        assert coordinator.configured is False
        with pytest.raises(ProtocolFailure) as failure:
            asyncio.run(coordinator.setup(TARGET))
        assert_failure(failure, 500)
        with pytest.raises(ProtocolFailure) as failure:
            asyncio.run(coordinator.run("unknown"))
        assert_failure(failure, 404)
    assert gateway.prepared == []


@pytest.mark.parametrize("cases", [(), (case(""),), (case(" \t"),), (case("a"), case("a"))])
def test_constructor_rejects_empty_or_invalid_case_inventory(cases):
    with pytest.raises(ValueError):
        Coordinator(Gateway(), cases)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_constructor_rejects_unbounded_or_invalid_timeouts(timeout):
    with pytest.raises(ValueError):
        Coordinator(setup_timeout=timeout)
    with pytest.raises(ValueError):
        Coordinator(run_timeout=timeout)


def test_normal_run_preserves_order_shared_state_and_known_errors():
    calls = []

    async def first(context):
        calls.append(("first", context.target_url, context.session_id))
        context.state["fixture"] = "created"
        return CaseResult(test_id="first", outcome="pass", message="Created fixture")

    async def negative(context):
        calls.append(("negative", context.target_url, context.session_id))
        assert context.state["fixture"] == "created"
        return CaseResult(test_id="negative", outcome="fail", message="Expected assertion failed")

    async def known_error(context):
        calls.append(("known", context.target_url, context.session_id))
        raise ProtocolFailure(503, "Target temporarily unavailable", "system")

    gateway = Gateway()
    coordinator = Coordinator(
        gateway,
        (SuiteCase("first", first), SuiteCase("negative", negative), SuiteCase("known", known_error)),
    )
    assert coordinator.configured is True
    session_id = asyncio.run(coordinator.setup(TARGET)).session_id
    report = asyncio.run(coordinator.run(session_id))
    assert [(result.test_id, result.outcome) for result in report.results] == [
        ("first", "pass"), ("negative", "fail"), ("known", "error"),
    ]
    assert report.results[2].source == "system"
    assert report.results[2].message == "Target temporarily unavailable"
    assert calls == [(name, TARGET, session_id) for name in ("first", "negative", "known")]
    assert gateway.prepared == [(TARGET, session_id)]
    assert gateway.coverage_calls == [(TARGET, session_id)]
    assert report.coverage.code.model_dump() == gateway.coverage.model_dump()
    for measurement in (report.coverage.component, report.coverage.system):
        assert measurement.covered is measurement.total is None
        assert "inventory" in measurement.basis
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.run(session_id))
    assert_failure(failure, 409)


@pytest.mark.parametrize("status,source", [(400, "system"), (503, "communication"), (500, "unknown")])
def test_classified_setup_errors_are_preserved_and_create_no_ready_session(status, source):
    gateway = Gateway()
    expected = ProtocolFailure(status, "Classified fixture setup failure", source)
    gateway.setup_failure = expected
    coordinator = Coordinator(gateway, (case("a"),))
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.setup(TARGET))
    assert failure.value is expected
    with pytest.raises(ProtocolFailure) as missing:
        coordinator.report(gateway.prepared[0][1])
    assert_failure(missing, 404)
    assert gateway.coverage_calls == []


def test_setup_echo_must_match_and_internal_errors_are_sanitized():
    gateway = Gateway()
    gateway.echo = "another-session"
    coordinator = Coordinator(gateway, (case("a"),))
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.setup(TARGET))
    assert_failure(failure, 500, "system")
    with pytest.raises(ProtocolFailure) as failure:
        coordinator.report(gateway.prepared[-1][1])
    assert_failure(failure, 404)
    gateway.echo = None
    gateway.setup_failure = RuntimeError("secret fixture credentials")
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.setup(TARGET))
    assert_failure(failure, 500)
    assert "secret" not in failure.value.message


def test_setup_timeout_is_communication_failure_and_creates_no_ready_session():
    class WaitingGateway(Gateway):
        async def prepare(self, target_url, session_id):
            self.prepared.append((target_url, session_id))
            await asyncio.Event().wait()

    gateway = WaitingGateway()
    coordinator = Coordinator(gateway, (case("a"),), setup_timeout=0.02)
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.setup(TARGET))
    assert_failure(failure, 500, "communication")
    with pytest.raises(ProtocolFailure) as missing:
        coordinator.report(gateway.prepared[0][1])
    assert_failure(missing, 404)


def test_unknown_and_ready_sessions_have_no_report():
    coordinator = Coordinator(Gateway(), (case("a"),))
    with pytest.raises(ProtocolFailure) as failure:
        coordinator.report("unknown")
    assert_failure(failure, 404)
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.run("unknown"))
    assert_failure(failure, 404)
    session_id = asyncio.run(coordinator.setup(TARGET)).session_id
    with pytest.raises(ProtocolFailure) as failure:
        coordinator.report(session_id)
    assert_failure(failure, 409)


def test_concurrent_request_threads_claim_one_run_without_waiting_for_adapter():
    entered, release = Event(), Event()
    calls = []

    async def blocked(context):
        calls.append(context.session_id)
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        return CaseResult(test_id="blocked", outcome="pass", message="Released fixture")

    gateway = Gateway()
    coordinator = Coordinator(gateway, (SuiteCase("blocked", blocked),))
    session_id = asyncio.run(coordinator.setup(TARGET)).session_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lambda: asyncio.run(coordinator.run(session_id)))
        try:
            assert entered.wait(5)
            with pytest.raises(ProtocolFailure) as failure:
                pool.submit(lambda: asyncio.run(coordinator.run(session_id))).result(timeout=5)
            assert_failure(failure, 409)
            with pytest.raises(ProtocolFailure) as failure:
                coordinator.report(session_id)
            assert_failure(failure, 409)
        finally:
            release.set()
        assert first.result(timeout=5).results[0].outcome == "pass"
    assert calls == [session_id]
    assert gateway.coverage_calls == [(TARGET, session_id)]


def test_completed_json_snapshot_cannot_be_changed_through_adapter_or_responses():
    adapter_result = CaseResult(test_id="a", outcome="pass", message="Original result")

    async def execute(context):
        return adapter_result

    gateway = Gateway()
    coordinator = Coordinator(gateway, (SuiteCase("a", execute),))
    session_id = asyncio.run(coordinator.setup(TARGET)).session_id
    returned = asyncio.run(coordinator.run(session_id))
    original = returned.model_dump(mode="json")
    returned.results[0].message = "Changed response"
    returned.results.clear()
    returned.coverage.code.covered = 999
    adapter_result.message = "Changed adapter"
    gateway.coverage.basis = "Changed coverage adapter"
    fetched = coordinator.report(session_id)
    assert fetched.model_dump(mode="json") == original
    fetched.coverage.component.basis = "Changed fetched response"
    assert coordinator.report(session_id).model_dump(mode="json") == original


@pytest.mark.parametrize("source", ["system", "communication", "unknown"])
def test_classified_coverage_failure_keeps_report_with_unknown_counts(source):
    gateway = Gateway()
    gateway.coverage_failure = ProtocolFailure(503, "Coverage fixture unavailable", source)
    coordinator = Coordinator(gateway, (case("a"),))
    session_id = asyncio.run(coordinator.setup(TARGET)).session_id
    report = asyncio.run(coordinator.run(session_id))
    assert report.results[0].outcome == "pass"
    assert report.coverage.code.covered is report.coverage.code.total is None
    assert source in report.coverage.code.basis
    assert "Coverage fixture unavailable" in report.coverage.code.basis


@pytest.mark.parametrize("failure_kind", ["exception", "wrong-id", "missing-result", "invalid-model"])
def test_abort_preserves_partial_results_and_fills_every_remaining_case(failure_kind):
    calls = []

    async def broken(context):
        calls.append("broken")
        if failure_kind == "exception":
            raise RuntimeError("private adapter detail")
        if failure_kind == "wrong-id":
            return CaseResult(test_id="different", outcome="pass", message="Wrong case")
        if failure_kind == "invalid-model":
            return CaseResult.model_construct(test_id="broken", outcome="invalid", message="Invalid")
        return None

    async def skipped(context):
        calls.append("skipped")
        return CaseResult(test_id="last", outcome="pass", message="Should never run")

    gateway = Gateway()
    coordinator = Coordinator(
        gateway, (case("first"), SuiteCase("broken", broken), SuiteCase("last", skipped))
    )
    session_id = asyncio.run(coordinator.setup(TARGET)).session_id
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.run(session_id))
    assert_failure(failure, 500)
    assert "private" not in failure.value.message
    report = coordinator.report(session_id)
    assert [(result.test_id, result.outcome) for result in report.results] == [
        ("first", "pass"), ("broken", "error"), ("last", "skip"),
    ]
    assert report.results[1].source == "tester"
    assert report.coverage.code.covered is report.coverage.code.total is None
    assert report.coverage.code.basis.strip()
    assert calls == ["broken"]
    assert gateway.coverage_calls == []
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.run(session_id))
    assert_failure(failure, 409)
    assert coordinator.report(session_id).model_dump() == report.model_dump()


def test_internal_coverage_failure_freezes_completed_case_results_before_raising():
    gateway = Gateway()
    gateway.coverage_failure = RuntimeError("private coverage secret")
    coordinator = Coordinator(gateway, (case("a"), case("b", "fail")))
    session_id = asyncio.run(coordinator.setup(TARGET)).session_id
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.run(session_id))
    assert_failure(failure, 500)
    report = coordinator.report(session_id)
    assert [result.outcome for result in report.results] == ["pass", "fail"]
    assert "private" not in report.coverage.code.basis
    assert report.coverage.code.covered is report.coverage.code.total is None


def test_run_timeout_freezes_partial_report_and_blocks_retry():
    async def blocked(context):
        await asyncio.Event().wait()

    gateway = Gateway()
    coordinator = Coordinator(
        gateway, (case("first"), SuiteCase("blocked", blocked), case("last")), run_timeout=0.02
    )
    session_id = asyncio.run(coordinator.setup(TARGET)).session_id
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.run(session_id))
    assert_failure(failure, 500)
    report = coordinator.report(session_id)
    assert [result.outcome for result in report.results] == ["pass", "error", "skip"]
    assert "timeout" in report.results[1].message
    with pytest.raises(ProtocolFailure) as failure:
        asyncio.run(coordinator.run(session_id))
    assert_failure(failure, 409)
    assert gateway.coverage_calls == []


def test_cancellation_freezes_partial_report_before_propagating_cancellation():
    async def scenario():
        entered = asyncio.Event()

        async def blocked(context):
            entered.set()
            await asyncio.Event().wait()

        coordinator = Coordinator(
            Gateway(), (case("first"), SuiteCase("blocked", blocked), case("last"))
        )
        session_id = (await coordinator.setup(TARGET)).session_id
        running = asyncio.create_task(coordinator.run(session_id))
        await entered.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        report = coordinator.report(session_id)
        assert [result.outcome for result in report.results] == ["pass", "error", "skip"]
        assert "cancelled" in report.results[1].message
        with pytest.raises(ProtocolFailure) as failure:
            await coordinator.run(session_id)
        assert_failure(failure, 409)

    asyncio.run(scenario())


def test_cancellation_before_first_case_invocation_marks_all_cases_skipped(monkeypatch):
    async def scenario():
        execution_scheduled = asyncio.Event()
        release_execution = asyncio.Event()
        invoked = []

        async def execute(context):
            invoked.append(context.session_id)
            return CaseResult(test_id="first", outcome="pass", message="Invoked fixture")

        gateway = Gateway()
        coordinator = Coordinator(gateway, (SuiteCase("first", execute), case("last")))
        session_id = (await coordinator.setup(TARGET)).session_id
        original_execute = coordinator._execute

        async def gated_execute(context, progress):
            # Hold the task scheduled by wait_for before it invokes any case.
            execution_scheduled.set()
            await release_execution.wait()
            return await original_execute(context, progress)

        monkeypatch.setattr(coordinator, "_execute", gated_execute)
        running = asyncio.create_task(coordinator.run(session_id))
        await execution_scheduled.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        report = coordinator.report(session_id)
        assert [result.outcome for result in report.results] == ["skip", "skip"]
        assert all(result.source is None for result in report.results)
        assert invoked == []
        assert gateway.coverage_calls == []
        assert report.coverage.code.covered is report.coverage.code.total is None
        with pytest.raises(ProtocolFailure) as failure:
            await coordinator.run(session_id)
        assert_failure(failure, 409)

    asyncio.run(scenario())
