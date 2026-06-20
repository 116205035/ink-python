"""Tests for :func:`pyink.externals.TextInput` (Phase 4 PR1).

We exercise the component through the live :func:`pyink.render.render`
pipeline because ``TextInput`` mounts :func:`pyink.hooks.use_input`,
whose guard requires the active ``_current_instance`` ContextVar that
only ``render`` sets up — the synchronous :func:`render_to_string`
renderer would refuse the hook.

Keystrokes are fed via the same ``_patch_input`` helper used by the
``use_input`` tests: we feed byte chunks into the terminal's input
loop and assert on either the rendered frame (visible buffer + cursor
SGR escapes) or captured callback side-effects.

Single-line scope (PR1): no Enter-to-insert-newline, no selection, no
paste-bracketing. Those land in PR2.
"""

from __future__ import annotations

import io
import re
import threading
import time
from collections.abc import Callable, Iterator

import pytest

from pyink import Box, Text, render
from pyink.core.element import Element
from pyink.externals import TextInput
from pyink.externals.text_input import _TextInputImpl, cursor_column, cursor_line
from pyink.render import terminal as _term_mod
from pyink.render.instance import Instance
from pyink.render.terminal import Terminal

ESC = "\x1b"

#: Regex that matches any CSI / OSC escape sequence (used to strip ANSI
#: when asserting on visible buffer content).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _visible(frame: str) -> str:
    """Strip ANSI escape sequences so assertions see only the visible text."""
    return _ANSI_RE.sub("", frame)


