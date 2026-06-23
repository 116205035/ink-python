"""Tests for ``measure_element`` + ``use_box_metrics`` (Phase 2 PR7).

Coverage:

* ``measure_element`` returns ``UNMEASURED`` before the first layout pass.
* ``measure_element`` returns the post-layout coordinates after mount.
* ``use_box_metrics`` returns a ``Computed`` whose value reflects the
  measured element and refreshes on layout changes.
* Two independent refs don't cross-pollute (each measures its own Box).
* Nested Box refs report outer vs inner measurements correctly.
* Unmount clears ``ref.value`` (subsequent reads see ``has_measured=False``).
* ``BoxMetrics`` is a frozen dataclass; ``UNMEASURED`` is the canonical
  pre-measurement sentinel.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from ink import (
    Box,
    BoxMetrics,
    Text,
    create_element,
    measure_element,
    ref,
    render,
    use_box_metrics,
)
from ink.core.element import Element
from ink.core.signal import Computed, Ref, effect
from ink.hooks._box_metrics_runtime import UNMEASURED, layout_epoch
from ink.layout.flex import LayoutNode


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _new_ref() -> Ref[LayoutNode | None]:
    """Create a typed Box-metrics ref (initial value ``None``)."""
    return ref(None)


# ---------------------------------------------------------------------------
# BoxMetrics data shape
# ---------------------------------------------------------------------------


def test_box_metrics_is_frozen_dataclass() -> None:
    m = BoxMetrics(width=10, height=2, left=0, top=0, has_measured=True)
    # FrozenInstanceError is a subclass of AttributeError.
    with pytest.raises(AttributeError):
        m.width = 99  # type: ignore[misc]


def test_unmeasured_sentinel_is_all_none() -> None:
    assert UNMEASURED.has_measured is False
    assert UNMEASURED.width is None
    assert UNMEASURED.height is None
    assert UNMEASURED.left is None
    assert UNMEASURED.top is None


def test_unmeasured_is_a_box_metrics_instance() -> None:
    assert isinstance(UNMEASURED, BoxMetrics)


# ---------------------------------------------------------------------------
# measure_element — imperative snapshot
# ---------------------------------------------------------------------------


def test_measure_element_unmeasured_ref_returns_unmeasured() -> None:
    """A ``Ref`` whose value is still ``None`` measures as UNMEASURED."""
    empty: Ref[LayoutNode | None] = ref(None)
    metrics = measure_element(empty)
    assert metrics is UNMEASURED
    assert metrics.has_measured is False


def test_measure_element_reads_post_layout_node() -> None:
    """measure_element pulls coordinates straight from the LayoutNode."""
    node = LayoutNode(x=3, y=4, width=10, height=2)
    box_ref: Ref[LayoutNode | None] = ref(node)
    metrics = measure_element(box_ref)
    assert metrics.has_measured is True
    assert metrics.width == 10
    assert metrics.height == 2
    assert metrics.left == 3
    assert metrics.top == 4


# ---------------------------------------------------------------------------
# Integration with Box + render
# ---------------------------------------------------------------------------


def test_box_ref_backfilled_after_mount() -> None:
    """The Box's ``ref`` is populated once the first layout pass runs."""
    captured: dict[str, Any] = {}

    def App() -> Element:
        box_ref = _new_ref()
        captured["ref"] = box_ref
        # Mount-time read; the ref is populated by the time the component
        # body returns *if* a layout has already run. The first layout
        # actually runs inside _start_render_loop's initial _paint_now,
        # which happens after the component body finishes — so we capture
        # the ref and read its value after render returns.
        return Box(Text("hi"), ref=box_ref)

    inst = render(
        create_element(App),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=20,
        rows=3,
        exit_on_ctrl_c=False,
    )
    try:
        box_ref = captured["ref"]
        # After mount + initial paint the ref is populated.
        assert box_ref.value is not None
        assert isinstance(box_ref.value, LayoutNode)
    finally:
        inst.unmount()


