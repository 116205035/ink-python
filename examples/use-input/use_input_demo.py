"""use_input example — capture and display keystrokes.

Reference: ink's ``examples/use-input/``.

The component shows the most recent key's raw input plus its parsed flag
set (``ctrl`` / ``shift`` / ``alt`` / arrows / ``enter`` / ``escape`` /
``tab``). ``Ctrl+C`` (handled by the render pipeline's default
``exit_on_ctrl_c=True``) terminates the demo.

Run::

    python examples/use-input/use_input_demo.py
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, signal, use_input
from pyink.core.element import Element
from pyink.render.keys import Key


def InputDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def InputDemoImpl() -> Element:
        last_key = signal("(nothing yet)")
        key_info = signal("(none)")

        def on_key(key: Key) -> None:
            raw = key.input
            # repr() makes the special characters visible (CR, ESC, …).
            last_key.value = repr(raw) if raw else "(special)"

            flags: list[str] = []
            if key.ctrl:
                flags.append("ctrl")
            if key.shift:
                flags.append("shift")
            if key.alt:
                flags.append("alt")
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
                flags.append("backspace")
            if key.delete:
                flags.append("delete")
            key_info.value = ", ".join(flags) if flags else "(none)"

        use_input(on_key)

        return Box(
            Text("Press any key (Ctrl+C to exit)", dimColor=True),
            Text(lambda: f"Input: {last_key.value}"),
            Text(lambda: f"Flags: {key_info.value}"),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(InputDemoImpl)


def main() -> int:
    inst = render(InputDemo(), columns=50, rows=6)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
