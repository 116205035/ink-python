"""Borders example — render every available border style.

Reference: ink's ``examples/borders/``.

PyInk supports the four border styles the layout engine knows about:
``single``, ``double``, ``round``, ``bold``. This example stacks them in a
row so the user can eyeball each one. Press ``Ctrl+C`` to quit (the
example has no interactive surface — it just paints once and waits).

Run::

    python examples/borders/borders.py
"""

from __future__ import annotations

import sys

from pyink import Box, Text, render
from pyink.core.element import Element


def Borders() -> Element:
    return Box(
        Box(Text("single"), borderStyle="single", marginRight=2),
        Box(Text("double"), borderStyle="double", marginRight=2),
        Box(Text("round"), borderStyle="round", marginRight=2),
        Box(Text("bold"), borderStyle="bold"),
        padding=1,
    )


def main() -> int:
    inst = render(Borders(), columns=60, rows=5)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
