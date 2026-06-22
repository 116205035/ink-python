"""VirtualList example — windowed 1000-row list (Phase 5 PR3).

Reference: ``react-window`` / ink's ad-hoc ``VirtualMessageList``.
PyInk ships :func:`pyink.externals.VirtualList` in Phase 5 — only the
visible slice of ``items`` is ever rendered, so a 1000-row list costs
the same per paint as a 20-row one.

The demo wires keyboard scrolling from outside the component via the
``scroll_signal`` prop: arrows move one row, ``PageUp`` / ``PageDown``
move a viewport's worth, ``Home`` / ``End`` jump to the top / bottom.
A status line under the viewport reports the current top-of-viewport
item index so callers can see the scroll position without digging into
signals.

Run::

    python examples/virtual-list/virtual_list_demo.py

Controls:

* Up / Down         — scroll one row.
* PageUp / PageDown — scroll one viewport (10 rows).
* Home / End        — jump to the first / last row.
* Esc / Ctrl+C      — quit.
"""

from __future__ import annotations

import sys

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

#: Total backing-list size. Large enough that rendering every row on
#: every paint would be visibly sluggish — the virtualisation is what
#: keeps the demo smooth.
TOTAL_ITEMS: int = 1000

#: Rows visible at once. Matches the wrapping Box height so the layout
#: grants the viewport exactly 10 rows.
VIEWPORT_HEIGHT: int = 10

#: Each item renders as a single Text row (fixed-height fast path).
def _render_item(item: str, idx: int) -> Element:
    # The ``idx`` is the absolute item index, so the row label stays
    # anchored to the backing item rather than the visible-slice offset.
    del idx  # the label already encodes the index; nothing else needed.
    return Text(item)


def VirtualListDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()
        scroll_sig: Signal[int] = signal(0)

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)
                return
            # Compute the next offset from the *current* signal value so
            # chained keystrokes accumulate correctly. ``VirtualList``
            # re-clamps out-of-range values internally, so passing a
            # value past the end is safe.
            current = scroll_sig.value
            if key.up_arrow:
                scroll_sig.value = current - 1
            elif key.down_arrow:
                scroll_sig.value = current + 1
            elif key.page_up:
                scroll_sig.value = current - VIEWPORT_HEIGHT
            elif key.page_down:
                scroll_sig.value = current + VIEWPORT_HEIGHT
            # Home / End handling — some terminals deliver these as
            # special escape sequences (``key.home`` / ``key.end``) while
            # others send the raw ``\x1b[H`` / ``\x1b[F`` bytes. Check
            # both shapes so the demo works across terminal emulators.
            elif getattr(key, "home", False) or key.input in ("\x1b[H", "\x1b[1~"):
                scroll_sig.value = 0
            elif getattr(key, "end", False) or key.input in ("\x1b[F", "\x1b[4~"):
                scroll_sig.value = TOTAL_ITEMS

        use_input(on_key)

        return Box(
            Text("VirtualList demo (1000 items, viewport=10)", bold=True),
            Text(
                "Up/Down scroll, PageUp/PageDown page, Home/End jump, Esc quits.",
                dimColor=True,
            ),
            VirtualList(
                [f"Item {i}" for i in range(TOTAL_ITEMS)],
                render_item=_render_item,
                viewport_height=VIEWPORT_HEIGHT,
                item_height=1,
                scroll_signal=scroll_sig,
                borderStyle="single",
            ),
            Text(
                lambda: f"Top index: {scroll_sig.value}",
                dimColor=True,
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(VirtualListDemo(), columns=60, rows=18)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