def test_measure_element_returns_real_size_after_mount() -> None:
    """measure_element sees the post-layout width/height on a fitted Box."""
    captured: dict[str, Any] = {}

    def App() -> Element:
        box_ref = _new_ref()
        captured["ref"] = box_ref
        return Box(
            Box(
                Text("hello"),
                ref=box_ref,
                borderStyle="single",
                alignSelf="flex-start",
            ),
            flexDirection="column",
        )

    inst = render(
        create_element(App),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=40,
        rows=5,
        exit_on_ctrl_c=False,
    )
    try:
        metrics = measure_element(captured["ref"])
        # "hello" is 5 chars wide; border adds 2 (left + right).
        assert metrics.has_measured is True
        assert metrics.width == 7
        assert metrics.height == 3  # top border + content + bottom border
    finally:
        inst.unmount()


# ---------------------------------------------------------------------------
# use_box_metrics — reactive subscription
# ---------------------------------------------------------------------------


def test_use_box_metrics_returns_computed_of_box_metrics() -> None:
    captured: dict[str, Any] = {}

    def App() -> Element:
        box_ref = _new_ref()
        captured["ref"] = box_ref
        metrics = use_box_metrics(box_ref)
        captured["metrics"] = metrics
        # Read inside a callable so the render loop subscribes to the
        # computed (and transitively to layout_epoch).
        return Box(
            Box(Text("hi"), ref=box_ref, borderStyle="single"),
            Text(lambda: f"w={metrics.value.width};m={metrics.value.has_measured}"),
            flexDirection="column",
        )

    inst = render(
        create_element(App),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=40,
        rows=5,
        exit_on_ctrl_c=False,
    )
    try:
        metrics = captured["metrics"]
        assert isinstance(metrics, Computed)
        # After mount + initial paint, the metrics are populated.
        snapshot = metrics.value
        assert isinstance(snapshot, BoxMetrics)
        assert snapshot.has_measured is True
    finally:
        inst.unmount()


def test_use_box_metrics_refreshes_on_viewport_change() -> None:
    """When the terminal width changes, the computed picks up new sizes."""
    captured: dict[str, Any] = {}

    def App() -> Element:
        box_ref = _new_ref()
        captured["ref"] = box_ref
        metrics = use_box_metrics(box_ref)
        captured["metrics"] = metrics
        # Box fills available width via flexGrow=1.
        return Box(
            Box(ref=box_ref, flexGrow=1),
            alignSelf="flex-start",
        )

    inst = render(
        create_element(App),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=20,
        rows=3,
        exit_on_ctrl_c=False,
    )
    try:
        metrics = captured["metrics"]
        # Initial measurement: Box fills the 20-col viewport (root fills
        # available columns and the inner Box has flexGrow=1 in a row).
        first = metrics.value
        assert first.has_measured is True
        first_width = first.width
        assert first_width is not None and first_width >= 1

        # Simulate a terminal resize: bump the options.columns and trigger
        # a fresh paint so the layout epoch advances.
        inst.options.columns = 60
        inst._paint_now()

        refreshed = metrics.value
        assert refreshed.has_measured is True
        assert refreshed.width is not None
        # The wider viewport should produce a wider measured Box.
        assert refreshed.width > first_width
    finally:
        inst.unmount()


def test_two_refs_measure_independently() -> None:
    """Two Boxes with separate refs each see their own size."""
    captured: dict[str, Any] = {}

    def App() -> Element:
        outer_ref = _new_ref()
        inner_ref = _new_ref()
        captured["outer"] = outer_ref
        captured["inner"] = inner_ref
        return Box(
            Box(
                Box(Text("inner"), ref=inner_ref),
                ref=outer_ref,
                padding=2,
                borderStyle="single",
            ),
            alignSelf="flex-start",
        )

    inst = render(
        create_element(App),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=60,
        rows=8,
        exit_on_ctrl_c=False,
    )
    try:
        outer = measure_element(captured["outer"])
        inner = measure_element(captured["inner"])
        assert outer.has_measured is True
        assert inner.has_measured is True
        # The outer Box is larger than the inner Box (it has padding + a
        # border + the inner content).
        assert outer.width is not None and inner.width is not None
        assert outer.width > inner.width
        assert outer.height is not None and inner.height is not None
        assert outer.height > inner.height
    finally:
        inst.unmount()