# ---------------------------------------------------------------------------
# Fake TTY + input pipeline patches (mirrors tests/hooks/test_use_input.py)
# ---------------------------------------------------------------------------


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _patch_input(
    bytes_iter: Iterator[bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch ``_read_stdin_chunk`` + ``_wait_for_input`` + raw-mode methods."""

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
    """A byte source that yields each chunk then idles with empty bytes."""
    yield from chunks
    while True:
        time.sleep(0.02)
        yield b""


def _mount(
    tree: Element,
    *,
    monkeypatch: pytest.MonkeyPatch,
    feed: list[bytes] | None = None,
    columns: int = 30,
    rows: int = 3,
) -> tuple[Instance, io.StringIO]:
    out = io.StringIO()
    if feed is None:
        feed = []
    _patch_input(_stream(feed), monkeypatch)
    inst = render(
        tree,
        stdout=out,
        stdin=_FakeTTY(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    # Let the first paint flush.
    time.sleep(0.05)
    return inst, out


def _frame(inst: Instance) -> str:
    return inst.current_frame


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


def _last_change_is(changes: list[str], expected: str) -> bool:
    """True when ``changes`` has at least one entry equal to ``expected`` at the end."""
    return bool(changes) and changes[-1] == expected


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_text_input_returns_function_component_element() -> None:
    el = TextInput()
    assert isinstance(el, Element)
    assert callable(el.type)
    assert el.type is _TextInputImpl
    # No children — the function component builds its own subtree.
    assert el.children == ()


def test_text_input_props_capture_defaults() -> None:
    el = TextInput()
    assert el.props["initial_value"] == ""
    assert el.props["placeholder"] is None
    assert el.props["on_change"] is None
    assert el.props["on_submit"] is None
    assert el.props["mask"] is None
    assert el.props["max_length"] is None
    assert el.props["color"] is None
    assert el.props["cursor_color"] is None
    assert el.props["cursor_style"] == "bar"
    assert el.props["is_active"] is True
    assert el.props["box_props"] == {}


def test_text_input_props_capture_caller_values() -> None:
    def on_change(v: str) -> None:
        pass

    def on_submit(v: str) -> None:
        pass

    el = TextInput(
        initial_value="abc",
        placeholder="hint",
        on_change=on_change,
        on_submit=on_submit,
        mask="*",
        max_length=10,
        color="red",
        cursor_color="green",
        cursor_style="block",
        is_active=False,
        padding=1,
    )
    assert el.props["initial_value"] == "abc"
    assert el.props["placeholder"] == "hint"
    assert el.props["on_change"] is on_change
    assert el.props["on_submit"] is on_submit
    assert el.props["mask"] == "*"
    assert el.props["max_length"] == 10
    assert el.props["color"] == "red"
    assert el.props["cursor_color"] == "green"
    assert el.props["cursor_style"] == "block"
    assert el.props["is_active"] is False
    # box_props absorbs the extra kwargs.
    assert el.props["box_props"] == {"padding": 1}


def test_text_input_invalid_cursor_style_raises() -> None:
    with pytest.raises(ValueError, match="cursor_style"):
        TextInput(cursor_style="blink")  # type: ignore[arg-type]


def test_text_input_negative_max_length_raises() -> None:
    with pytest.raises(ValueError, match="max_length"):
        TextInput(max_length=-1)


def test_text_input_multichar_mask_collapsed_to_first_char() -> None:
    """A multi-char ``mask`` is silently collapsed to its first glyph."""
    el = TextInput(mask="***")
    assert el.props["mask"] == "*"


def test_text_input_empty_mask_treated_as_none() -> None:
    el = TextInput(mask="")
    assert el.props["mask"] is None


def test_text_input_initial_value_clipped_to_max_length() -> None:
    el = TextInput(initial_value="abcdef", max_length=3)
    assert el.props["initial_value"] == "abc"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_text_input() -> None:
    from pyink.externals import TextInput as InitTextInput

    assert InitTextInput is TextInput


def test_text_input_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in."""
    import pyink

    assert not hasattr(pyink, "TextInput"), "TextInput must NOT be top-level"


# ---------------------------------------------------------------------------
# First-frame rendering (initial value / placeholder / mask / cursor)
# ---------------------------------------------------------------------------


def test_initial_value_renders_in_first_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(TextInput(initial_value="hello"), monkeypatch=monkeypatch)
    assert _wait_for(lambda: "hello" in _visible(_frame(inst)))
    inst.unmount()


def test_placeholder_shown_when_buffer_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(placeholder="enter name"), monkeypatch=monkeypatch
    )
    assert _wait_for(lambda: "enter name" in _visible(_frame(inst)))
    inst.unmount()


def test_placeholder_replaced_after_first_char(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(placeholder="enter name"), monkeypatch=monkeypatch
    )
    assert _wait_for(lambda: "enter name" in _visible(_frame(inst)))
    # Feed one char — placeholder should disappear and the typed char
    # should show.
    # We need a separate render mount because _stream already exhausted.
    inst.unmount()
    inst2, _ = _mount(
        TextInput(placeholder="enter name"),
        monkeypatch=monkeypatch,
        feed=[b"a"],
    )
    assert _wait_for(lambda: "a" in _visible(_frame(inst2)))
    assert "enter name" not in _visible(_frame(inst2))
    inst2.unmount()


def test_mask_renders_asterisks(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        TextInput(initial_value="secret", mask="*"), monkeypatch=monkeypatch
    )
    assert _wait_for(lambda: "******" in _visible(_frame(inst)))
    # The actual buffer must NOT leak into the frame.
    assert "secret" not in _visible(_frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Typing — single-char insertion
# ---------------------------------------------------------------------------


def test_typing_single_char_appends_to_empty_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    inst, _ = _mount(
        TextInput(on_change=changes.append), monkeypatch=monkeypatch, feed=[b"h"]
    )
    assert _wait_for(lambda: "h" in _visible(_frame(inst)))
    inst.unmount()
    assert changes == ["h"]


def test_typing_multiple_chars_appends_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(), monkeypatch=monkeypatch, feed=[b"h", b"i"]
    )
    assert _wait_for(lambda: "hi" in _visible(_frame(inst)))
    inst.unmount()


def test_typing_inserts_at_cursor_middle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Initial value "ac" with cursor at end; we move left once then
    # type "b" to insert between a and c.
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"b"]  # Left, then 'b'
    inst, _ = _mount(
        TextInput(initial_value="ac", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abc"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Backspace / Delete
# ---------------------------------------------------------------------------


def test_backspace_deletes_previous_char(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    feed: list[bytes] = [b"\x7f"]  # Backspace
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "ab"))
    inst.unmount()


def test_backspace_at_start_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backspace with cursor at column 0 must not mutate the buffer."""
    changes: list[str] = []
    # Left three times to move cursor to start, then Backspace.
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x1b[D", b"\x7f"]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # Give it time to process all keys.
    time.sleep(0.2)
    assert changes == []
    inst.unmount()


def test_delete_removes_char_at_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Move cursor to start (Left × 3), then Delete removes 'a'.
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[D", b"\x1b[D", b"\x1b[D",
        b"\x1b[3~",
    ]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "bc"))
    inst.unmount()


def test_delete_at_end_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[3~"]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    time.sleep(0.2)
    assert changes == []
    inst.unmount()


# ---------------------------------------------------------------------------
# Arrow keys — cursor movement
# ---------------------------------------------------------------------------


def test_left_arrow_moves_cursor_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cursor at end → Left → typing inserts before the last char."""
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "aXb"))
    inst.unmount()


