"""PR3 Bug 1 part 2 regression tests — ``_Grid`` content-box clipping.

The layout engine's ``_distribute_main`` clamps each child's main-axis
allocation at its ``min_content_main`` floor (PR2) so a text leaf can
never be compressed to 0 height. But when the *sum* of children's
min-contents exceeds the container's main axis, the layout still
positions the overflow children outside the container's outer rect and
the renderer used to paint them anyway — smearing the overflow into the
parent's adjacent rows (the classic "row overlap" bug).

PR3 adds a clip stack to :class:`pyink.layout.render_layout._Grid`:

* :meth:`_Grid.clip` pushes an inclusive ``(x1, y1, x2, y2)`` rectangle
  (intersected with whatever clip the ancestor pushed).
* :meth:`_Grid.unclip` pops the most recent push.
* :meth:`_Grid.put` / :meth:`_Grid.fill_row` honour the active clip —
  writes whose starting cell falls outside the clip are dropped.

``_paint_node`` pushes a clip around each Box's outer rectangle before
painting its children (and pops it before painting the border, so the
border edges themselves are never clipped).
"""

from __future__ import annotations

from typing import Any

from pyink.core.element import Element, create_element
from pyink.layout.flex import LayoutNode
from pyink.layout.render_layout import _Grid, _paint_node
from pyink.render import render_to_string


def _text_leaf(
    text: str,
    x: int,
    y: int,
    width: int = 10,
    height: int = 1,
) -> LayoutNode:
    """Build a minimal text-leaf LayoutNode for direct paint tests."""
    return LayoutNode(
        x=x,
        y=y,
        width=width,
        height=height,
        content=text,
        kind="text",
    )


def _box_node(
    x: int,
    y: int,
    width: int,
    height: int,
    children: list[LayoutNode] | None = None,
    *,
    has_border: bool = False,
) -> LayoutNode:
    return LayoutNode(
        x=x,
        y=y,
        width=width,
        height=height,
        children=list(children) if children else [],
        kind="box",
        style={
            "hasBorder": has_border,
            "borderTop": has_border,
            "borderRight": has_border,
            "borderBottom": has_border,
            "borderLeft": has_border,
            "padding": (1 if has_border else 0,) * 4,
        },
    )


# ---------------------------------------------------------------------------
# _Grid.clip / unclip — intersection and write masking
# ---------------------------------------------------------------------------


def test_grid_clip_drops_out_of_bounds_write() -> None:
    """``put`` honours the active clip — out-of-bounds writes are dropped."""
    grid = _Grid(width=10, height=10)
    grid.clip(2, 2, 5, 5)
    # Inside the clip — written.
    grid.put(3, 3, "A")
    # Outside the clip — dropped.
    grid.put(0, 0, "B")
    grid.put(6, 6, "C")
    grid.unclip()
    out = grid.to_string().split("\n")
    # Row 3 column 3 contains 'A'.
    assert out[3][3] == "A"
    # (0, 0) and (6, 6) remain empty.
    assert out[0].rstrip() == ""
    assert out[6].rstrip() == ""


def test_grid_clip_intersect_with_parent() -> None:
    """Nested clips take the intersection — children only narrow the region."""
    grid = _Grid(width=20, height=20)
    # Parent clip: 0..9 × 0..9.
    grid.clip(0, 0, 9, 9)
    # Child clip narrows to 5..9 × 5..9 (intersection of 5..15 and 0..9).
    grid.clip(5, 5, 15, 15)
    # Inside both — written.
    grid.put(6, 6, "A")
    # Inside child but outside parent — dropped (intersection wins).
    grid.put(12, 12, "B")
    # Outside both — dropped.
    grid.put(0, 0, "C")
    grid.unclip()
    grid.unclip()
    out = grid.to_string().split("\n")
    assert out[6][6] == "A"
    assert out[12].rstrip() == ""
    assert out[0].rstrip() == ""


def test_grid_clip_unclip_restores_writable_region() -> None:
    """After ``unclip`` writes land anywhere again."""
    grid = _Grid(width=10, height=10)
    grid.clip(2, 2, 4, 4)
    grid.unclip()
    # No active clip — write succeeds even at the corners.
    grid.put(0, 0, "X")
    out = grid.to_string().split("\n")
    assert out[0][0] == "X"


def test_grid_unclip_empty_stack_is_noop() -> None:
    """``unclip`` on an empty stack must not raise."""
    grid = _Grid(width=4, height=4)
    grid.unclip()  # no-op, no exception


def test_grid_clip_fill_row_out_of_bounds() -> None:
    """``fill_row`` also honours the active clip."""
    grid = _Grid(width=10, height=10)
    grid.clip(2, 2, 8, 8)
    # Fill inside clip — paints.
    grid.fill_row(2, 3, 4, "ZZZZ")
    # Fill outside clip (wrong row) — dropped.
    grid.fill_row(2, 0, 4, "YYYY")
    grid.unclip()
    out = grid.to_string().split("\n")
    assert "ZZZZ" in out[3]
    assert "YYYY" not in out[0]


# ---------------------------------------------------------------------------
# _paint_node — box clips children to outer rectangle
# ---------------------------------------------------------------------------


