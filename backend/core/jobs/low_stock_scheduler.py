"""Optional in-process scheduler for the read-only low-stock job."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from backend.core import logger
from backend.core.jobs.low_stock_job import run_low_stock_check

logging = logger(__name__)

LowStockRunner = Callable[[str], Awaitable[dict[str, int]]]


class LowStockScheduler:
    """Run low-stock counts on a cancellation-safe fixed-delay schedule."""

    def __init__(
        self,
        *,
        interval_seconds: float,
        run_immediately: bool,
        runner: LowStockRunner | None = None,
    ) -> None:
        """Configure an in-process low-stock scheduler.

        Production settings enforce a 60-second minimum; accepting smaller
        positive values here keeps the lifecycle independently testable.

        Args:
            interval_seconds: Delay between the end of one run and the next run.
            run_immediately: Whether to run once before the first delay.
            runner: Optional injected low-stock job implementation.

        Raises:
            ValueError: If the interval is not positive.
        """
        if interval_seconds <= 0:
            raise ValueError("Low-stock scheduler interval must be positive")
        self._interval_seconds = interval_seconds
        self._run_immediately = run_immediately
        self._runner = runner or run_low_stock_check
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """Report whether the background loop is active.

        Returns:
            True while the scheduler task exists and has not completed.
        """
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the scheduler once on the current event loop.

        Repeated starts while the loop is active are safe no-ops.
        """
        if self.is_running:
            return
        logging.info(
            "Starting low-stock scheduler interval_seconds=%s run_immediately=%s",
            self._interval_seconds,
            self._run_immediately,
        )
        self._task = asyncio.create_task(
            self._run_loop(),
            name="low-stock-scheduler",
        )

    async def stop(self) -> None:
        """Cancel and await the scheduler background task.

        Shutdown cancellation is treated as an expected lifecycle event.
        """
        task = self._task
        if task is None:
            return
        self._task = None
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logging.info("Low-stock scheduler stopped")

    async def _run_loop(self) -> None:
        """Run immediately when configured, then use a fixed post-run delay.

        Per-run failures are contained so a transient database error does not
        terminate later checks. Task cancellation still propagates to shutdown.
        """
        if self._run_immediately:
            await self._run_once()
        while True:
            await asyncio.sleep(self._interval_seconds)
            await self._run_once()

    async def _run_once(self) -> None:
        """Execute one correlated job and contain ordinary failures.

        Logs only the generated job ID, aggregate counts, and exception type;
        no inventory rows or configuration secrets are emitted.
        """
        job_id = str(uuid.uuid4())
        logging.info("Low-stock job started job_id=%s", job_id)
        try:
            results = await self._runner(job_id)
        except asyncio.CancelledError:
            logging.info("Low-stock job cancelled job_id=%s", job_id)
            raise
        except Exception as error:  # noqa: BLE001 - scheduler must survive job failures
            logging.error(
                "Low-stock job failed job_id=%s error_type=%s",
                job_id,
                type(error).__name__,
            )
            return
        logging.info(
            "Low-stock job succeeded job_id=%s warehouse_count=%s low_stock_count=%s",
            job_id,
            len(results),
            sum(results.values()),
        )
