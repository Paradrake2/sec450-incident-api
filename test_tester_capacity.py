"""Admission limits retain reports and reserve in-flight setup slots."""

import asyncio

import pytest

from tester_core import TesterCoordinator as Coordinator
from tester_interfaces import ProtocolFailure, SuiteCase
from testing_protocol import CoverageMeasure, TestResult as CaseResult


TARGET = "https://approved.example"


async def passed(context):
    return CaseResult(test_id="one", outcome="pass", message="Completed")


class Gateway:
    def __init__(self):
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.wait = False
        self.failure = False

    async def prepare(self, target, session_id):
        self.calls += 1
        self.entered.set()
        if self.wait:
            await self.release.wait()
        if self.failure:
            raise ProtocolFailure(500, "Target unavailable", "communication")
        return session_id

    async def code_coverage(self, target, session_id):
        return CoverageMeasure(covered=None, total=None, basis="Not measured")


def coordinator(gateway, maximum=1):
    return Coordinator(gateway, (SuiteCase("one", passed),), max_sessions=maximum)


@pytest.mark.parametrize("maximum", [True, False, 0, -1, 1.5, "2", None])
def test_invalid_capacity_is_rejected(maximum):
    with pytest.raises(ValueError, match="max_sessions"):
        coordinator(Gateway(), maximum)


def test_completed_report_is_retained_when_capacity_rejects_setup():
    async def scenario():
        gateway = Gateway()
        service = coordinator(gateway)
        session = await service.setup(TARGET)
        report = await service.run(session.session_id)
        with pytest.raises(ProtocolFailure) as failure:
            await service.setup(TARGET)
        assert failure.value.status_code == 503
        assert failure.value.source == "tester"
        assert gateway.calls == 1
        assert service.report(session.session_id) == report

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_inflight_setup_reserves_capacity_and_failure_or_cancel_releases_it(cancel):
    async def scenario():
        gateway = Gateway()
        gateway.wait = True
        gateway.failure = not cancel
        service = coordinator(gateway)
        task = asyncio.create_task(service.setup(TARGET))
        await gateway.entered.wait()
        with pytest.raises(ProtocolFailure) as full:
            await service.setup(TARGET)
        assert full.value.status_code == 503
        assert gateway.calls == 1
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            gateway.release.set()
            with pytest.raises(ProtocolFailure):
                await task
        gateway.wait = gateway.failure = False
        replacement = await service.setup(TARGET)
        assert replacement.session_id
        assert gateway.calls == 2

    asyncio.run(scenario())