def test_right_arrow_moves_cursor_right(monkeypatch: pytest.MonkeyPatch) -> None:
    # Left, Left (start), Right, then type 'X' to insert at index 1.
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x1b[C", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "aXb"))
    inst.unmount()


def test_left_arrow_at_start_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    # Left at start, then type — must insert at position 0 (cursor
    # didn't move below 0).
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xab"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Home / End / Ctrl+A / Ctrl+E
# ---------------------------------------------------------------------------


def test_home_moves_cursor_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    # Home, then type 'X' — inserts at start.
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[H", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xab"))
    inst.unmount()


def test_end_moves_cursor_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # Left, Left, End, then type 'X' — inserts at end.
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x1b[F", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abX"))
    inst.unmount()


def test_ctrl_a_moves_cursor_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ctrl+A = 0x01, then type 'X'.
    changes: list[str] = []
    feed: list[bytes] = [b"\x01", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xab"))
    inst.unmount()


def test_ctrl_e_moves_cursor_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # Left, Left, Ctrl+E (0x05), then type 'X'.
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x05", b"X"]
    inst, _ = _mount(
        TextInput(initial_value="ab", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abX"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Ctrl+K / Ctrl+U / Ctrl+W — line / word kill
# ---------------------------------------------------------------------------


def test_ctrl_k_kills_to_end_of_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # Move cursor left twice (cursor after 'ab' in 'abcd'), then Ctrl+K (0x0b).
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x0b"]
    inst, _ = _mount(
        TextInput(initial_value="abcd", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "ab"))
    inst.unmount()


def test_ctrl_u_kills_to_start_of_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # Move left twice (cursor after 'ab'), then Ctrl+U (0x15).
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[D", b"\x1b[D", b"\x15"]
    inst, _ = _mount(
        TextInput(initial_value="abcd", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "cd"))
    inst.unmount()


def test_ctrl_w_deletes_previous_word(monkeypatch: pytest.MonkeyPatch) -> None:
    # Buffer "foo bar"; cursor at end; Ctrl+W deletes "bar".
    changes: list[str] = []
    feed: list[bytes] = [b"\x17"]  # Ctrl+W
    inst, _ = _mount(
        TextInput(initial_value="foo bar", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "foo "))
    inst.unmount()


def test_ctrl_w_skips_trailing_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    # Buffer "foo   "; cursor at end; Ctrl+W deletes all whitespace
    # plus "foo".
    changes: list[str] = []
    feed: list[bytes] = [b"\x17"]
    inst, _ = _mount(
        TextInput(initial_value="foo   ", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, ""))
    inst.unmount()


def test_ctrl_w_at_start_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    changes: list[str] = []
    # Move to start, then Ctrl+W.
    feed: list[bytes] = [b"\x1b[H", b"\x17"]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    time.sleep(0.2)
    assert changes == []
    inst.unmount()


# ---------------------------------------------------------------------------
# Enter — on_submit
# ---------------------------------------------------------------------------


def test_enter_triggers_on_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[str] = []
    feed: list[bytes] = [b"\r"]  # Enter
    inst, _ = _mount(
        TextInput(initial_value="value", on_submit=submitted.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: len(submitted) == 1)
    inst.unmount()
    assert submitted == ["value"]


def test_enter_without_submit_callback_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed: list[bytes] = [b"\r"]
    inst, _ = _mount(
        TextInput(initial_value="value"), monkeypatch=monkeypatch, feed=feed
    )
    # Must not crash; value remains.
    time.sleep(0.1)
    assert "value" in _visible(_frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# on_change — fired only when the buffer actually changes
# ---------------------------------------------------------------------------


def test_on_change_not_fired_for_cursor_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[D",  # Left
        b"\x1b[C",  # Right
        b"\x1b[H",  # Home
        b"\x1b[F",  # End
        b"\x01",    # Ctrl+A
        b"\x05",    # Ctrl+E
    ]
    inst, _ = _mount(
        TextInput(initial_value="abc", on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    time.sleep(0.2)
    assert changes == []
    inst.unmount()


def test_on_change_fired_for_each_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    changes: list[str] = []
    feed: list[bytes] = [b"a", b"b", b"c"]
    inst, _ = _mount(
        TextInput(on_change=changes.append), monkeypatch=monkeypatch, feed=feed
    )
    assert _wait_for(lambda: "abc" in _frame(inst))
    inst.unmount()
    assert changes == ["a", "ab", "abc"]


# ---------------------------------------------------------------------------
# max_length — truncates insertions
# ---------------------------------------------------------------------------


def test_max_length_drops_overflowing_insertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    feed: list[bytes] = [b"x"]  # would push length 3 → 4, but max=3
    inst, _ = _mount(
        TextInput(initial_value="abc", max_length=3, on_change=changes.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    time.sleep(0.15)
    assert "abc" in _visible(_frame(inst))
    assert changes == []
    inst.unmount()


def test_max_length_allows_deletion_under_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed: list[bytes] = [b"\x7f"]  # Backspace
    inst, _ = _mount(
        TextInput(initial_value="abc", max_length=3),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(
        lambda: "ab" in _visible(_frame(inst))
        and "abc" not in _visible(_frame(inst))
    )
    inst.unmount()


# ---------------------------------------------------------------------------
# is_active=False — input ignored
# ---------------------------------------------------------------------------


def test_is_active_false_ignores_keystrokes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    feed: list[bytes] = [b"x", b"y", b"\x7f"]
    inst, _ = _mount(
        TextInput(
            initial_value="abc",
            is_active=False,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    time.sleep(0.2)
    assert "abc" in _visible(_frame(inst))
    assert changes == []
    inst.unmount()


# ---------------------------------------------------------------------------
# Cursor rendering — bar / block / underline
# ---------------------------------------------------------------------------


def test_cursor_bar_style_emits_inverse_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bar`` cursor emits an inverse-video space at the cursor position."""
    # Empty buffer with no placeholder — cursor sits at end (col 0) as a
    # lone inverse space.
    inst, _ = _mount(TextInput(cursor_style="bar"), monkeypatch=monkeypatch)
    assert _wait_for(lambda: f"{ESC}[7m {ESC}[0m" in _frame(inst))
    inst.unmount()


def test_cursor_block_style_emits_inverse_char(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``block`` cursor renders the cursor character under inverse video.

    Initial value "a" puts the cursor past it, so an inverse space is
    emitted at the end.
    """
    inst, _ = _mount(
        TextInput(initial_value="", cursor_style="block"),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: f"{ESC}[7m {ESC}[0m" in _frame(inst))
    inst.unmount()


def test_cursor_underline_style_emits_underline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(initial_value="", cursor_style="underline"),
        monkeypatch=monkeypatch,
    )
    assert _wait_for(lambda: f"{ESC}[4m {ESC}[0m" in _frame(inst))
    inst.unmount()


def test_cursor_color_applied_to_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    """``cursor_color`` tints the bar cursor's foreground."""
    inst, _ = _mount(
        TextInput(cursor_style="bar", cursor_color="green"),
        monkeypatch=monkeypatch,
    )
    # Green (32) + inverse, then a space, then reset.
    expected = f"{ESC}[32m{ESC}[7m {ESC}[0m"
    assert _wait_for(lambda: expected in _frame(inst))
    inst.unmount()


def test_cursor_color_applied_to_block(monkeypatch: pytest.MonkeyPatch) -> None:
    inst, _ = _mount(
        TextInput(cursor_style="block", cursor_color="red"),
        monkeypatch=monkeypatch,
    )
    expected = f"{ESC}[31m{ESC}[7m {ESC}[0m"
    assert _wait_for(lambda: expected in _frame(inst))
    inst.unmount()


def test_cursor_color_applied_to_underline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(cursor_style="underline", cursor_color="blue"),
        monkeypatch=monkeypatch,
    )
    expected = f"{ESC}[34m{ESC}[4m {ESC}[0m"
    assert _wait_for(lambda: expected in _frame(inst))
    inst.unmount()


def test_cursor_at_middle_position_renders_on_char(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block cursor over an actual character inverts that character."""
    # Cursor at start (Home) over "a" in "ab".
    feed: list[bytes] = [b"\x1b[H"]
    inst, _ = _mount(
        TextInput(initial_value="ab", cursor_style="block"),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # The block cursor must sit on top of "a" — inverse-video "a".
    assert _wait_for(lambda: f"{ESC}[7ma{ESC}[0m" in _frame(inst))
    inst.unmount()


def test_color_prop_wraps_displayed_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst, _ = _mount(
        TextInput(initial_value="hi", color="cyan"), monkeypatch=monkeypatch
    )
    # Body colour sequence appears around the text.
    assert _wait_for(lambda: f"{ESC}[36m" in _frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Composition — TextInput inside a Box with sibling Text
# ---------------------------------------------------------------------------


def test_text_input_inside_box_with_sibling_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = Box(
        Text("label"),
        TextInput(initial_value="value"),
    )
    inst, _ = _mount(tree, monkeypatch=monkeypatch, columns=30)
    assert _wait_for(
        lambda: "label" in _visible(_frame(inst))
        and "value" in _visible(_frame(inst))
    )
    inst.unmount()


def test_text_input_box_props_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """``padding`` / ``borderStyle`` (box props) are forwarded to the Box."""
    inst, _ = _mount(
        TextInput(initial_value="hi", padding=1),
        monkeypatch=monkeypatch,
        columns=20,
        rows=6,
    )
    # Padding adds blank lines around the text — we should see "hi" plus
    # at least one blank line above/below.
    assert _wait_for(lambda: "hi" in _visible(_frame(inst)))
    frame = _frame(inst)
    # Newline count > 1 indicates padding rows.
    assert frame.count("\n") > 1
    inst.unmount()


# ---------------------------------------------------------------------------
# Integration: full editing sequence
# ---------------------------------------------------------------------------


def test_integration_type_delete_retype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Type 'hello', Backspace twice, type 'p'.

    Final buffer should be "help".
    """
    feed: list[bytes] = [
        b"h", b"e", b"l", b"l", b"o",
        b"\x7f", b"\x7f",  # Backspace × 2 → "hel"
        b"p",
    ]
    inst, _ = _mount(TextInput(), monkeypatch=monkeypatch, feed=feed)
    assert _wait_for(lambda: "help" in _visible(_frame(inst)))
    inst.unmount()


def test_integration_ctrl_u_then_retype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Type 'abc', Ctrl+U (clear left), then type 'XYZ'."""
    feed: list[bytes] = [
        b"a", b"b", b"c",
        b"\x15",       # Ctrl+U → "" (cursor was at end → whole buffer)
        b"X", b"Y", b"Z",
    ]
    inst, _ = _mount(TextInput(), monkeypatch=monkeypatch, feed=feed)
    assert _wait_for(
        lambda: "XYZ" in _visible(_frame(inst))
        and "abc" not in _visible(_frame(inst))
    )
    inst.unmount()


# ===========================================================================
# PR2 — multi-line, selection, paste
# ===========================================================================


# ---------------------------------------------------------------------------
# Public helpers — cursor_line / cursor_column
# ---------------------------------------------------------------------------


def test_cursor_line_counts_preceding_newlines() -> None:
    assert cursor_line("abc", 0) == 0
    assert cursor_line("abc\ndef", 3) == 0
    assert cursor_line("abc\ndef", 4) == 1
    assert cursor_line("abc\ndef\nghi", 9) == 2


def test_cursor_column_resets_after_newline() -> None:
    assert cursor_column("abc", 0) == 0
    assert cursor_column("abc", 3) == 3
    assert cursor_column("abc\ndef", 4) == 0
    assert cursor_column("abc\ndef", 7) == 3


def test_cursor_line_clamps_past_end() -> None:
    assert cursor_line("ab", 99) == 0
    assert cursor_line("ab\ncd", 99) == 1


def test_cursor_column_clamps_negative() -> None:
    assert cursor_column("abc", -5) == 0


# ---------------------------------------------------------------------------
# multiline=True — Enter inserts newline
# ---------------------------------------------------------------------------


def test_multiline_enter_inserts_newline(monkeypatch: pytest.MonkeyPatch) -> None:
    """In multiline mode Enter inserts ``\\n`` instead of submitting."""
    submitted: list[str] = []
    changes: list[str] = []
    feed: list[bytes] = [b"a", b"b", b"\r", b"c"]
    inst, _ = _mount(
        TextInput(
            multiline=True,
            on_submit=submitted.append,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "ab\nc"))
    inst.unmount()
    # on_submit must NOT fire in multiline mode.
    assert submitted == []


def test_singleline_enter_still_submits(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR1 behaviour is preserved when ``multiline=False`` (default)."""
    submitted: list[str] = []
    feed: list[bytes] = [b"\r"]
    inst, _ = _mount(
        TextInput(initial_value="value", on_submit=submitted.append),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: len(submitted) == 1)
    inst.unmount()
    assert submitted == ["value"]


def test_multiline_renders_each_line_on_its_own_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-line value splits into multiple Text rows inside the Box."""
    inst, _ = _mount(
        TextInput(initial_value="ab\ncd", multiline=True),
        monkeypatch=monkeypatch,
        columns=20,
        rows=6,
    )
    # Both rows should be visible, and they sit on different lines.
    assert _wait_for(
        lambda: "ab" in _visible(_frame(inst))
        and "cd" in _visible(_frame(inst))
    )
    # More than one newline in the raw frame indicates multi-row layout.
    assert _wait_for(lambda: _frame(inst).count("\n") >= 2)
    inst.unmount()


# ---------------------------------------------------------------------------
# ArrowUp / ArrowDown — cross-line navigation
# ---------------------------------------------------------------------------


def test_arrow_down_moves_to_next_line_same_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Down arrow on line 0 col 1 → line 1 col 1.

    Initial value "abc\\ndef", cursor at end (offset 7, after 'f'). Up
    moves to line 0 col 3 (end of "ab"). Home moves to line 0 col 0.
    Right moves to line 0 col 1 (offset 1). Down → offset 5 (line 1 col
    1, between 'd' and 'e'). Type 'X' inserts at offset 5 →
    "abc\\ndXef".
    """
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[A",  # Up    → line 0 col 3
        b"\x1b[H",  # Home  → line 0 col 0 (offset 0)
        b"\x1b[C",  # Right → line 0 col 1 (offset 1)
        b"\x1b[B",  # Down  → offset 5 (line 1 col 1, between 'd' and 'e')
        b"X",
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abc\ndef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abc\ndXef"))
    inst.unmount()


def test_arrow_up_moves_to_previous_line_same_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Up arrow on line 1 col 1 → line 0 col 1.

    Initial value "abc\\ndef", cursor at end (offset 7). Left → offset 6
    (line 1 col 2). Left → offset 5 (line 1 col 1). Up → offset 1 (line
    0 col 1, between 'a' and 'b'). Type 'X' inserts at offset 1 →
    "aXbc\\ndef".
    """
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[D",  # Left  → line 1 col 2 (between 'e' and 'f')
        b"\x1b[D",  # Left  → line 1 col 1 (between 'd' and 'e')
        b"\x1b[A",  # Up    → line 0 col 1 (between 'a' and 'b')
        b"X",
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abc\ndef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "aXbc\ndef"))
    inst.unmount()


def test_arrow_up_on_first_line_moves_to_line_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Up arrow on the first line clamps to that line's start (col 0).

    Initial value "abc\\ndef", cursor at end (offset 7). Up → line 0 col
    3. Home → line 0 col 0 (offset 0). Right → line 0 col 1 (offset 1).
    Up — already on line 0, so cursor clamps to line 0 col 0. Type 'X'
    inserts at offset 0 → "Xabc\\ndef".
    """
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[A",  # Up    → line 0 col 3
        b"\x1b[H",  # Home  → offset 0
        b"\x1b[C",  # Right → offset 1 (col 1 of line 0)
        b"\x1b[A",  # Up    — first line, so cursor → col 0 (offset 0)
        b"X",
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abc\ndef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xabc\ndef"))
    inst.unmount()


def test_arrow_down_on_last_line_moves_to_line_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Down arrow on the last line clamps to that line's end."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[D",   # Left once — line 1 col 2 (between 'e' and 'f')
        b"\x1b[B",   # Down — last line, so cursor → end of line 1 (after 'f')
        b"X",        # type → appends at end
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abc\ndef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abc\ndefX"))
    inst.unmount()


def test_arrow_down_to_shorter_line_clamps_to_line_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Down to a line shorter than the current column clamps to its end.

    Initial value "abcde\\nxy" — cursor after 'e' (col 5 of line 0).
    Down → line 1 col 5, but line 1 has length 2, so clamp to col 2
    (end of "xy"). Type 'X' → "abcde\\nxyX".
    """
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[B", b"X"]
    inst, _ = _mount(
        TextInput(
            initial_value="abcde\nxy",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abcde\nxyX"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Shift + Arrow / Home / End — selection
# ---------------------------------------------------------------------------


def test_shift_left_extends_selection_backward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shift+Left extends selection to the left; typing replaces the range."""
    changes: list[str] = []
    feed: list[bytes] = [
        # Cursor starts at end of "abcd" (offset 4).
        b"\x1b[1;2D",  # Shift+Left → selection [3, 4)
        b"\x1b[1;2D",  # Shift+Left → selection [2, 4)
        b"X",          # Replace [2, 4) with "X" → "abX"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abX"))
    inst.unmount()


def test_shift_right_extends_selection_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shift+Right extends selection to the right; typing replaces it."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home → cursor at 0
        b"\x1b[1;2C",    # Shift+Right → selection [0, 1)
        b"\x1b[1;2C",    # Shift+Right → selection [0, 2)
        b"X",            # Replace [0, 2) with "X" → "Xcd"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xcd"))
    inst.unmount()


def test_shift_home_extends_selection_to_line_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shift+Home extends selection to the start of the current line."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[D",       # Left once — cursor at 3
        # Shift+Home (xterm: ESC [ 1 ; 2 H)
        b"\x1b[1;2H",
        b"X",            # Replace [0, 3) with "X" → "Xd"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "Xd"))
    inst.unmount()


def test_shift_end_extends_selection_to_line_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shift+End extends selection to the end of the current line."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home — cursor at 0
        b"\x1b[D",       # Left (no-op at 0)
        # Shift+End (xterm: ESC [ 1 ; 2 F)
        b"\x1b[1;2F",
        b"X",            # Replace [0, 4) with "X" → "X"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "X"))
    inst.unmount()


def test_selection_collapses_after_plain_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain Right after a Shift+Right collapses the selection.

    Sequence: Home → Shift+Right (selection [0, 1)) → Right (clears
    selection, cursor moves to offset 2) → type 'X' → inserts at offset 2.
    """
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home → cursor 0
        b"\x1b[1;2C",    # Shift+Right → selection [0, 1)
        b"\x1b[C",       # Right → clears selection, cursor 2
        b"X",            # Insert at offset 2 → "abXcd"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "abXcd"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Selection rendering — inverse video
# ---------------------------------------------------------------------------


def test_selection_renders_with_inverse_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The selected range is wrapped in SGR inverse video (``\\x1b[7m``)."""
    feed: list[bytes] = [
        b"\x1b[H",       # Home
        b"\x1b[1;2C",    # Shift+Right → [0, 1)
        b"\x1b[1;2C",    # Shift+Right → [0, 2)
    ]
    inst, _ = _mount(
        TextInput(initial_value="abcd"), monkeypatch=monkeypatch, feed=feed
    )
    # The first two chars ("ab") must be wrapped in inverse video.
    assert _wait_for(
        lambda: f"{ESC}[7mab{ESC}[0m" in _frame(inst)
        and "cd" in _frame(inst)
    )
    inst.unmount()


# ---------------------------------------------------------------------------
# Backspace / Delete on selection
# ---------------------------------------------------------------------------


def test_backspace_on_selection_deletes_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backspace with an active selection deletes the whole range."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home
        b"\x1b[1;2C",    # Shift+Right → [0, 1)
        b"\x1b[1;2C",    # Shift+Right → [0, 2)
        b"\x7f",         # Backspace → delete [0, 2)
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "cd"))
    inst.unmount()


def test_delete_on_selection_deletes_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete with an active selection also deletes the whole range."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home
        b"\x1b[1;2C",    # Shift+Right → [0, 1)
        b"\x1b[1;2C",    # Shift+Right → [0, 2)
        b"\x1b[3~",      # Delete → delete [0, 2)
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "cd"))
    inst.unmount()


def test_typing_replaces_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A printable key with an active selection replaces the range."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",
        b"\x1b[1;2C",
        b"\x1b[1;2C",
        b"XY",  # Both chars collapse into one edit (selection collapses
                # after the first).
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # After "X": selection [0,2) replaced → "Xcd", cursor at 1, selection None.
    # After "Y": insert at 1 → "XYcd".
    assert _wait_for(lambda: _last_change_is(changes, "XYcd"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Alt+Backspace == Ctrl+W
# ---------------------------------------------------------------------------


def test_alt_backspace_deletes_previous_word(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alt+Backspace behaves like Ctrl+W (backward-kill-word)."""
    changes: list[str] = []
    # Alt+Backspace arrives as ESC + DEL → bytes "\x1b\x7f".
    feed: list[bytes] = [b"\x1b\x7f"]
    inst, _ = _mount(
        TextInput(
            initial_value="foo bar",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "foo "))
    inst.unmount()


# ---------------------------------------------------------------------------
# Paste — bracketed paste sequence
# ---------------------------------------------------------------------------


def test_bracketed_paste_inserts_as_single_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bracketed paste ``\\x1b[200~ ... \\x1b[201~`` produces one on_change."""
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[200~hello\x1b[201~"]
    inst, _ = _mount(
        TextInput(
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "hello"))
    inst.unmount()
    # Crucially, only ONE on_change fired (not one per character).
    assert changes == ["hello"]


def test_bracketed_paste_replaces_active_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paste over an active selection replaces the selected range."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[H",       # Home
        b"\x1b[1;2C",    # Shift+Right → [0, 1)
        b"\x1b[1;2C",    # Shift+Right → [0, 2)
        b"\x1b[200~XY\x1b[201~",  # Paste "XY"
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="abcd",
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "XYcd"))
    inst.unmount()


def test_bracketed_paste_in_multiline_keeps_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-line paste in multiline mode preserves newlines verbatim."""
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[200~line1\nline2\x1b[201~"]
    inst, _ = _mount(
        TextInput(
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=30,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "line1\nline2"))
    inst.unmount()


def test_bracketed_paste_in_singleline_strips_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-line paste collapses newlines to spaces."""
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[200~line1\nline2\x1b[201~"]
    inst, _ = _mount(
        TextInput(
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    assert _wait_for(lambda: _last_change_is(changes, "line1 line2"))
    inst.unmount()


def test_bracketed_paste_respects_max_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paste that would exceed ``max_length`` is dropped entirely."""
    changes: list[str] = []
    feed: list[bytes] = [b"\x1b[200~hello\x1b[201~"]
    inst, _ = _mount(
        TextInput(
            initial_value="abc",
            max_length=5,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
    )
    # Buffer "abc" + paste "hello" → 8 chars > 5 → dropped.
    time.sleep(0.2)
    assert changes == []
    assert "abc" in _visible(_frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Ctrl+K / Ctrl+U — now line-scoped (PR1 was buffer-scoped)
# ---------------------------------------------------------------------------


def test_ctrl_k_kills_to_end_of_current_line_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In multiline mode Ctrl+K kills to end of the *current* line only.

    Initial value "ab\\ncdef"; default cursor sits at end of line 1
    (offset 7). Up → offset 2 (end of "ab", line 0). Left → offset 1
    (between 'a' and 'b'). Ctrl+K removes [1, 2) → kills 'b' only.
    The newline + line 1 are preserved.
    """
    changes: list[str] = []
    feed: list[bytes] = [
        b"\x1b[A",   # Up    → line 0 col 2 (offset 2, end of "ab")
        b"\x1b[D",   # Left  → offset 1 (between 'a' and 'b')
        b"\x0b",     # Ctrl+K — kill [1, 2) → removes 'b' only.
    ]
    inst, _ = _mount(
        TextInput(
            initial_value="ab\ncdef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "a\ncdef"))
    inst.unmount()


def test_ctrl_u_kills_to_start_of_current_line_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In multiline mode Ctrl+U kills to start of the *current* line only."""
    changes: list[str] = []
    # Initial value "ab\\ncdef"; cursor at end of line 1.
    # Ctrl+U on line 1 kills "cdef" (everything before cursor on line 1).
    feed: list[bytes] = [b"\x15"]  # Ctrl+U at cursor end → kill to line start.
    inst, _ = _mount(
        TextInput(
            initial_value="ab\ncdef",
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "ab\n"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Multiline integration — type + newline + backspace roundtrip
# ---------------------------------------------------------------------------


def test_multiline_type_and_backspace_across_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enter creates a newline; Backspace at col 0 of line 1 joins lines."""
    changes: list[str] = []
    feed: list[bytes] = [
        b"a", b"b",
        b"\r",      # Enter → "ab\n"
        b"c",       # → "ab\nc"
        b"\x7f",    # Backspace → "ab\n"
        b"\x7f",    # Backspace at col 0 line 1 → "ab" (removes newline)
    ]
    inst, _ = _mount(
        TextInput(
            multiline=True,
            on_change=changes.append,
        ),
        monkeypatch=monkeypatch,
        feed=feed,
        columns=20,
        rows=6,
    )
    assert _wait_for(lambda: _last_change_is(changes, "ab"))
    inst.unmount()


# ---------------------------------------------------------------------------
# Props — multiline default
# ---------------------------------------------------------------------------


def test_multiline_default_is_false() -> None:
    el = TextInput()
    assert el.props["multiline"] is False


def test_multiline_prop_captured() -> None:
    el = TextInput(multiline=True)
    assert el.props["multiline"] is True
