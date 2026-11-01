"""Process-local tester lifecycle with injected, trusted suite adapters.

Sessions and their final JSON snapshots live for this coordinator's lifetime.
They are not durable across process restarts or shared between workers.
"""

import asyncio
from dataclasses import dataclass, field
import math
from threading import Lock
from uuid import uuid4

from tester_interfaces import ProtocolFailure, SuiteCase, TargetGateway, TestContext
from testing_protocol import (
    CoverageMeasure,
    SessionResponse,
    SuiteCoverage,
    SuiteReport,
    TestResult,
)


@dataclass
class _Session:
    context: TestContext
    phase: str = "ready"
    report_json: str | None = None


@dataclass
class _Progress:
    results: list[TestResult] = field(default_factory=list)
    active_index: int | None = None


class _InvalidAdapterResult(Exception):
    """A trusted adapter violated its in-process interface."""


def _unknown_coverage(basis: str) -> CoverageMeasure:
    return CoverageMeasure(covered=None, total=None, basis=basis)


class TesterCoordinator:
    """Run each configured case once for each successfully prepared session.

    The short registry lock works across event loops and request threads. No
    adapter or case is awaited while holding it. URL authorization belongs to
    the facade and the trusted gateway, rather than this lifecycle engine.
    """

    def __init__(
        self,
        gateway: TargetGateway | None = None,
        cases: tuple[SuiteCase, ...] | None = None,
        setup_timeout: float = 10,
        run_timeout: float = 30,
    ):
        for name, value in (
            ("setup_timeout", setup_timeout),
            ("run_timeout", run_timeout),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a finite positive number")
        if cases is not None:
            cases = tuple(cases)
            if not cases:
                raise ValueError("A configured suite needs at least one case")
            ids = []
            for case in cases:
                if (
                    not isinstance(case, SuiteCase)
                    or not isinstance(case.test_id, str)
                    or not case.test_id.strip()
                    or not callable(case.run)
                ):
                    raise ValueError("Suite cases need a nonblank ID and callable run")
                ids.append(case.test_id)
            if len(ids) != len(set(ids)):
                raise ValueError("Suite case IDs must be unique")
        self._gateway = gateway
        self._cases = cases
        self._setup_timeout = setup_timeout
        self._run_timeout = run_timeout
        self._sessions: dict[str, _Session] = {}
        self._lock = Lock()

    @property
    def configured(self) -> bool:
        return self._gateway is not None and self._cases is not None

    def _require_configuration(self) -> None:
        if not self.configured:
            raise ProtocolFailure(
                500, "The tester has no configured gateway and test suite.", "tester"
            )

    async def setup(self, target_url: str) -> SessionResponse:
        self._require_configuration()
        session_id = str(uuid4())
        try:
            echoed_id = await asyncio.wait_for(
                self._gateway.prepare(target_url, session_id),
                timeout=self._setup_timeout,
            )
        except ProtocolFailure:
            raise
        except TimeoutError:
            raise ProtocolFailure(
                500, "The target setup request timed out.", "communication"
            ) from None
        except Exception:
            raise ProtocolFailure(
                500, "The tester could not prepare the target.", "tester"
            ) from None
        if echoed_id != session_id:
            raise ProtocolFailure(
                500, "The target did not echo the requested session ID.", "system"
            )
        session = _Session(TestContext(target_url, session_id))
        with self._lock:
            self._sessions[session_id] = session
        return SessionResponse(session_id=session_id)

    async def run(self, session_id: str) -> SuiteReport:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ProtocolFailure(404, "The tester session was not found.", "tester")
            if session.phase != "ready":
                raise ProtocolFailure(
                    409, "This tester session has already been claimed for a run.", "tester"
                )
            self._require_configuration()
            session.phase = "running"

        progress = _Progress()
        try:
            code = await asyncio.wait_for(
                self._execute(session.context, progress), timeout=self._run_timeout
            )
        except asyncio.CancelledError:
            self._abort(session, progress, "The test run was cancelled.")
            raise
        except TimeoutError:
            message = "The test run exceeded the configured timeout."
            self._abort(session, progress, message)
            raise ProtocolFailure(500, message, "tester") from None
        except Exception:
            message = "The tester could not complete the test run."
            self._abort(session, progress, message)
            raise ProtocolFailure(500, message, "tester") from None
        self._finish(session, progress.results, code, phase="completed")
        return self.report(session_id)

    async def _execute(
        self, context: TestContext, progress: _Progress
    ) -> CoverageMeasure:
        for index, case in enumerate(self._cases):
            progress.active_index = index
            try:
                result = await case.run(context)
            except ProtocolFailure as exc:
                result = TestResult(
                    test_id=case.test_id,
                    outcome="error",
                    message=exc.message,
                    source=exc.source,
                )
            if not isinstance(result, TestResult):
                raise _InvalidAdapterResult()
            # Revalidate even mutable/model_construct-created adapter models and
            # detach results before a later case can mutate an earlier result.
            result = TestResult.model_validate_json(result.model_dump_json())
            if result.test_id != case.test_id:
                raise _InvalidAdapterResult()
            progress.results.append(result)
            progress.active_index = None

        try:
            code = await self._gateway.code_coverage(
                context.target_url, context.session_id
            )
        except ProtocolFailure as exc:
            return _unknown_coverage(
                f"Code coverage is unavailable ({exc.source}): {exc.message}"
            )
        if not isinstance(code, CoverageMeasure):
            raise _InvalidAdapterResult()
        return CoverageMeasure.model_validate_json(code.model_dump_json())

    def _abort(self, session: _Session, progress: _Progress, message: str) -> None:
        results = list(progress.results)
        for index in range(len(results), len(self._cases)):
            current = index == progress.active_index
            results.append(
                TestResult(
                    test_id=self._cases[index].test_id,
                    outcome="error" if current else "skip",
                    message=(
                        message
                        if current
                        else "Skipped because the test run stopped before this case."
                    ),
                    source="tester" if current else None,
                )
            )
        self._finish(
            session,
            results,
            _unknown_coverage(
                "Code coverage is unavailable because the test run did not complete: "
                + message
            ),
            phase="aborted",
        )

    def _finish(
        self,
        session: _Session,
        results: list[TestResult],
        code: CoverageMeasure,
        *,
        phase: str,
    ) -> None:
        report = SuiteReport(
            session_id=session.context.session_id,
            target_url=session.context.target_url,
            results=results,
            coverage=SuiteCoverage(
                code=code,
                component=_unknown_coverage(
                    "Component coverage is unknown: the approved pod component "
                    "inventory and case mapping are pending."
                ),
                system=_unknown_coverage(
                    "System coverage is unknown: the approved pod system "
                    "inventory and case mapping are pending."
                ),
            ),
        )
        snapshot = report.model_dump_json()
        with self._lock:
            session.report_json = snapshot
            session.phase = phase

    def report(self, session_id: str) -> SuiteReport:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ProtocolFailure(404, "The tester session was not found.", "tester")
            snapshot = session.report_json
            if snapshot is None:
                raise ProtocolFailure(
                    409, "This tester session has no completed report yet.", "tester"
                )
        return SuiteReport.model_validate_json(snapshot)
