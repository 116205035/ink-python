"""Counter example — auto-incrementing counter via signals + threads.

Reference: ink's ``examples/counter/``.

The PyInk variant does not require keyboard input — a background thread
ticks the counter every 100 ms. Press ``Ctrl+C`` (or send SIGINT) to exit.

This example demonstrates the minimum viable reactive loop:

* a ``signal`` holds the count
* a ``Text`` callable child reads it on every flush
* a daemon thread writes to the signal from outside the component
* the render-loop effect subscribes to the signal and repaints the frame

Run::

    python examples/counter/counter.py
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable

from pyink import Box, Text, effect, render, signal
from pyink.core.element import Element


def Counter() -> Element:
    """A counter that increments itself every 100 ms."""
    count = signal(0)

    def setup_timer() -> Callable[[], None]:
        # Daemon thread ticks the signal — the render loop repaints on
        # each write. Cleanup flips ``running`` to False so the loop
        # exits when the component is unmounted.
        running = [True]

        def tick() -> None:
            while running[0]:
                time.sleep(0.1)
                if not running[0]:
                    return
                count.value += 1

        t = threading.Thread(target=tick, daemon=True)
        t.start()

        def cleanup() -> None:
            running[0] = False

        return cleanup

    # ``deps=[]`` → mount-only effect; cleanup fires on unmount.
    effect(setup_timer, deps=[])

    return Box(
        Text(lambda: f"{count.value} tests passed", color="green"),
        flexDirection="column",
        padding=1,
    )


def main() -> int:
    inst = render(Counter(), columns=40, rows=3)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
