"""Tests for :func:`pyink.externals.VirtualList` (Phase 5 PR2).

``VirtualList`` is a function-component factory — the factory itself is
declarative (no render context needed), but the slice of visible items
is computed inside the reconciler-mounted function body through a
callable child so signal writes drive re-paints.

Coverage:

* Element shape — ``VirtualList`` returns an :class:`Element` whose
  ``type`` is the :func:`_VirtualListImpl` function component.
* Factory validation — ``viewport_height >= 1``, ``overscan >= 0``,
  ``box_props.height`` collision raises.
* Fixed-height fast path — only the visible slice is mounted (we
  verify by counting items rendered into the frame).
* ``overscan`` widens the visible slice on both sides.
* ``initial_scroll`` seeds the offset and is clamped.
* ``on_scroll`` fires when the offset changes.
* ``scroll_signal`` lets the caller drive scrolling from outside.
* Items ``Signal`` mutation (append / truncate) re-paints the slice
  and clamps a stale offset.
* Dynamic-height mode raises :class:`NotImplementedError`.

The reactive tests use the live :func:`pyink.render.render` pipeline
because callable children + ``effect`` subscriptions only establish
inside the reconciler render context (mirrors
:mod:`tests.externals.test_text_input` / :mod:`tests.externals.test_select_input`).
Static render tests use :func:`pyink.render_to_string` (one-shot sync
renderer) so the deterministic shape tests do not need terminal plumbing.
"""

from __future__ import annotations

import io
import re
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from pyink import Box, Text, render, render_to_string, signal
from pyink.core.element import Element
from pyink.core.signal import Signal
from pyink.externals import VirtualList
from pyink.externals.virtual_list import _clamp_offset, _VirtualListImpl
from pyink.render import terminal as _term_mod
from pyink.render.instance import Instance
from pyink.render.terminal import Terminal

#: Regex that matches any CSI / OSC escape sequence (used to strip ANSI
#: when asserting on visible buffer content).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _visible(frame: str) -> str:
    """Strip ANSI escape sequences so assertions see only the visible text."""
    return _ANSI_RE.sub("", frame)


