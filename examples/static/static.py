"""Static example — permanent output region + live counter.

Reference: ink's ``examples/static/``.

A background thread pushes a new line to a ``signal(list)`` every 500 ms.
``Static`` renders those items permanently above the live frame — they
never get re-painted by the frame diff and accumulate like ordinary log
output. A live ``Text`` below shows the running total.

Demonstrates:

* ``Static`` with a reactive ``Signal[list]`` source
* ``render_item`` callback producing styled output per item
* coexistence of permanent output (Static) and live frame (Text)

Run::

    python examples/static/static.py
"""

from __future__ import annotations

import sys
import threading
import time

from ink import Box, Static, Text, render, signal
from ink.core.element import Element
from ink.core.signal import Signal


def App() -> Element:
    completed: Signal[list[str]] = signal([])
    count = signal(0)
    total = 10

    def bg() -> None:
        for i in range(total):
            time.sleep(0.5)
            completed.value = [*completed.value, f"Task {i} completed"]
            count.value = i + 1

    threading.Thread(target=bg, daemon=True).start()

    def render_item(item: str, idx: int) -> Element:
        return Text(f"[done] {item}", color="green")

    return Box(
        Static(completed, render_item),
        Text(lambda: f"Completed: {count.value}/{total}", bold=True),
        flexDirection="column",
    )


def main() -> int:
    inst = render(App(), columns=50, rows=6)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
