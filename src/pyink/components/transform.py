"""``Transform`` — rewrite the rendered string of its children (PR7).

Mirrors ink's ``<Transform>``. The children are laid out and rendered to a
string, split into lines, and each line is passed through ``transform``.
The transformed text replaces the children's output in the tree.

Typical uses: uppercasing, ANSI gradients, indentation, hanging indents.

.. code-block:: python

    from pyink import Transform, Text

    Transform(
        Text("hello world"),
        transform=lambda line, idx: line.upper(),
    )

Constraint: ``transform`` should not change a line's visible width — the
layout engine has already positioned the children based on the original
widths, so changing them after the fact will misalign the rendered grid.

Implementation:

* ``Transform`` is a function component. On mount it mounts the children
  into a throwaway reconciler, lays them out at the active Instance's
  current width, renders them to a string, and applies ``transform`` per
  line. The result is emitted as a single ``text`` host leaf.
* Because function-component bodies run only once, the transformed text
  is captured at mount time. If the children read signals via lazy
  ``Text`` callables, those reads happen once during the inner layout
  and do *not* establish subscriptions — wrap the whole ``Transform``
  call site in a parent that rerenders if you need reactive transforms.
* ANSI escape sequences in the rendered string are passed through to the
  transform verbatim. A transform that wants to operate only on visible
  text should strip and re-apply escapes itself — PyInk does not impose
  a particular ANSI-awareness contract on transforms (matches ink).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyink.core.element import Element, create_element
from pyink.core.reconciler import Reconciler
from pyink.hooks._runtime import _get_current_instance
from pyink.layout import layout, render_layout_to_string

__all__ = ["Transform"]


def Transform(
    *children: Any,
    transform: Callable[[str, int], str],
    **_props: Any,
) -> Element:
    """Render ``children`` and pipe the resulting text through ``transform``.

    ``transform(line, index)`` is called once per line of the rendered
    output; ``index`` is the zero-based line number. The return value
    replaces that line in the output.

    Extra keyword props are accepted for parity with ink's API (which
    allows ``accessibilityLabel``) but currently ignored — PyInk does not
    implement a screen-reader surface.
    """
    transform_fn = transform
    captured_children = children

    def TransformImpl() -> Element:
        # Build a throwaway host tree containing the children and render it.
        # We use a fresh Reconciler so the children's effects (if any) are
        # scoped to this snapshot and torn down immediately after.
        inner = create_element("box", *captured_children)
        reconciler = Reconciler()
        mounted = reconciler.mount(inner)
        try:
            inst = _get_current_instance()
            columns = 80
            if inst is not None:
                cols_attr = getattr(inst, "columns", 0)
                if isinstance(cols_attr, int) and cols_attr > 0:
                    columns = cols_attr
            tree = layout(mounted, columns=columns)
            rendered = render_layout_to_string(tree)
        finally:
            reconciler.unmount(mounted)

        if rendered:
            lines = rendered.split("\n")
            transformed = "\n".join(
                transform_fn(line, idx) for idx, line in enumerate(lines)
            )
        else:
            transformed = ""

        return create_element("text", transformed)

    return create_element(TransformImpl)
