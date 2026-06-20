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
from pyink.externals.text_input import _TextInputImpl
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
