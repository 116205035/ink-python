"""Scheduler — batched rerender queue (PR2).

PR2 only needs the API surface: callers can ``schedule`` callbacks and
``flush`` them in registration order (deduplicated by identity). The PR5
render pipeline will wire signal writes through the scheduler to coalesce
multiple updates into a single rerender pass.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import suppress

__all__ = ["Scheduler"]


class Scheduler:
    """A minimal FIFO scheduler with identity-based deduplication.

    The queue is flushed eagerly by :meth:`flush` — PR2 does not integrate
    with the event loop or signal writes. Thread-safe via a single lock.
    """

    __slots__ = ("_queue", "_lock", "_flushing")

    def __init__(self) -> None:
        self._queue: list[Callable[[], None]] = []
        self._lock = threading.RLock()
        self._flushing: bool = False

    def schedule(self, callback: Callable[[], None]) -> None:
        """Enqueue ``callback`` for the next flush (no-op if already queued)."""
        with self._lock:
            for existing in self._queue:
                if existing is callback:
                    return
            self._queue.append(callback)

    def flush(self) -> None:
        """Invoke every queued callback in registration order.

        Callbacks scheduled during the flush are picked up in the same
        pass. Exceptions inside a callback are swallowed so a single
        failure cannot starve the rest of the queue — mirror the
        signals-module policy of not letting one bad subscriber break
        the reactive graph.
        """
        with self._lock:
            if self._flushing:
                return
            self._flushing = True

        try:
            while True:
                with self._lock:
                    if not self._queue:
                        break
                    batch = self._queue
                    self._queue = []
                for callback in batch:
                    with suppress(Exception):
                        # Intentional: keep flushing remaining callbacks.
                        callback()
        finally:
            with self._lock:
                self._flushing = False

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)
