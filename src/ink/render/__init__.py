"""Render package — sync test renderer + live render pipeline (PR5).

Two entry points live here:

* :func:`render_to_string` — sync, side-effect-free renderer used by
  tests and CI. PR2 introduced it; PR3 upgraded it to run the full
  flex layout pipeline.
* :func:`render` — the live TUI renderer added in PR5. Returns an
  :class:`Instance` that owns the render loop, scheduler, and
  terminal state. Inline mode is the default (PRD Decision 3);
  alternate screen is opt-in.

Sub-modules:

* :mod:`ink.render.ansi`     — colour / border helpers (PR4).
* :mod:`ink.render.diff`     — frame-level inline diff (PR5).
* :mod:`ink.render.terminal` — cross-platform terminal wrapper (PR5).
* :mod:`ink.render.instance` — live render handle (PR5).
* :mod:`ink.render.pipeline` — :func:`render` entry point (PR5).
"""

from __future__ import annotations

from dataclasses import dataclass

from ink.core.element import Element
from ink.core.reconciler import Reconciler
from ink.layout import layout, render_layout_to_string
from ink.render.instance import Instance
from ink.render.pipeline import render

__all__ = ["RenderOptions", "Instance", "render", "render_to_string"]


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """Options bag for :func:`render_to_string`.

    Separate from :class:`ink.render.pipeline.RenderOptions` (which
    allows ``None`` for auto-detect) because :func:`render_to_string`
    always needs a concrete ``columns`` value to feed the layout.
    """

    columns: int = 80
    rows: int | None = None


def render_to_string(
    tree: Element,
    *,
    columns: int = 80,
) -> str:
    """Render ``tree`` to a plain string snapshot.

    The tree is mounted, laid out, painted, then unmounted — the
    function does not keep the tree alive. Callable leaves are
    evaluated synchronously a single time; signal reads inside them
    return current snapshot values but do **not** establish
    subscriptions.
    """
    options = RenderOptions(columns=columns)
    reconciler = Reconciler()
    root = reconciler.mount(tree, parent=None)
    try:
        if root is None:
            return ""
        layout_tree = layout(root, columns=options.columns, rows=options.rows)
        return render_layout_to_string(layout_tree)
    finally:
        reconciler.unmount(root)