def test_nested_box_refs_measure_correct_sides() -> None:
    """An outer ref reports the outer size, an inner ref the inner size."""
    captured: dict[str, Any] = {}

    def App() -> Element:
        outer_ref = _new_ref()
        inner_ref = _new_ref()
        captured["outer"] = outer_ref
        captured["inner"] = inner_ref
        return Box(
            Box(Text("a"), ref=outer_ref),
            Box(Text("bb"), ref=inner_ref),
        )

    inst = render(
        create_element(App),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    try:
        outer = measure_element(captured["outer"])
        inner = measure_element(captured["inner"])
        assert outer.has_measured is True
        assert inner.has_measured is True
        # "a" is 1 char wide; "bb" is 2 chars wide.
        assert outer.width == 1
        assert inner.width == 2
    finally:
        inst.unmount()


def test_ref_value_cleared_after_unmount() -> None:
    """Unmounting the tree clears every Box's ref so callers see UNMEASURED."""
    captured: dict[str, Any] = {}

    def App() -> Element:
        box_ref = _new_ref()
        captured["ref"] = box_ref
        return Box(Text("hi"), ref=box_ref)

    inst = render(
        create_element(App),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=20,
        rows=3,
        exit_on_ctrl_c=False,
    )
    box_ref = captured["ref"]
    # Pre-unmount: ref is populated.
    assert box_ref.value is not None
    inst.unmount()
    # Post-unmount: ref is cleared.
    assert box_ref.value is None
    # measure_element reflects the cleared state.
    assert measure_element(box_ref) is UNMEASURED


# ---------------------------------------------------------------------------
# Standalone (no render) use_box_metrics — epoch drives refresh
# ---------------------------------------------------------------------------


def test_use_box_metrics_outside_render_needs_epoch_bump() -> None:
    """Without a render loop the consumer must bump the epoch manually.

    The hook is primarily a render-loop citizen, but it should still
    behave predictably when used in a test harness that drives layout
    directly: the computed is lazy, so reading ``.value`` triggers
    evaluation; the only signal in its dependency graph is
    ``layout_epoch``, so the computed refreshes only after a bump.
    """
    box_ref = _new_ref()
    metrics = use_box_metrics(box_ref)

    # Initial read: ref is None → UNMEASURED.
    assert metrics.value is UNMEASURED

    # Populate the ref directly (simulating a layout pass).
    box_ref.value = LayoutNode(x=5, y=6, width=12, height=3)

    # The computed is cached — without an epoch bump it stays stale.
    # (This documents the intended behaviour: the epoch is what carries
    # the dependency edge. Without the render loop, the caller is
    # responsible for bumping it.)
    assert metrics.value is UNMEASURED

    # After a bump, the computed re-evaluates and reflects the new node.
    from ink.hooks._box_metrics_runtime import bump_layout_epoch

    bump_layout_epoch()
    refreshed = metrics.value
    assert refreshed.has_measured is True
    assert refreshed.width == 12
    assert refreshed.height == 3
    assert refreshed.left == 5
    assert refreshed.top == 6


def test_use_box_metrics_subscribes_via_effect() -> None:
    """An effect that reads the computed re-runs after a layout tick."""
    box_ref = _new_ref()
    metrics = use_box_metrics(box_ref)

    observed: list[bool] = []

    def watch() -> None:
        observed.append(metrics.value.has_measured)

    dispose = effect(watch)
    try:
        # Initial effect run sees UNMEASURED.
        assert observed == [False]
        box_ref.value = LayoutNode(x=0, y=0, width=4, height=1)
        from ink.hooks._box_metrics_runtime import bump_layout_epoch

        bump_layout_epoch()
        # Effect re-ran, now observing has_measured=True.
        assert observed[-1] is True
    finally:
        dispose()


# ---------------------------------------------------------------------------
# layout_epoch is a signal
# ---------------------------------------------------------------------------


def test_layout_epoch_is_a_signal_starting_at_zero() -> None:
    """The epoch is reset to 0 at process import time (sanity check)."""
    # We only assert it's an int >= 0; full reset semantics aren't part
    # of the contract (other tests may have bumped it already).
    assert isinstance(layout_epoch.value, int)
    assert layout_epoch.value >= 0
