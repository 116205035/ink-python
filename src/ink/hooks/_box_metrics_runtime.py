"""Runtime support for ``measure_element`` / ``use_box_metrics`` (Phase 2 PR7).

Three pieces live here so :mod:`ink.hooks.box_metrics` can stay focused on
the user-facing API:

* :class:`BoxMetrics` — the immutable measurement result a caller reads from
  :func:`ink.hooks.box_metrics.measure_element` or the
  :class:`~ink.core.signal.Computed` returned by
  :func:`ink.hooks.box_metrics.use_box_metrics`. Carries the four
  coordinates (``width`` / ``height`` / ``left`` / ``top``) plus a
  ``has_measured`` flag that's ``False`` until the element has been laid out
  at least once.
* :data:`UNMEASURED` — sentinel returned before the first layout pass (and
  after unmount, once the host element is gone). All four coordinates are
  ``None`` and ``has_measured`` is ``False``.
* ``layout_epoch`` — a process-wide :class:`~ink.core.signal.Signal` of
  ``int`` incremented at the end of every layout pass. ``use_box_metrics``
  reads this signal inside its :class:`~ink.core.signal.Computed` body so
  the computed re-evaluates after every layout — without it the computed
  would only ever read ``ref.value`` (a plain :class:`~ink.core.signal.Ref`
  which doesn't trigger reactivity), and consumers would never observe
  updates.

Reactivity contract: ``ref.value`` is *not* a signal (see PRD Decision 2 —
``ref`` is the PR1 non-reactive holder). ``use_box_metrics`` therefore
combines the epoch signal with a fresh read of ``ref.value`` inside a
``computed`` body. The render loop subscribes to the epoch (because it bumps
after every paint), and consumers reading the computed inside a callable
``Text`` leaf / ``effect`` subscribe in turn.

The epoch is bumped from :mod:`ink.render.instance` (the render loop's
layout passes). Test code that drives layout directly via
:func:`ink.layout.layout` can also call :func:`bump_layout_epoch` to keep
its assertions in sync.
"""

from __future__ import annotations

from dataclasses import dataclass

from ink.core.signal import Signal, signal

__all__ = [
    "BoxMetrics",
    "UNMEASURED",
    "bump_layout_epoch",
    "layout_epoch",
]


@dataclass(frozen=True, slots=True)
class BoxMetrics:
    """Element measurement snapshot.

    All four coordinate fields are ``int`` once the element has been laid
    out; ``None`` before that. ``has_measured`` distinguishes "laid out,
    zero-sized" (``has_measured=True`` with ``width=0``) from "never laid
    out" (``has_measured=False``).

    ``left`` / ``top`` are coordinates **relative to the parent's
    top-left corner** (matching :class:`ink.layout.flex.LayoutNode.x` /
    ``.y``) — this is what callers normally want to anchor tooltips /
    overlays against sibling boxes. For absolute terminal coordinates
    consumers must walk up the layout tree, which is out of scope for
    Phase 2.
    """

    width: int | None
    height: int | None
    left: int | None
    top: int | None
    has_measured: bool


#: Sentinel returned by :func:`measure_element` /
#: :func:`use_box_metrics` when the underlying layout node has not yet
#: been measured (first layout hasn't run) or has been unmounted.
UNMEASURED: BoxMetrics = BoxMetrics(
    width=None,
    height=None,
    left=None,
    top=None,
    has_measured=False,
)


#: Process-wide monotonic counter bumped after every layout pass.
#:
#: ``use_box_metrics`` reads this inside its ``computed`` body so the
#: computed re-evaluates whenever a layout pass has refreshed the
#: underlying ``ref.value`` — without this the computed's dependency
#: graph would never include ``ref`` (refs are non-reactive) and consumers
#: would never observe fresh measurements.
layout_epoch: Signal[int] = signal(0)


def bump_layout_epoch() -> None:
    """Advance :data:`layout_epoch` by one.

    Called by the render loop after every layout pass. Test code that
    drives layout directly can also call this to keep subscribers in
    sync with the new measurements.

    The write bypasses :attr:`Signal.value`'s getter on the read side so
    callers may safely invoke this from *inside* an effect / computed body
    (e.g. the render loop's layout pass running inside ``_effect_body``).
    Going through ``layout_epoch.value = layout_epoch.value + 1`` would
    otherwise register the calling observer as a subscriber to the very
    signal it's bumping, creating a self-retrigger loop that re-runs the
    effect on every paint.
    """
    # Read the underlying field directly (bypassing the tracking getter)
    # then go through the public setter so subscribers still get notified.
    layout_epoch.value = layout_epoch._value + 1
