"""Tests for alternate-screen integration in the live render pipeline (PR7).

PR5 shipped alternate-screen support and a basic enter/exit test. PR7
rounds out coverage:

* The enter/exit escape sequences are emitted in the expected order and
  surround the live frame.
* Alternate screen is idempotent at the Terminal level (already covered
  by ``test_terminal.py``) — we cover the higher-level ``render(...,
  alternate_screen=True)`` contract.
* A rerender inside alternate screen emits only the diff (no full clear).
* Static writes inside alternate screen land above the live frame and
  do not break the diff.
* atexit / cleanup returns the terminal to the main screen.
* Unmounting restores the main screen even when no frame was painted.
* Order of escape sequences: alt-enter, hide-cursor, frame, ...,
  show-cursor, alt-exit.
"""

from __future__ import annotations

import io
import time
from typing import Any

from pyink import Box, Static, Text, render
from pyink.core.signal import signal
from pyink.render.instance import Instance


def _render_silent(tree: Any, **kwargs: Any) -> tuple[Instance, io.StringIO]:
    out = io.StringIO()
    kwargs.setdefault("exit_on_ctrl_c", False)
    inst = render(tree, stdout=out, **kwargs)
    return inst, out


def test_alt_screen_emits_enter_then_hide_cursor() -> None:
    """The cursor-hide sequence follows (not precedes) the alt-enter."""
    inst, out = _render_silent(Text("hi"), columns=20, rows=2, alternate_screen=True)
    written = out.getvalue()
    pos_enter = written.find("\x1b[?1049h")
    pos_hide = written.find("\x1b[?25l")
    assert pos_enter != -1
    assert pos_hide != -1
    assert pos_enter < pos_hide
    inst.unmount()


def test_alt_screen_emits_show_cursor_then_exit_on_unmount() -> None:
    inst, out = _render_silent(Text("hi"), columns=20, rows=2, alternate_screen=True)
    out.truncate(0)
    out.seek(0)
    inst.unmount()
    written = out.getvalue()
    pos_show = written.find("\x1b[?25h")
    pos_exit = written.find("\x1b[?1049l")
    assert pos_show != -1
    assert pos_exit != -1
    assert pos_show < pos_exit


def test_alt_screen_never_uses_full_clear() -> None:
    """PRD Decision 3: never emit ``\\x1b[2J`` even in alt screen."""
    inst, out = _render_silent(Text("hi"), columns=20, rows=2, alternate_screen=True)
    assert "\x1b[2J" not in out.getvalue()
    out.truncate(0)
    out.seek(0)
    counter = signal(0)

    def Counter() -> Any:
        return Text(lambda: f"c={counter.value}")

    inst.rerender(Counter())
    counter.value = 1
    time.sleep(0.2)
    assert "\x1b[2J" not in out.getvalue()
    inst.unmount()
    assert "\x1b[2J" not in out.getvalue()


def test_alt_screen_rerender_emits_only_diff() -> None:
    """Inside alt screen, rerender uses cursor-move + line-clear, not full paint."""
    inst, out = _render_silent(Text("first"), columns=20, rows=2, alternate_screen=True)
    out.truncate(0)
    out.seek(0)
    inst.rerender(Text("second"))
    repaint = out.getvalue()
    assert "second" in repaint
    assert "\x1b[2K" in repaint  # inline diff
    assert "\x1b[2J" not in repaint
    inst.unmount()


def test_alt_screen_unmount_restores_main_screen() -> None:
    inst, _out = _render_silent(Text("hi"), columns=20, rows=2, alternate_screen=True)
    assert inst.terminal.in_alternate_screen
    inst.unmount()
    assert not inst.terminal.in_alternate_screen


def test_alt_screen_then_inline_does_not_reenter() -> None:
    """A separate Instance created without alt screen must not see alt escapes."""
    inst1, _ = _render_silent(Text("a"), columns=10, rows=2, alternate_screen=True)
    inst1.unmount()
    out2 = io.StringIO()
    inst2 = render(Text("b"), stdout=out2, columns=10, rows=2, exit_on_ctrl_c=False)
    written = out2.getvalue()
    assert "\x1b[?1049h" not in written
    inst2.unmount()


def test_alt_screen_with_static_writes_above_frame() -> None:
    """Static output is preserved inside alt screen — the static text
    appears above the live frame in stdout."""
    log = signal(["first"])

    def App() -> Any:
        return Box(
            Static(log, lambda item, idx: Text(item)),
            Text("frame"),
            flexDirection="column",
        )

    inst, out = _render_silent(App(), columns=30, rows=4, alternate_screen=True)
    written = out.getvalue()
    assert "first" in written
    assert "frame" in written
    # Append a new item — it should appear in a subsequent write.
    out.truncate(0)
    out.seek(0)
    log.value = [*log.value, "second"]
    time.sleep(0.2)
    assert "second" in out.getvalue()
    inst.unmount()


def test_alt_screen_atexit_cleanup_exits_alt() -> None:
    """``Instance.cleanup`` flips the Terminal back to the main screen."""
    inst, _out = _render_silent(Text("x"), columns=10, rows=2, alternate_screen=True)
    assert inst.terminal.in_alternate_screen
    inst.cleanup()
    assert not inst.terminal.in_alternate_screen


def test_alt_screen_no_frame_still_exits_cleanly() -> None:
    """Unmounting without ever painting a frame still exits alt screen."""

    def Empty() -> Any:
        # Layout sentinel: produces nothing on screen.
        return Static([], lambda item, idx: Text(str(item)))

    inst, out = _render_silent(Empty(), columns=20, rows=2, alternate_screen=True)
    inst.unmount()
    final = out.getvalue()
    assert "\x1b[?1049h" in final
    assert "\x1b[?1049l" in final


def test_alt_screen_emits_hide_cursor_only_once() -> None:
    inst, out = _render_silent(Text("hi"), columns=20, rows=2, alternate_screen=True)
    written = out.getvalue()
    # One enter, one hide cursor on mount.
    assert written.count("\x1b[?1049h") == 1
    assert written.count("\x1b[?25l") == 1
    inst.unmount()
    final = out.getvalue()
    assert final.count("\x1b[?1049l") == 1
    assert final.count("\x1b[?25h") == 1