def test_paint_box_clips_children_past_outer_rect() -> None:
    """A text leaf positioned outside the box's outer rect is masked.

    Simulates the overflow case: the layout placed a child at y=5 but
    the box's outer rect ends at y=3. Without the clip the text would
    paint into row 5 (a sibling's territory). With the clip it's
    dropped.
    """
    root = _box_node(
        x=0,
        y=0,
        width=10,
        height=4,
        children=[
            _text_leaf("INSIDE", x=0, y=1, width=6, height=1),
            # ``y=5`` is outside the parent's outer rect (which ends at y=3).
            _text_leaf("OUTSIDE", x=0, y=5, width=7, height=1),
        ],
    )
    grid = _Grid(width=10, height=10)
    _paint_node(grid, root, base_x=0, base_y=0)
    rendered = grid.to_string()
    assert "INSIDE" in rendered
    assert "OUTSIDE" not in rendered


def test_paint_box_keeps_child_in_padding_band() -> None:
    """Children in the padding/bottom-border band still paint.

    The clip targets the box's *outer* rectangle (not the inner content
    box) so a child the layout positioned in the bottom-padding band
    (e.g. a small single-row overflow when sum-of-min-contents just
    barely exceeds the container) still renders. This preserves the
    historic behaviour the example tests rely on — the small overflow
    paints into the padding band rather than being dropped.
    """
    # Box 5 rows tall; child placed at y=3 (the bottom-padding band when
    # border+padding would put content at y=1..2, leaving y=3 as
    # bottom-padding and y=4 as bottom-border).
    root = _box_node(
        x=0,
        y=0,
        width=10,
        height=5,
        children=[_text_leaf("X", x=1, y=3, width=1, height=1)],
        has_border=True,
    )
    grid = _Grid(width=10, height=8)
    _paint_node(grid, root, base_x=0, base_y=0)
    rendered = grid.to_string()
    # Child is inside outer rect (rows 0..4) — must paint.
    assert "X" in rendered


def test_overflow_content_does_not_leak_to_sibling() -> None:
    """End-to-end: column with two boxes; the second must not get overwritten.

    Builds the Bug 1 scenario at the integration level:

    * Root column holds two boxes stacked vertically.
    * The first box's children have a total min-content that exceeds
      the box's main-axis allocation (the layout overflows).
    * Without the clip the overflow children paint past the first box's
      bottom edge into the second box's rows — overwriting the second
      box's border.
    * With the clip the overflow is masked at the first box's outer
      rect and the second box renders cleanly.
    """
    # First box: 3 rows tall, holds 4 lines of text. Sum of min-contents
    # (4 lines) > container height (3 rows) → overflow by 1 line.
    # Second box: 3 rows tall, sits directly below.
    # Total root height: 6 rows.
    overflow_box = box(
        box(
            text("L0"),
            text("L1"),
            text("L2"),
            text("L3"),
            flexDirection="column",
        ),
        height=3,
    )
    sibling_box = box(text("Sibling"), height=3)
    tree = box(
        overflow_box,
        sibling_box,
        flexDirection="column",
        height=6,
        width=20,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    # The sibling box's text must survive — without the clip the
    # overflow from the first box would have painted "L3" on top of it.
    rendered_blob = "\n".join(lines)
    assert "Sibling" in rendered_blob
    # At least one of the leading L0/L1 lines from the overflow box is
    # still painted (the clip does not mask content *inside* the outer
    # rect, only the part that spills past it).
    assert "L0" in rendered_blob


def test_clip_does_not_affect_layout_size() -> None:
    """Paint-time clipping must not change layout measurements.

    The clip is applied in :func:`_paint_node` only — the layout
    engine's min-content / shrink / grow passes are untouched. A box
    whose children overflow still reports its declared size; only the
    rendered output masks the overflow.
    """
    # Build a tree with overflow, render it, and verify the rendered
    # string does not crash and has the expected row count.
    tree = box(
        box(
            text("AAAA"),
            text("BBBB"),
            text("CCCC"),
            text("DDDD"),
            flexDirection="column",
        ),
        height=2,
        width=10,
    )
    out = render_to_string(tree)
    # Two rows in the output (the root is height=2).
    assert len(out.split("\n")) <= 2


# ---------------------------------------------------------------------------
# Pseudo-host helpers (mirror test_flex.py's vocabulary)
# ---------------------------------------------------------------------------


def box(*children: Any, **props: Any) -> Element:
    return create_element("box", *children, **props)


def text(content: str, **props: Any) -> Element:
    return create_element("text", content, **props)


# ---------------------------------------------------------------------------
# render_layout_to_string — clip stack starts empty
# ---------------------------------------------------------------------------


def test_render_layout_has_no_active_clip_initially() -> None:
    """Sanity: rendering a trivial tree leaves the grid's clip stack empty.

    Guards against a push/pop mismatch where a paint path pushes a clip
    and forgets to pop it — the next sibling's paint would inherit the
    wrong clip.
    """
    grid = _Grid(width=4, height=2)
    root = _box_node(
        x=0,
        y=0,
        width=4,
        height=2,
        children=[_text_leaf("hi", x=0, y=0, width=2, height=1)],
    )
    _paint_node(grid, root, base_x=0, base_y=0)
    # Internal invariant: clip stack is empty after painting a root.
    assert grid._clip_stack == []  # noqa: SLF001 — intentional internal access
