"""Adapter boundaries for the course tester; no project or network oracle."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from testing_protocol import CoverageMeasure, TestResult


class ProtocolFailure(Exception):
    """A classified target/communication error safe to return to a caller."""

    def __init__(self, status_code: int, message: str, source: str):
        if not 400 <= status_code <= 599:
            raise ValueError("Protocol failures need an HTTP error status")
        if not message.strip():
            raise ValueError("Protocol failures need a message")
        if source not in {"tester", "system", "communication", "unknown"}:
            raise ValueError("Invalid protocol failure source")
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.source = source


class TargetGateway(Protocol):
    """A trusted adapter, not a user-provided URL fetcher.

    prepare must require target HTTP 201 and return its echoed session ID.
    A future live adapter must enforce network address policy, redirects,
    response bounds and timeouts. This milestone supplies test doubles only.
    """

    async def prepare(self, target_url: str, session_id: str) -> str: ...

    async def code_coverage(self, target_url: str, session_id: str) -> CoverageMeasure: ...


@dataclass(frozen=True)
class TestContext:
    target_url: str
    session_id: str
    state: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SuiteCase:
    test_id: str
    run: Callable[[TestContext], Awaitable[TestResult]]
