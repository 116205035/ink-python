"""``measure_element`` API + ``use_box_metrics`` hook (Phase 2 PR7).

Mirrors ink's ``measureElement(ref)`` (see
``D:/Projects/github/ink/ink-master/src/measure-element.ts``). Two entry
points:

* :func:`measure_element` — imperative snapshot. Reads ``ref.value`` once,
  returns a :class:`BoxMetrics`. Use this for one-shot queries (e.g. before
  positioning an overlay).
* :func:`use_box_metrics` — reactive subscription. Returns a
  :class:`ink.core.signal.Computed` of :class:`BoxMetrics`; reading
  ``.value`` inside a callable ``Text`` leaf / ``effect`` /
  :class:`ink.core.signal.Computed` body subscribes to layout updates.

Reactivity plumbing: the underlying ``ref.value`` is a plain
:class:`ink.core.signal.Ref` (PRD Decision 2 — non-reactive holder). The
hook therefore combines ``ref.value`` with the module-level
:data:`ink.hooks._box_metrics_runtime.layout_epoch` signal inside a
``computed`` body so the computed re-evaluates after every layout pass. The
render loop bumps the epoch once per layout, so consumers observe fresh
measurements without manually polling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ink.core.signal import Computed, computed
from ink.hooks._box_metrics_runtime import (
    UNMEASURED,
    BoxMetrics,
    layout_epoch,
)

if TYPE_CHECKING:
    from ink.core.signal import Ref
    from ink.layout.flex import LayoutNode

__all__ = ["BoxMetrics", "measure_element", "use_box_metrics"]


def measure_element(ref: Ref[LayoutNode | None]) -> BoxMetrics:
    """Read the current measurements for the element ``ref`` points at.

    Imperative — does not subscribe. Use :func:`use_box_metrics` inside a
    component body for reactive updates.

    Parameters
    ----------
    ref:
        The :class:`ink.core.signal.Ref` passed as the ``ref`` prop of a
        :func:`ink.components.Box`. ``ref.value`` is ``None`` before the
        first layout pass and after unmount.

    Returns
    -------
    BoxMetrics
        Snapshot of the element's measured size and position. ``has_measured``
        is ``False`` (and the coordinates are ``None``) when ``ref.value``
        is ``None``.
    """
    return _metrics_from_layout_node(ref.value)


def use_box_metrics(ref: Ref[LayoutNode | None]) -> Computed[BoxMetrics]:
    """Subscribe to measurements for the element ``ref`` points at.

    Returns a :class:`ink.core.signal.Computed` of
    :class:`BoxMetrics`. Read ``.value`` inside a callable ``Text`` leaf,
    another ``computed`` body, or an ``effect`` to subscribe — the computed
    refreshes after every layout pass.

    The hook is safe to call from inside a function-component body mounted
    via :func:`ink.render.render`. It is also usable outside ``render``
    (e.g. in tests that drive layout directly) — in that case the consumer
    must call :func:`bump_layout_epoch` after each layout pass for the
    computed to refresh, since no render loop is running to do it
    automatically.

    The returned computed is bound to the calling component instance
    through the active-component ContextVar (the same path ``use_input`` /
    ``use_app`` take) only when it's created inside a component body; the
    underlying ``Computed`` has no dispose to bind — its lifetime is
    bounded by the signals graph naturally.
    """
    # Read ``layout_epoch.value`` inside the body so the computed subscribes
    # to it. The render loop bumps the epoch after every layout pass, so a
    # consumer reading the computed inside a callable Text leaf / effect
    # re-renders when measurements change. The ``ref.value`` read is a plain
    # attribute access (Ref is non-reactive by design) — the epoch is what
    # carries the dependency edge.
    return computed(
        lambda: (
            _metrics_from_layout_node(ref.value),
            layout_epoch.value,  # subscribe to layout ticks
        )[0]
    )


def _metrics_from_layout_node(node: LayoutNode | None) -> BoxMetrics:
    """Build a :class:`BoxMetrics` snapshot from a :class:`LayoutNode`.

    ``None`` maps to :data:`UNMEASURED` so callers can distinguish "never
    laid out" from "laid out at zero size".
    """
    if node is None:
        return UNMEASURED
    return BoxMetrics(
        width=node.width,
        height=node.height,
        left=node.x,
        top=node.y,
        has_measured=True,
    )
