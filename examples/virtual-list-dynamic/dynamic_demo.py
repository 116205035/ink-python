"""VirtualList dynamic example — Signal[list] append + auto-follow (Phase 5 PR3).

A background thread appends a new log-style row to a
``Signal[list[str]]`` every 200 ms (simulating a tailing log stream).
The :func:`pyink.externals.VirtualList` reads the signal at paint time
so new rows show up automatically. After each append the demo also
writes ``scroll_signal.value`` to point past the new tail — the
component clamps the value to the valid range, so this drives an
"auto-follow" behaviour that sticks to the bottom as items arrive.

Run::

    python examples/virtual-list-dynamic/dynamic_demo.py

Controls:

* Esc / Ctrl+C — quit (auto-follow keeps running otherwise).
"""

from __future__ import annotations

import sys
import threading
import time

from pyink import (
    Box,
    Text,
    create_element,
    render,
    signal,
    use_app,
    use_input,
)
from pyink.core.element import Element
from pyink.core.signal import Signal
from pyink.externals import VirtualList
from pyink.render.keys import Key

#: How many items to push before the background thread stops. Keeps the
#: demo from growing without bound — the viewport auto-follows until
#: the cap, then freezes at the tail so the user can scroll manually.
TOTAL_TARGET: int = 200

#: Inter-arrival delay between appended rows.
APPEND_INTERVAL_S: float = 0.2

#: Visible viewport size.
VIEWPORT_HEIGHT: int = 10


def _render_item(item: str, idx: int) -> Element:
    # ``idx`` is the absolute item index; we render it as a left-padded
    # 4-digit counter so columns line up regardless of payload length.
    return Text(f"[{idx:>4}] {item}")


def DynamicDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()
        items: Signal[list[str]] = signal([])
        scroll_sig: Signal[int] = signal(0)

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        def bg() -> None:
            """Append a new item every ``APPEND_INTERVAL_S`` seconds.

            Writes both the items signal (the data) and the scroll
            signal (the follow position) so the viewport stays glued
            to the tail as the stream grows. The component clamps
            out-of-range scroll values internally, so writing past the
            end is the canonical "stick to bottom" pattern.
            """
            for i in range(TOTAL_TARGET):
                # Sleep first so the initial paint shows an empty list
                # and the first append is visible.
                time.sleep(APPEND_INTERVAL_S)
                items.value = [*items.value, f"event #{i}"]
                # Drive auto-follow: point one-past-the-end; the
                # component clamps it back to the last valid offset.
                scroll_sig.value = len(items.value)

        threading.Thread(target=bg, daemon=True).start()

        return Box(
            Text("VirtualList dynamic demo (live append + auto-follow)", bold=True),
            Text("Esc quits.", dimColor=True),
            VirtualList(
                items,
                render_item=_render_item,
                viewport_height=VIEWPORT_HEIGHT,
                item_height=1,
                scroll_signal=scroll_sig,
                borderStyle="single",
            ),
            Text(
                lambda: f"Total: {len(items.value)} items",
                dimColor=True,
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(DynamicDemo(), columns=70, rows=18)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
