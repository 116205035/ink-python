"""SelectInput example — keyboard navigation over a list of choices.

Reference: ink's ``examples/select-input/`` + the third-party
``ink-select-input`` component.

PyInk does not ship a ``SelectInput`` external in the MVP — this example
builds the same UX out of primitives (``Box`` / ``Text`` / ``use_input`` /
``signal``). It demonstrates:

* ``use_input`` key handling (arrows + Enter)
* ``signal`` driving selection highlight
* ``use_app().exit`` to terminate the loop on Enter

Run::

    python examples/select-input/select_input.py

Controls:

* Up / Down — move the highlight
* Enter    — confirm and exit
* Ctrl+C   — quit
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
from pyink.render.keys import Key

ITEMS = ("Apple", "Banana", "Cherry", "Date", "Elderberry")


def SelectInput() -> Element:
    """Render a navigable list; Enter confirms selection.

    Hooks (``use_input`` / ``use_app``) must be called inside the
    component body — i.e. inside ``SelectInputImpl``. The outer factory
    only captures shared state and returns ``create_element(Impl)``.
    """
    items = list(ITEMS)

    def SelectInputImpl() -> Element:
        selected = signal(0)
        app = use_app()

        def on_key(key: Key) -> None:
            if key.up_arrow:
                selected.value = (selected.value - 1) % len(items)
            elif key.down_arrow:
                selected.value = (selected.value + 1) % len(items)
            elif key.return_key:
                # Confirm and exit. ``app.exit`` triggers ``Instance.unmount``.
                app.exit(None)

        use_input(on_key)

        def render_item(idx: int, label: str) -> Element:
            prefix = "> " if idx == selected.value else "  "
            is_active = idx == selected.value
            return Text(
                f"{prefix}{label}",
                color="green" if is_active else None,
                bold=is_active,
            )

        return Box(
            Text("Pick a fruit:", bold=True),
            Text("Use arrow keys, Enter to select.", dimColor=True),
            *[render_item(i, label) for i, label in enumerate(items)],
            flexDirection="column",
            padding=1,
        )

    return create_element(SelectInputImpl)


def main() -> int:
    inst = render(SelectInput(), columns=40, rows=10)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