# ---------------------------------------------------------------------------
# Fake TTY + input pipeline patches (reactive scroll tests)
# ---------------------------------------------------------------------------


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _patch_input(
    bytes_iter: Iterator[bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = threading.Lock()
    exhausted = {"value": False}

    def fake_read(fd: int, n: int) -> bytes:
        with lock:
            if exhausted["value"]:
                time.sleep(0.05)
                return b""
            try:
                return next(bytes_iter)
            except StopIteration:
                exhausted["value"] = True
                return b""

    monkeypatch.setattr(_term_mod, "_read_stdin_chunk", fake_read)
    monkeypatch.setattr(_term_mod, "_wait_for_input", lambda fd, timeout: True)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_unix", lambda self: None)
    monkeypatch.setattr(Terminal, "_enter_raw_mode_windows", lambda self: None)
    monkeypatch.setattr(Terminal, "_exit_raw_mode_windows", lambda self: None)


def _stream(chunks: list[bytes]) -> Iterator[bytes]:
    yield from chunks
    while True:
        time.sleep(0.02)
        yield b""


def _mount(
    tree: Element,
    *,
    monkeypatch: pytest.MonkeyPatch,
    columns: int = 40,
    rows: int = 16,
) -> tuple[Instance, io.StringIO]:
    out = io.StringIO()
    _patch_input(_stream([]), monkeypatch)
    inst = render(
        tree,
        stdout=out,
        stdin=_FakeTTY(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    time.sleep(0.05)
    return inst, out


def _frame(inst: Instance) -> str:
    return _visible(inst.current_frame)


def _wait_for(
    predicate: Callable[[], bool],
    *,
    attempts: int = 200,
    delay: float = 0.025,
) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        time.sleep(delay)
    return predicate()


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_virtual_list_returns_function_component_element() -> None:
    """``VirtualList`` is a factory returning an element wrapping ``_VirtualListImpl``."""
    el = VirtualList(
        items=["a", "b", "c"],
        render_item=lambda item, i: Text(item),
        viewport_height=2,
        item_height=1,
    )
    assert isinstance(el, Element)
    assert callable(el.type)
    assert el.type is _VirtualListImpl
    # No children — the function component builds its own subtree.
    assert el.children == ()


def test_virtual_list_props_capture_defaults() -> None:
    el = VirtualList(
        items=["a"],
        render_item=lambda item, i: Text(item),
        viewport_height=1,
        item_height=1,
    )
    assert el.props["items"] == ["a"]
    assert el.props["viewport_height"] == 1
    assert el.props["item_height"] == 1
    assert el.props["overscan"] == 3
    assert el.props["on_scroll"] is None
    assert el.props["key"] is None
    assert el.props["initial_scroll"] == 0
    assert el.props["scroll_signal"] is None
    assert el.props["box_props"] == {}


def test_virtual_list_props_capture_caller_values() -> None:
    def on_scroll(off: int) -> None:
        pass

    def key_fn(item: Any, i: int) -> str:
        return str(i)

    my_sig: Signal[int] = signal(0)

    el = VirtualList(
        items=["a", "b"],
        render_item=lambda item, i: Text(item),
        viewport_height=5,
        item_height=2,
        overscan=1,
        on_scroll=on_scroll,
        key=key_fn,
        initial_scroll=7,
        scroll_signal=my_sig,
        width=20,
    )
    assert el.props["viewport_height"] == 5
    assert el.props["item_height"] == 2
    assert el.props["overscan"] == 1
    assert el.props["on_scroll"] is on_scroll
    assert el.props["key"] is key_fn
    assert el.props["initial_scroll"] == 7
    assert el.props["scroll_signal"] is my_sig
    assert el.props["box_props"] == {"width": 20}


# ---------------------------------------------------------------------------
# Factory validation
# ---------------------------------------------------------------------------


def test_virtual_list_rejects_zero_viewport_height() -> None:
    with pytest.raises(ValueError, match="viewport_height"):
        VirtualList(
            items=["a"],
            render_item=lambda item, i: Text(item),
            viewport_height=0,
            item_height=1,
        )


def test_virtual_list_rejects_negative_overscan() -> None:
    with pytest.raises(ValueError, match="overscan"):
        VirtualList(
            items=["a"],
            render_item=lambda item, i: Text(item),
            viewport_height=1,
            item_height=1,
            overscan=-1,
        )


def test_virtual_list_rejects_explicit_height_prop() -> None:
    """``height`` is pinned to ``viewport_height`` — explicit pass is an error."""
    with pytest.raises(ValueError, match="height"):
        VirtualList(
            items=["a"],
            render_item=lambda item, i: Text(item),
            viewport_height=1,
            item_height=1,
            height=99,
        )


# ---------------------------------------------------------------------------
# _clamp_offset helper
# ---------------------------------------------------------------------------


def test_clamp_offset_zero_when_list_shorter_than_viewport() -> None:
    assert _clamp_offset(offset=5, n=3, viewport_height=10) == 0


def test_clamp_offset_within_range_unchanged() -> None:
    assert _clamp_offset(offset=4, n=20, viewport_height=10) == 4


def test_clamp_offset_past_end_clamps() -> None:
    assert _clamp_offset(offset=100, n=20, viewport_height=10) == 10


def test_clamp_offset_negative_clamps_to_zero() -> None:
    assert _clamp_offset(offset=-3, n=20, viewport_height=10) == 0


# ---------------------------------------------------------------------------
# Fixed-height fast path via render_to_string
# ---------------------------------------------------------------------------


def test_fixed_height_renders_visible_slice_only() -> None:
    """A 20-item list with viewport=5 renders exactly the first 5 rows."""
    tree: Any = Box(
        VirtualList(
            items=[f"row-{i:02d}" for i in range(20)],
            render_item=lambda item, i: Text(item),
            viewport_height=5,
            item_height=1,
            overscan=0,
        ),
        flexDirection="column",
        width=20,
    )
    out = render_to_string(tree, columns=40)
    lines = out.split("\n")
    # The first 5 rows are visible; row-05 through row-19 are NOT
    # mounted, so their text must not appear in the frame at all.
    visible = [ln for ln in lines if ln.startswith("row-")]
    assert visible == [f"row-{i:02d}" for i in range(5)]
    for i in range(5, 20):
        assert f"row-{i:02d}" not in out


def test_fixed_height_renders_overscan_above_and_below(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overscan widens the slice that ``render_item`` is called for.

    With ``initial_scroll=10`` and ``overscan=3``, the range passed to
    ``render_item`` is ``[7, 18)`` (11 rows). The visible viewport still
    shows only 5 rows (the Text ``scroll_offset`` clips the overscan),
    but the slice computed — and therefore the indices ``render_item``
    saw — covers the wider window.

    We verify by recording the indices ``render_item`` is called with
    and asserting they span ``[7, 18)`` exactly.
    """
    seen: list[int] = []

    def render_item(item: str, i: int) -> Element:
        seen.append(i)
        return Text(item)

    scroll_sig: Signal[int] = signal(10)
    items = [f"row-{i}" for i in range(50)]

    tree: Any = Box(
        VirtualList(
            items=items,
            render_item=render_item,
            viewport_height=5,
            item_height=1,
            overscan=3,
            scroll_signal=scroll_sig,
        ),
        flexDirection="column",
        width=20,
    )
    inst, _ = _mount(tree, monkeypatch=monkeypatch)

    # The first paint resolves the slice synchronously; wait for the
    # expected indices to appear.
    assert _wait_for(lambda: 17 in seen)
    # Exactly indices 7..17 inclusive (each render paints the slice;
    # we dedupe so we assert on the *set* of indices seen, not the
    # call count).
    assert sorted(set(seen)) == list(range(7, 18))

    inst.unmount()


def test_initial_scroll_clamps_past_end() -> None:
    """``initial_scroll`` past the last valid offset settles on the last window."""
    tree: Any = Box(
        VirtualList(
            items=[f"row-{i:02d}" for i in range(20)],
            render_item=lambda item, i: Text(item),
            viewport_height=5,
            item_height=1,
            overscan=0,
            initial_scroll=999,
        ),
        flexDirection="column",
        width=20,
    )
    out = render_to_string(tree, columns=40)
    rendered_lines = [ln for ln in out.split("\n") if ln.startswith("row-")]
    # Valid last offset = 20 - 5 = 15 → rows 15..19 visible.
    assert rendered_lines == [f"row-{i:02d}" for i in range(15, 20)]


def test_fixed_height_list_shorter_than_viewport_renders_all() -> None:
    """When ``n <= viewport_height`` the offset is forced to 0."""
    tree: Any = Box(
        VirtualList(
            items=["only", "two"],
            render_item=lambda item, i: Text(item),
            viewport_height=10,
            item_height=1,
            overscan=0,
            initial_scroll=5,
        ),
        flexDirection="column",
        width=20,
    )
    out = render_to_string(tree, columns=40)
    assert "only" in out
    assert "two" in out


# ---------------------------------------------------------------------------
# Dynamic-height mode is not implemented in PR2
# ---------------------------------------------------------------------------


def test_dynamic_height_raises_not_implemented() -> None:
    """``item_height=None`` must raise at mount time (PR2 scope limit)."""
    tree: Any = Box(
        VirtualList(
            items=["a", "b", "c"],
            render_item=lambda item, i: Text(item),
            viewport_height=2,
            item_height=None,
        ),
        flexDirection="column",
        width=20,
    )
    # ``render_to_string`` mounts the tree synchronously; the
    # ``NotImplementedError`` surfaces from the function body.
    with pytest.raises(NotImplementedError, match="dynamic-height"):
        render_to_string(tree, columns=40)


# ---------------------------------------------------------------------------
# Reactive scroll via the live render pipeline
# ---------------------------------------------------------------------------


def test_scroll_signal_drives_re_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing ``scroll_signal.value`` re-paints with the new visible slice."""
    scroll_sig: Signal[int] = signal(0)
    items = [f"item-{i:03d}" for i in range(100)]

    tree: Any = Box(
        VirtualList(
            items=items,
            render_item=lambda item, i: Text(item),
            viewport_height=5,
            item_height=1,
            overscan=0,
            scroll_signal=scroll_sig,
        ),
        flexDirection="column",
        width=20,
    )
    inst, _ = _mount(tree, monkeypatch=monkeypatch)

    # Initial paint: items 0..4.
    frame = _frame(inst)
    for i in range(5):
        assert f"item-{i:03d}" in frame
    assert "item-005" not in frame

    # Scroll down to offset 50.
    scroll_sig.value = 50
    assert _wait_for(lambda: "item-050" in _frame(inst))
    frame = _frame(inst)
    for i in range(50, 55):
        assert f"item-{i:03d}" in frame
    # The previously visible items are gone.
    for i in range(5):
        assert f"item-{i:03d}" not in frame

    inst.unmount()


def test_on_scroll_callback_fires_on_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``on_scroll`` fires with the new offset whenever the signal changes."""
    offsets: list[int] = []
    scroll_sig: Signal[int] = signal(0)

    def on_scroll(off: int) -> None:
        offsets.append(off)

    items = [f"x-{i}" for i in range(50)]

    tree: Any = Box(
        VirtualList(
            items=items,
            render_item=lambda item, i: Text(item),
            viewport_height=5,
            item_height=1,
            overscan=0,
            on_scroll=on_scroll,
            scroll_signal=scroll_sig,
        ),
        flexDirection="column",
        width=20,
    )
    inst, _ = _mount(tree, monkeypatch=monkeypatch)
    # The mount-time clamp writes 0 → callback fires with 0.
    assert _wait_for(lambda: 0 in offsets)

    scroll_sig.value = 10
    assert _wait_for(lambda: 10 in offsets)

    scroll_sig.value = 20
    assert _wait_for(lambda: 20 in offsets)

    inst.unmount()


def test_external_scroll_signal_clamped_when_past_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing past the valid range is clamped inside the component."""
    scroll_sig: Signal[int] = signal(0)
    items = [f"r-{i}" for i in range(10)]

    tree: Any = Box(
        VirtualList(
            items=items,
            render_item=lambda item, i: Text(item),
            viewport_height=5,
            item_height=1,
            overscan=0,
            scroll_signal=scroll_sig,
        ),
        flexDirection="column",
        width=20,
    )
    inst, _ = _mount(tree, monkeypatch=monkeypatch)

    # The valid offset range for n=10, viewport=5 is [0, 5]. Writing 999
    # must be clamped back to 5 by the internal effect.
    scroll_sig.value = 999
    assert _wait_for(lambda: scroll_sig.value == 5)

    inst.unmount()


def test_items_signal_mutation_drives_re_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Appending to an items signal re-paints with the new contents."""
    items_sig: Signal[list[str]] = signal([f"row-{i}" for i in range(5)])
    scroll_sig: Signal[int] = signal(0)

    tree: Any = Box(
        VirtualList(
            items=items_sig,
            render_item=lambda item, i: Text(item),
            viewport_height=5,
            item_height=1,
            overscan=0,
            scroll_signal=scroll_sig,
        ),
        flexDirection="column",
        width=20,
    )
    inst, _ = _mount(tree, monkeypatch=monkeypatch)

    # Initial paint: rows 0..4.
    assert _wait_for(lambda: "row-4" in _frame(inst))

    # Append 5 more items and scroll to offset 5.
    items_sig.value = [f"row-{i}" for i in range(10)]
    scroll_sig.value = 5
    assert _wait_for(lambda: "row-9" in _frame(inst))
    frame = _frame(inst)
    for i in range(5, 10):
        assert f"row-{i}" in frame

    inst.unmount()


def test_items_shrink_clamps_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When items shrink below the current offset, the offset re-clamps."""
    items_sig: Signal[list[str]] = signal([f"v-{i}" for i in range(20)])
    scroll_sig: Signal[int] = signal(0)

    tree: Any = Box(
        VirtualList(
            items=items_sig,
            render_item=lambda item, i: Text(item),
            viewport_height=5,
            item_height=1,
            overscan=0,
            scroll_signal=scroll_sig,
        ),
        flexDirection="column",
        width=20,
    )
    inst, _ = _mount(tree, monkeypatch=monkeypatch)

    # Scroll to offset 10 (valid for n=20, viewport=5).
    scroll_sig.value = 10
    assert _wait_for(lambda: "v-10" in _frame(inst))

    # Shrink to 4 items — offset must clamp back to 0 and the earlier
    # items become visible again.
    items_sig.value = ["v-0", "v-1", "v-2", "v-3"]
    assert _wait_for(lambda: scroll_sig.value == 0)
    assert _wait_for(lambda: "v-3" in _frame(inst))

    inst.unmount()


# ---------------------------------------------------------------------------
# Callable items source (non-Signal)
# ---------------------------------------------------------------------------


def test_callable_items_source_resolves_at_paint_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``Callable[[], list[T]]`` items source is re-invoked on each paint."""
    state = {"items": [f"c-{i}" for i in range(10)]}
    scroll_sig: Signal[int] = signal(0)

    tree: Any = Box(
        VirtualList(
            items=lambda: state["items"],
            render_item=lambda item, i: Text(item),
            viewport_height=5,
            item_height=1,
            overscan=0,
            scroll_signal=scroll_sig,
        ),
        flexDirection="column",
        width=20,
    )
    inst, _ = _mount(tree, monkeypatch=monkeypatch)

    assert _wait_for(lambda: "c-4" in _frame(inst))

    # Mutating the closure state does not, by itself, trigger a
    # re-render — the callable is only re-invoked when something else
    # invalidates the layout. Writing ``scroll_signal`` does invalidate
    # it, and the new callable result is picked up.
    state["items"] = [f"d-{i}" for i in range(10)]
    scroll_sig.value = 1  # any write to trigger a re-paint
    assert _wait_for(lambda: "d-1" in _frame(inst))

    inst.unmount()


# ---------------------------------------------------------------------------
# Render index is absolute (not slice-relative)
# ---------------------------------------------------------------------------


def test_render_item_receives_absolute_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``render_item``'s ``index`` argument is the item's position in the
    backing list, not its slot in the visible slice."""
    seen: list[int] = []

    def render_item(item: str, i: int) -> Element:
        seen.append(i)
        return Text(f"#{i}: {item}")

    scroll_sig: Signal[int] = signal(0)
    items = [f"x-{i}" for i in range(50)]

    tree: Any = Box(
        VirtualList(
            items=items,
            render_item=render_item,
            viewport_height=5,
            item_height=1,
            overscan=0,
            scroll_signal=scroll_sig,
        ),
        flexDirection="column",
        width=20,
    )
    inst, _ = _mount(tree, monkeypatch=monkeypatch)

    # Scroll to offset 20 → indices 20..24 should be rendered.
    scroll_sig.value = 20
    assert _wait_for(lambda: 24 in seen)
    # Each rendered index is in the absolute range [20, 25).
    late_indices = [i for i in seen if i >= 20]
    assert late_indices == [20, 21, 22, 23, 24]

    inst.unmount()
