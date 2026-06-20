"""Debug-input example — print every keypress raw and parsed.

Use this when diagnosing why a key (arrow / Tab / function key / CJK
character) doesn't reach the component on a real terminal. It bypasses
all interactive logic and just echoes whatever ``use_input`` delivers to
the UI.

Run::

    python examples/debug-input/debug_input.py

Controls:

* Press any key — last keypress + history shown.
* Ctrl+C — quit.

What you should see (on a working terminal):

* Letter ``a`` → ``input='a'`` (no flags)
* Up arrow → ``input='' flags=['up']``
* Down arrow → ``input='' flags=['down']``
* Left / Right → ``input='' flags=['left'] / ['right']``
* Tab → ``input='\\t' flags=['tab']``
* Enter → ``input='\\r' flags=['enter']``
* Esc → ``input='\\x1b' flags=['esc']``
* F1-F12 → ``input=''`` with no flags (function keys are not surfaced as
  Key flags in the MVP — handlers must match the raw escape sequence).
* Home / End → ``input='' flags=['home'] / ['end']``
* Ctrl+C → exits.

If you see ``(none)`` forever (no Last update), the reader thread isn't
delivering bytes — that's a raw-mode problem (see
``Terminal.enter_raw_mode``). If you see printable letters but no arrows,
the console isn't translating special keys into ANSI escape sequences
— on Windows that means ``ENABLE_VIRTUAL_TERMINAL_INPUT`` is missing.
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
from pyink.render.keys import Key


def _flags(key: Key) -> list[str]:
    flags: list[str] = []
    if key.ctrl:
        flags.append("ctrl")
    if key.shift:
        flags.append("shift")
    if key.alt:
        flags.append("alt")
    if key.meta:
        flags.append("meta")
    if key.up_arrow:
        flags.append("up")
    if key.down_arrow:
        flags.append("down")
    if key.left_arrow:
        flags.append("left")
    if key.right_arrow:
        flags.append("right")
    if key.return_key:
        flags.append("enter")
    if key.escape:
        flags.append("esc")
    if key.tab:
        flags.append("tab")
    if key.backspace:
        flags.append("bs")
    if key.delete:
        flags.append("del")
    if key.page_up:
        flags.append("pgup")
    if key.page_down:
        flags.append("pgdn")
    if key.home:
        flags.append("home")
    if key.end:
        flags.append("end")
    return flags


def DebugInput() -> Element:
    """Component that prints every received key."""

    def Impl() -> Element:
        last = signal("(press a key)")
        history: Signal[list[tuple[str, list[str]]]] = signal([])

        def on_key(key: Key) -> None:
            flags = _flags(key)
            entry = (key.input, flags)
            last.value = (
                f"input={key.input!r} code={[hex(ord(c)) for c in key.input]}"
                f" flags={flags}"
            )
            history.value = [*history.value[-7:], entry]

        use_input(on_key)
        app = use_app()

        def on_ctrl_c(key: Key) -> None:
            if key.ctrl and key.input == "c":
                app.exit(None)

        use_input(on_ctrl_c)

        def render_history_line(entry: tuple[str, list[str]]) -> Element:
            inp, flags = entry
            return Text(f"  input={inp!r} flags={flags}", dimColor=True)

        return Box(
            Text("Press keys (Ctrl+C to exit)", dimColor=True),
            Text(""),
            Text(lambda: f"Last: {last.value}"),
            Text(""),
            Text("History:", bold=True),
            *[render_history_line(e) for e in history.value],
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(DebugInput(), columns=80, rows=24)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
