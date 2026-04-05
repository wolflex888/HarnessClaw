from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Awaitable


class LimitEnforcer:
    """Checks cost and time limits for an agent session.

    Call check_cost() and check_time() on each CostPoller tick.
    on_exceeded is called at most once across all checks.
    """

    def __init__(
        self,
        session_id: str,
        max_cost: float | None,
        max_time: int | None,
        on_exceeded: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self._session_id = session_id
        self._max_cost = max_cost
        self._max_time = max_time
        self._on_exceeded = on_exceeded
        self._fired = False
        self._started_at = time.monotonic()

    def check_cost(self, current_cost: float) -> None:
        if self._fired or self._max_cost is None:
            return
        if current_cost >= self._max_cost:
            self._fire("cost", f"Cost ${current_cost:.4f} exceeded limit ${self._max_cost:.4f}")

    def check_time(self) -> None:
        if self._fired or self._max_time is None:
            return
        elapsed = int(time.monotonic() - self._started_at)
        if elapsed >= self._max_time:
            self._fire("time", f"Time {elapsed}s exceeded limit {self._max_time}s")

    def _fire(self, reason: str, detail: str) -> None:
        self._fired = True
        try:
            asyncio.create_task(self._on_exceeded(reason, detail))
        except RuntimeError:
            pass  # no event loop (tests or shutdown)
