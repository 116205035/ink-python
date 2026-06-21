"""``TextInput`` — single-line + multi-line text input (Phase 4 PR1 + PR2).

Mirrors ``ink-text-input`` at the level Phase 4 needs:

* PR1 — single-line editing with a visible cursor, basic Emacs-style
  word / line kill shortcuts, password masking and ``max_length``
  truncation.
* PR2 — multi-line editing (Enter inserts ``\\n``), cross-line cursor
  movement (ArrowUp / ArrowDown), selection (Shift + arrows / Home /
  End, with inverse-video rendering), and bracketed-paste handling.

Design (per PRD Decision 1):

* ``TextInput`` is a factory returning an :class:`Element` whose
  ``type`` is the :func:`_TextInputImpl` function component. The
  factory itself never runs hooks; only the wrapped function does,
  when the reconciler mounts it.
* Three writable :class:`Signal` s live inside the component: ``value``
  (current string), ``cursor`` (character offset,
  ``0 <= cursor <= len(value)``), and ``selection`` (``None`` or a
  ``[start, end)`` pair). Every keypress updates one or more; the
  layout-time callable child subscribes to all of them so the render
  loop re-paints on each change.
* Callbacks (``on_change`` / ``on_submit``) are captured in
  :class:`Ref` s refreshed every mount — this lets the caller swap
  callbacks between mounts without resubscribing the input handler.
  The handler closure is registered exactly once via
  :func:`pyink.hooks.use_input`.

Cursor rendering (per PRD Section "cursor 渲染细节"):

* ``bar`` — a one-cell inverse-video space is inserted at the cursor
  position; the surrounding characters keep their normal style. This
  matches the VSCode / vim insert-mode feel.
* ``block`` — the character *under* the cursor is rendered with
  inverse video; if the cursor is at end-of-input an inverse space is
  shown instead. Classic terminal "overwrite" cursor.
* ``underline`` — the character under the cursor gets an underline; at
  end-of-input a bare underline escape with no glyph is shown.

Selection rendering (PR2): the ``[start, end)`` range of the buffer is
wrapped in SGR "inverse video" escapes. The escapes are inserted
*before* the cursor splice so the selection paint is independent of the
cursor paint; the layout measure pass strips CSI sequences so neither
selection nor cursor escapes inflate the column budget.

Paste (PR2): when :class:`pyink.render.keys.Key` arrives with a
``paste`` payload (delivered by the Terminal's bracketed-paste
dispatcher) the whole payload is spliced into the buffer at the cursor
in a single edit, replacing any active selection, and ``on_change``
fires exactly once.

Out of scope (Phase 4):

* Vim mode (Phase 4.5).
* Complex input history (Up / Down scrolling previous values).

Esc / Ctrl+C are deliberately *not* swallowed so the surrounding
``exit_on_ctrl_c`` pipeline still owns them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element, create_element
from pyink.core.signal import Ref, Signal, effect, ref, signal
from pyink.hooks.input import use_input
from pyink.render.ansi import apply_style, parse_color
from pyink.render.keys import Key

__all__ = [
    "TextInput",
    "cursor_column",
    "cursor_line",
]

#: Allowed values for the ``cursor_style`` prop.
CursorStyle = Literal["bar", "block", "underline"]

#: SGR "inverse video" sequence — used by ``bar`` / ``block`` cursors and
#: by selection rendering.
_SGR_INVERSE = "\x1b[7m"
#: SGR "underline" sequence — used by the ``underline`` cursor.
_SGR_UNDERLINE = "\x1b[4m"
#: SGR reset — closes every cursor / selection SGR run.
_SGR_RESET = "\x1b[0m"


# ---------------------------------------------------------------------------
# Line / column helpers (PRD Decision 2)
# ---------------------------------------------------------------------------


def cursor_line(value: str, cursor: int) -> int:
    """Return the 0-based line number of ``cursor`` within ``value``.

    The line number is the count of ``\\n`` characters strictly before
    the cursor offset. Line 0 is the first line; an offset past the
    final newline sits on the last line.
    """
    clamped = max(0, min(cursor, len(value)))
    return value[:clamped].count("\n")


def cursor_column(value: str, cursor: int) -> int:
    """Return the 0-based column number of ``cursor`` within its line.

    The column resets to 0 at the start of every line; for an offset on
    line N it is ``cursor - (index of the line's leading ``\\n``) - 1``.
    On line 0 (no preceding newline) the column is simply ``cursor``.
    """
    clamped = max(0, min(cursor, len(value)))
    last_newline = value.rfind("\n", 0, clamped)
    if last_newline == -1:
        return clamped
    return clamped - last_newline - 1


# ---------------------------------------------------------------------------
# Internal helpers — word boundary, cursor cell, displayed text
# ---------------------------------------------------------------------------


def _is_word_char(ch: str) -> bool:
    """``True`` for characters that count as part of a word (alphanumeric / underscore)."""
    return ch.isalnum() or ch == "_"


def _line_bounds(value: str, cursor: int) -> tuple[int, int]:
    """Return the ``[start, end)`` offsets of the line containing ``cursor``.

    ``end`` is the offset of the next ``\\n`` (or ``len(value)`` if the
    cursor is on the last line). ``start`` is the offset right after the
    previous ``\\n`` (or 0). Both bounds are exclusive of the newline
    character itself.
    """
    last_newline = value.rfind("\n", 0, cursor)
    start = 0 if last_newline == -1 else last_newline + 1
    next_newline = value.find("\n", cursor)
    end = len(value) if next_newline == -1 else next_newline
    return start, end


def _line_start(value: str, cursor: int) -> int:
    return _line_bounds(value, cursor)[0]


def _line_end(value: str, cursor: int) -> int:
    return _line_bounds(value, cursor)[1]


def _offset_above(value: str, cursor: int) -> int | None:
    """Offset of the same column on the previous line, or ``None``.

    Returns the offset of line 0's start (column 0) when ``cursor`` is
    on the first line — the caller treats this as "stay on the first
    line" by clamping to ``_line_start``.
    """
    start, _ = _line_bounds(value, cursor)
    if start == 0:
        return None
    # The newline that ends the previous line sits at ``start - 1``.
    prev_end = start - 1
    prev_start = _line_start(value, prev_end)
    col = cursor - start
    target = prev_start + col
    prev_line_end = _line_end(value, prev_end)
    return min(target, prev_line_end)


def _offset_below(value: str, cursor: int) -> int | None:
    """Offset of the same column on the next line, or ``None``.

    Returns the offset of the last line's end when ``cursor`` is on the
    last line — caller treats this as "stay on the last line" by
    clamping to ``_line_end``.
    """
    _, end = _line_bounds(value, cursor)
    if end >= len(value):
        return None
    # The next line begins right after the newline at ``end``.
    next_start = end + 1
    next_end = _line_end(value, next_start)
    col = cursor - _line_start(value, cursor)
    target = next_start + col
    return min(target, next_end)


def _word_boundary_left(value: str, cursor: int) -> int:
    """Offset of the start of the word (or whitespace run) before ``cursor``.

    Mirrors readline's ``backward-word`` motion: skip whitespace first,
    then skip the alphanumeric/underscore run that precedes it.
    """
    pos = cursor
    while pos > 0 and value[pos - 1].isspace():
        pos -= 1
    while pos > 0 and _is_word_char(value[pos - 1]):
        pos -= 1
    return pos


def _word_boundary_right(value: str, cursor: int) -> int:
    """Offset of the start of the next word (or whitespace run) after ``cursor``.

    Mirrors readline's ``forward-word`` motion: skip the current
    alphanumeric/underscore run, then skip the trailing whitespace.
    """
    pos = cursor
    while pos < len(value) and _is_word_char(value[pos]):
        pos += 1
    while pos < len(value) and value[pos].isspace():
        pos += 1
    return pos


def _cursor_cell(
    style: CursorStyle,
    *,
    char: str,
    cursor_color: str | None,
) -> str:
    """Render a single cursor cell.

    Parameters
    ----------
    style:
        One of ``"bar"`` / ``"block"`` / ``"underline"``.
    char:
        The character at the cursor position. Empty string means the
        cursor sits at end-of-input and a space cell should be used.
    cursor_color:
        Optional colour spec applied to the cursor cell.
    """
    cell = char if char else " "
    if style == "bar":
        # A bar cursor occupies its own cell — an inverse-video space.
        # ``cursor_color`` tints the foreground so terminals that paint
        # ``inverse`` by swapping fg/bg render the bar in that colour.
        if cursor_color:
            body = parse_color(cursor_color, type_="foreground")
            open_seq = f"\x1b[{body}m{_SGR_INVERSE}" if body else _SGR_INVERSE
        else:
            open_seq = _SGR_INVERSE
        return f"{open_seq} {_SGR_RESET}"
    if style == "block":
        # Inverse video on the cursor character itself. Colour is
        # applied under the inverse so the cell reads as coloured-on-
        # inverted background.
        if cursor_color:
            body = parse_color(cursor_color, type_="foreground")
            open_seq = f"\x1b[{body}m{_SGR_INVERSE}" if body else _SGR_INVERSE
        else:
            open_seq = _SGR_INVERSE
        return f"{open_seq}{cell}{_SGR_RESET}"
    # underline — apply underline (and optional colour) to the cursor
    # character without inverting it.
    if cursor_color:
        body = parse_color(cursor_color, type_="foreground")
        open_seq = f"\x1b[{body}m{_SGR_UNDERLINE}" if body else _SGR_UNDERLINE
    else:
        open_seq = _SGR_UNDERLINE
    return f"{open_seq}{cell}{_SGR_RESET}"


def _apply_selection(
    displayed: str,
    *,
    value_start: int,
    value_end: int,
    sel_start: int,
    sel_end: int,
) -> str:
    """Wrap the ``[sel_start, sel_end)`` slice of ``displayed`` in inverse video.

    ``value_start`` / ``value_end`` are the half-open range of the
    underlying value that ``displayed`` corresponds to (selection is
    per-line — only the slice that overlaps the current line is
    highlighted). Offsets outside the range contribute no escape.
    """
    if sel_end <= value_start or sel_start >= value_end:
        return displayed
    # Translate selection offsets into the local coordinate of this
    # displayed run.
    local_start = max(0, sel_start - value_start)
    local_end = min(value_end, sel_end) - value_start
    local_start = max(0, min(local_start, len(displayed)))
    local_end = max(local_start, min(local_end, len(displayed)))
    before = displayed[:local_start]
    body = displayed[local_start:local_end]
    after = displayed[local_end:]
    if not body:
        return displayed
    return f"{before}{_SGR_INVERSE}{body}{_SGR_RESET}{after}"


def _build_displayed_line(
    line_text: str,
    cursor_in_line: bool,
    cursor: int,
    line_start: int,
    *,
    cursor_style: CursorStyle,
    cursor_color: str | None,
    selection: tuple[int, int] | None,
) -> str:
    """Render one line of the buffer with cursor + selection overlays.

    Parameters
    ----------
    line_text:
        Visible text for the line (already mask-applied, no newline).
    cursor_in_line:
        Whether the cursor sits in this line (any column, including the
        end-of-line position right before a ``\\n``).
    cursor:
        Absolute cursor offset.
    line_start:
        Offset of the first character of this line.
    cursor_style / cursor_color:
        Forwarded from props.
    selection:
        Active selection as an absolute ``[start, end)`` pair, or
        ``None``.
    """
    line_end = line_start + len(line_text)

    if selection is not None:
        sel_start, sel_end = selection
    else:
        sel_start = sel_end = -1

    if not cursor_in_line:
        # No cursor here — just paint any selection overlap and return.
        return _apply_selection(
            line_text,
            value_start=line_start,
            value_end=line_end,
            sel_start=sel_start,
            sel_end=sel_end,
        )

    local_cursor = cursor - line_start

    if cursor_style == "bar":
        # Bar inserts an extra cell at the cursor position; selection
        # paint applies to the characters on either side of the bar.
        before = line_text[:local_cursor]
        rest = line_text[local_cursor:]
        before = _apply_selection(
            before,
            value_start=line_start,
            value_end=line_start + local_cursor,
            sel_start=sel_start,
            sel_end=sel_end,
        )
        rest = _apply_selection(
            rest,
            value_start=cursor,
            value_end=line_end,
            sel_start=sel_start,
            sel_end=sel_end,
        )
        cursor_cell = _cursor_cell(
            cursor_style, char="", cursor_color=cursor_color
        )
        return before + cursor_cell + rest

    # Block / underline sit *on* a character. At end-of-line (cursor ==
    # line_end) there is no character to sit on — emit the cursor cell
    # with an empty underlying char (renders as a styled space).
    before = line_text[:local_cursor]
    rest = line_text[local_cursor:]
    if rest:
        target = rest[0]
        after = rest[1:]
    else:
        target = ""
        after = ""
    cursor_cell = _cursor_cell(
        cursor_style, char=target, cursor_color=cursor_color
    )
    # Selection paint wraps the non-cursor characters. The character
    # under the cursor keeps its cursor styling (block / underline),
    # which already swaps video / underline; selection inverse would
    # double-invert it back to normal, so we deliberately skip the
    # cursor cell when painting selection.
    before = _apply_selection(
        before,
        value_start=line_start,
        value_end=line_start + local_cursor,
        sel_start=sel_start,
        sel_end=sel_end,
    )
    after = _apply_selection(
        after,
        value_start=cursor + 1,
        value_end=line_end,
        sel_start=sel_start,
        sel_end=sel_end,
    )
    return before + cursor_cell + after


def _split_lines(value: str) -> list[tuple[int, str]]:
    """Split ``value`` into ``[(line_start_offset, line_text), ...]``.

    Trailing empty line: when ``value`` ends with ``\\n`` we emit a
    final ``("")]`` entry so the cursor can rest on the empty last
    line. When ``value`` is empty we emit a single ``[(0, "")]`` row.
    """
    if value == "":
        return [(0, "")]
    out: list[tuple[int, str]] = []
    offset = 0
    for piece in value.split("\n"):
        out.append((offset, piece))
        offset += len(piece) + 1  # +1 for the consumed newline.
    return out


# ---------------------------------------------------------------------------
# Function component
# ---------------------------------------------------------------------------


def _TextInputImpl(**props: Any) -> Element:
    """Function component body — runs inside the reconciler render context.

    Owns the ``value`` / ``cursor`` / ``selection`` signals, registers
    the keyboard handler via :func:`use_input`, and returns a :func:`Box`
    containing one :func:`Text` per rendered line. Each :func:`Text`
    child is a callable that re-evaluates on every signal write.
    """
    initial_value: str = props["initial_value"]
    placeholder: str | None = props["placeholder"]
    on_change: Callable[[str], None] | None = props["on_change"]
    on_submit: Callable[[str], None] | None = props["on_submit"]
    on_cursor_change: Callable[[int], None] | None = props["on_cursor_change"]
    mask: str | None = props["mask"]
    max_length: int | None = props["max_length"]
    color: str | None = props["color"]
    cursor_color: str | None = props["cursor_color"]
    cursor_style: CursorStyle = props["cursor_style"]
    is_active: bool = props["is_active"]
    multiline: bool = props["multiline"]
    box_props: dict[str, Any] = props["box_props"]

    # Initial cursor sits at end of the initial value — matches
    # ink-text-input and the common "open input, type to append" UX.
    value: Signal[str] = signal(initial_value)
    cursor: Signal[int] = signal(len(initial_value))
    selection: Signal[tuple[int, int] | None] = signal(None)

    # Keep the latest callback without resubscribing the input handler.
    # ``use_input``'s closure captures the ref objects, so updating
    # ``.value`` here is enough to redirect future keypresses.
    on_change_ref: Ref[Callable[[str], None] | None] = ref(on_change)
    on_submit_ref: Ref[Callable[[str], None] | None] = ref(on_submit)
    on_cursor_change_ref: Ref[Callable[[int], None] | None] = ref(on_cursor_change)
    on_change_ref.value = on_change
    on_submit_ref.value = on_submit
    on_cursor_change_ref.value = on_cursor_change

    def _notify_change(old: str, new: str) -> None:
        if new != old:
            cb = on_change_ref.value
            if cb is not None:
                cb(new)

    def _clear_selection() -> None:
        if selection.value is not None:
            selection.value = None

    def _set_cursor(pos: int, *, keep_selection: bool = False) -> None:
        clamped = max(0, min(pos, len(value.value)))
        if not keep_selection:
            _clear_selection()
        cursor.value = clamped

    def _extend_selection(target: int) -> None:
        """Move the cursor to ``target`` and grow the selection.

        On the first Shift+motion the selection is anchored at the
        current cursor; subsequent Shift+motions keep the same anchor
        and slide the cursor. ``target`` is clamped to ``[0, len(value)]``.

        The anchor is the *opposite* end of the existing selection from
        the current cursor — when the cursor sits at ``sel[0]`` the
        anchor is ``sel[1]``, and vice versa. This keeps Shift+Left /
        Shift+Right symmetric: extending right past the original anchor
        then back leftward properly collapses the range.
        """
        old_cursor = cursor.value
        cur_sel = selection.value
        if cur_sel is not None:
            sel_start, sel_end = cur_sel
            # The anchor is whichever end of the selection the cursor is
            # currently *not* sitting on.
            if old_cursor == sel_start:
                anchor = sel_end
            elif old_cursor == sel_end:
                anchor = sel_start
            else:
                # Cursor drifted outside the selection (shouldn't happen
                # in normal use); fall back to the nearest end.
                anchor = sel_end if abs(old_cursor - sel_end) < abs(
                    old_cursor - sel_start
                ) else sel_start
        else:
            anchor = old_cursor
        new_cursor = max(0, min(target, len(value.value)))
        cursor.value = new_cursor
        if new_cursor == anchor:
            selection.value = None
        else:
            lo = min(anchor, new_cursor)
            hi = max(anchor, new_cursor)
            selection.value = (lo, hi)

    def _splice(
        text: str,
        *,
        start: int,
        end: int,
        new_cursor: int,
    ) -> bool:
        """Replace ``value[start:end]`` with ``text``.

        Honours ``max_length``: returns ``False`` (no edit) if the
        resulting length would exceed the cap. On success writes the
        new value, advances the cursor, clears the selection, fires
        ``on_change`` once, and returns ``True``.
        """
        old_value = value.value
        new_value = old_value[:start] + text + old_value[end:]
        if max_length is not None and len(new_value) > max_length:
            return False
        value.value = new_value
        clamped = max(0, min(new_cursor, len(new_value)))
        cursor.value = clamped
        _clear_selection()
        _notify_change(old_value, new_value)
        return True

    def _delete_selection_or_deselect() -> bool:
        """If a selection is active, delete it and return ``True``.

        Returns ``False`` when there is no active selection so the
        caller can fall through to character-based behaviour.
        """
        sel = selection.value
        if sel is None:
            return False
        start, end = sel
        _splice("", start=start, end=end, new_cursor=start)
        return True

    def handle_key(key: Key) -> None:
        if not is_active:
            return

        old_value = value.value
        old_cursor = cursor.value

        # ----------------------------------------------------------------
        # Paste — single multi-char edit.
        # ----------------------------------------------------------------
        if key.paste:
            payload = key.paste
            if multiline:
                # Multi-line paste: keep newlines verbatim.
                insert_text = payload
            else:
                # Single-line paste: strip newlines so a pasted paragraph
                # collapses to one row (matches ink-text-input).
                insert_text = payload.replace("\r\n", " ").replace(
                    "\n", " "
                ).replace("\r", " ")
            sel = selection.value
            if sel is not None:
                start, end = sel
            else:
                start = end = old_cursor
            _splice(
                insert_text,
                start=start,
                end=end,
                new_cursor=start + len(insert_text),
            )
            return

        # ----------------------------------------------------------------
        # Selection-aware editing keys.
        # ----------------------------------------------------------------
        # Any non-Shift editing key (typing, Backspace, Delete, Ctrl+K/U/W,
        # paste, etc.) collapses the selection. ``_splice`` /
        # ``_delete_selection_or_deselect`` clear it; for plain
        # navigation we go through ``_set_cursor``.

        # Printable single character (no Ctrl / Alt).
        if (
            key.input
            and not key.ctrl
            and not key.alt
            and not key.up_arrow
            and not key.down_arrow
            and not key.left_arrow
            and not key.right_arrow
            and not key.return_key
            and not key.tab
            and not key.backspace
            and not key.delete
            and not key.escape
            and not key.home
            and not key.end
            and not key.paste_start
            and not key.paste_end
            and not key.paste
            and key.input.isprintable()
        ):
            sel = selection.value
            if sel is not None:
                start, end = sel
            else:
                start = end = old_cursor
            _splice(key.input, start=start, end=end, new_cursor=start + 1)
            return

        # Backspace — Alt+Backspace falls through to Ctrl+W; plain /
        # Ctrl+Backspace deletes one char or selection.
        if key.backspace:
            if key.alt and not key.ctrl:
                # Alt+Backspace == Ctrl+W (PRD PR2 requirement).
                _do_backward_kill_word(old_value, old_cursor)
                return
            if _delete_selection_or_deselect():
                return
            if key.ctrl:
                # Ctrl+Backspace — backward-kill-word, same as Ctrl+W.
                _do_backward_kill_word(old_value, old_cursor)
                return
            if old_cursor > 0:
                _splice(
                    "",
                    start=old_cursor - 1,
                    end=old_cursor,
                    new_cursor=old_cursor - 1,
                )
            return

        if key.delete and not key.ctrl and not key.alt:
            if _delete_selection_or_deselect():
                return
            if old_cursor < len(old_value):
                _splice(
                    "",
                    start=old_cursor,
                    end=old_cursor + 1,
                    new_cursor=old_cursor,
                )
            return

        # ----------------------------------------------------------------
        # Selection-extending navigation (Shift + arrows / Home / End).
        # ----------------------------------------------------------------
        if key.shift and not key.ctrl and not key.alt:
            if key.left_arrow:
                _extend_selection(old_cursor - 1)
                return
            if key.right_arrow:
                _extend_selection(old_cursor + 1)
                return
            if key.up_arrow:
                target = _offset_above(old_value, old_cursor)
                if target is None:
                    target = _line_start(old_value, old_cursor)
                _extend_selection(target)
                return
            if key.down_arrow:
                target = _offset_below(old_value, old_cursor)
                if target is None:
                    target = _line_end(old_value, old_cursor)
                _extend_selection(target)
                return
            if key.home:
                _extend_selection(_line_start(old_value, old_cursor))
                return
            if key.end:
                _extend_selection(_line_end(old_value, old_cursor))
                return

        # Ctrl+Shift+Left/Right — extend selection by one word. Optional
        # but cheap to wire up since we already have the helpers.
        if key.shift and key.ctrl and not key.alt:
            if key.left_arrow:
                _extend_selection(_word_boundary_left(old_value, old_cursor))
                return
            if key.right_arrow:
                _extend_selection(_word_boundary_right(old_value, old_cursor))
                return

        # ----------------------------------------------------------------
        # Plain cursor movement (no selection modification).
        # ----------------------------------------------------------------
        if key.left_arrow and not key.ctrl and not key.alt:
            _set_cursor(old_cursor - 1)
            return

        if key.right_arrow and not key.ctrl and not key.alt:
            _set_cursor(old_cursor + 1)
            return

        if key.up_arrow and not key.ctrl and not key.alt:
            target = _offset_above(old_value, old_cursor)
            if target is None:
                _set_cursor(_line_start(old_value, old_cursor))
            else:
                _set_cursor(target)
            return

        if key.down_arrow and not key.ctrl and not key.alt:
            target = _offset_below(old_value, old_cursor)
            if target is None:
                _set_cursor(_line_end(old_value, old_cursor))
            else:
                _set_cursor(target)
            return

        if key.home and not key.ctrl and not key.alt:
            _set_cursor(_line_start(old_value, old_cursor))
            return

        if key.end and not key.ctrl and not key.alt:
            _set_cursor(_line_end(old_value, old_cursor))
            return

        # ----------------------------------------------------------------
        # Ctrl+letter shortcuts — Emacs-style line / word editing.
        # ----------------------------------------------------------------
        if key.ctrl and not key.alt and key.input:
            letter = key.input.lower()
            if letter == "a":
                _set_cursor(0)
                return
            if letter == "e":
                _set_cursor(len(old_value))
                return
            if letter == "k":
                # Kill to end of line — drop everything past the cursor
                # up to (and excluding) the next newline.
                line_end = _line_end(old_value, old_cursor)
                if old_cursor < line_end:
                    _splice(
                        "",
                        start=old_cursor,
                        end=line_end,
                        new_cursor=old_cursor,
                    )
                return
            if letter == "u":
                # Kill to start of line — drop everything from the line
                # start up to the cursor.
                line_start = _line_start(old_value, old_cursor)
                if old_cursor > line_start:
                    _splice(
                        "",
                        start=line_start,
                        end=old_cursor,
                        new_cursor=line_start,
                    )
                return
            if letter == "w":
                _do_backward_kill_word(old_value, old_cursor)
                return

        # ----------------------------------------------------------------
        # Enter — newline (multiline) or submit (single-line).
        # ----------------------------------------------------------------
        if key.return_key and not key.alt:
            if multiline:
                sel = selection.value
                if sel is not None:
                    start, end = sel
                else:
                    start = end = old_cursor
                _splice(
                    "\n",
                    start=start,
                    end=end,
                    new_cursor=start + 1,
                )
                return
            cb = on_submit_ref.value
            if cb is not None:
                cb(value.value)
            return

        # All other keys (Esc, Tab, Alt+X, function keys, …) are
        # intentionally left for the surrounding pipeline to handle.

    def _do_backward_kill_word(value_str: str, cur: int) -> None:
        """Shared Ctrl+W / Alt+Backspace body — delete the previous word.

        If a selection is active it is replaced wholesale; otherwise the
        whitespace + word run to the left of the cursor is removed.
        Mirrors readline's ``backward-kill-word``.
        """
        sel = selection.value
        if sel is not None:
            start, end = sel
            _splice("", start=start, end=end, new_cursor=start)
            return
        if cur <= 0:
            return
        new_cursor = cur
        # Skip whitespace to the left.
        while new_cursor > 0 and value_str[new_cursor - 1].isspace():
            new_cursor -= 1
        # Skip the word to the left.
        while new_cursor > 0 and _is_word_char(value_str[new_cursor - 1]):
            new_cursor -= 1
        if new_cursor != cur:
            _splice(
                "",
                start=new_cursor,
                end=cur,
                new_cursor=new_cursor,
            )

    use_input(handle_key, is_active=is_active)

    # Reactive side-effect: fire ``on_cursor_change`` whenever the
    # internal ``cursor`` signal moves. Subscribing via ``effect`` means
    # we automatically pick up every code path that writes the cursor —
    # keyboard navigation, text edits, selection-extension motions, and
    # programmatic writes from outside — without having to thread a
    # notify call through every ``_set_cursor`` / ``_splice`` helper.
    # ``effect`` also fires once on mount with the initial offset; that
    # mirrors ``on_change``'s "fire on first write" behaviour and lets
    # callers use the callback to seed their own cursor mirror.
    def _notify_cursor_change() -> None:
        cb = on_cursor_change_ref.value
        if cb is not None:
            cb(cursor.value)

    effect(_notify_cursor_change)

    def _render_lines() -> list[str]:
        """Build a list of per-line rendered strings (with cursor + selection)."""
        cur_value = value.value
        cur_cursor = cursor.value
        cur_sel = selection.value

        # Empty buffer with placeholder: render the placeholder on the
        # first row and the cursor at column 0, suppressing multi-line
        # logic entirely.
        if not cur_value and placeholder:
            cursor_cell = _cursor_cell(
                cursor_style, char="", cursor_color=cursor_color
            )
            return [apply_style(placeholder, dimColor=True) + cursor_cell]

        lines: list[str] = []
        for line_start, line_text_raw in _split_lines(cur_value):
            line_text = mask * len(line_text_raw) if mask else line_text_raw
            line_end = line_start + len(line_text_raw)
            cursor_in_line = line_start <= cur_cursor <= line_end
            rendered = _build_displayed_line(
                line_text,
                cursor_in_line=cursor_in_line,
                cursor=cur_cursor,
                line_start=line_start,
                cursor_style=cursor_style,
                cursor_color=cursor_color,
                selection=cur_sel,
            )
            lines.append(rendered)
        return lines

    def render_text() -> str:
        # Build the full rendered buffer (with cursor + selection overlays
        # already applied per line) and join lines with ``\n``. The single
        # Text child carries the multi-line payload; layout's wrap engine
        # (``truncate-end``) preserves embedded newlines and measures each
        # paragraph independently, so the Box grows to N rows when the
        # buffer has N lines. Per-line truncation keeps very long physical
        # lines from pushing the layout past its column / row budget.
        #
        # Why one Text instead of one Text per line: PyInk function
        # components run *exactly once* at mount (PRD Decision 1). The
        # children list passed to :func:`Box` is captured at that single
        # mount, so a per-line scheme would freeze the row count at the
        # initial value and never reflect lines the user adds by pressing
        # Enter later. A single Text whose callable re-evaluates the
        # current buffer on every signal write sidesteps the freeze
        # entirely — the layout engine sees the up-to-date ``\n``-joined
        # string on every paint.
        return "\n".join(_render_lines())

    # ``wrap="truncate-end"`` so a single line longer than the available
    # width is clipped (with ellipsis) to one visible row instead of
    # growing the parent Box and pushing siblings past the column / row
    # budget. For multi-line buffers the wrap engine preserves the
    # embedded newlines, so the Box still grows to N rows.
    return Box(
        Text(render_text, color=color, wrap="truncate-end"),
        **box_props,
    )


def TextInput(
    *,
    initial_value: str = "",
    placeholder: str | None = None,
    on_change: Callable[[str], None] | None = None,
    on_submit: Callable[[str], None] | None = None,
    on_cursor_change: Callable[[int], None] | None = None,
    multiline: bool = False,
    mask: str | None = None,
    max_length: int | None = None,
    color: str | None = None,
    cursor_color: str | None = None,
    cursor_style: CursorStyle = "block",
    is_active: bool = True,
    **box_props: Any,
) -> Element:
    """Create a single-line or multi-line text input.

    Parameters
    ----------
    initial_value:
        Initial string placed in the buffer on mount. The cursor is
        positioned at the end of this string so the user can append by
        typing. May contain newlines when ``multiline=True``.
    placeholder:
        Dimmed hint shown when the buffer is empty. ``None`` (default)
        renders an empty cell.
    on_change:
        Called with the new buffer value whenever the user edits it
        (typing, deleting, Ctrl+K/U/W, paste). Not invoked for
        cursor-only moves (arrows / Home / End / Ctrl+A/E).
    on_submit:
        Called with the current buffer value when the user presses
        Enter *in single-line mode* (``multiline=False``). In
        ``multiline=True`` Enter inserts a newline instead.
        ``None`` (default) disables submit.
    on_cursor_change:
        Called with the absolute cursor offset (a ``int`` in
        ``[0, len(value)]``) whenever the cursor moves. Fires for
        keyboard navigation (arrows / Home / End / Ctrl+A/E), text
        edits that advance the cursor (typing, Backspace, Delete,
        paste, Ctrl+K/U/W), selection-extension motions, and
        programmatic writes to the internal ``cursor`` signal. Also
        fires once on mount with the initial offset (end of
        ``initial_value``), mirroring ``on_change``'s first-write
        semantics — this lets the caller seed a cursor mirror without
        a separate initialisation code path. ``None`` (default)
        disables the callback.
    multiline:
        When ``True``, Enter inserts a ``\\n`` (multi-line editing);
        ArrowUp / ArrowDown move across lines. When ``False`` (default),
        Enter triggers :attr:`on_submit` and the input stays single-line.
    mask:
        When set, every visible character is replaced by this string
        (password mode). E.g. ``mask="*"`` renders ``"secret"`` as
        ``"******"``. The buffer itself is unaffected — only the
        rendered output is masked.
    max_length:
        Hard ceiling on the buffer length. Keystrokes (and pastes)
        that would exceed it are silently dropped.
    color:
        Colour spec for the rendered text (``"red"``, ``"#ff0000"``,
        ``"rgb(255,0,0)"``, ``"ansi256(9)"``). Forwarded to
        :func:`Text`.
    cursor_color:
        Colour spec applied to the cursor cell. Independent of
        ``color``.
    cursor_style:
        Cursor visual: ``"block"`` (default — inverse video *over* the
        cursor character so the character "lights up", Claude Code /
        classic terminal feel), ``"bar"`` (thin inverse bar between
        characters, VSCode / vim insert feel), or ``"underline"``
        (underline under the cursor character).
    is_active:
        When ``False`` the input ignores all keystrokes. Toggle at
        runtime to switch focus between multiple ``TextInput`` s.
    **box_props:
        Forwarded to the wrapping :func:`Box` (``padding``,
        ``borderStyle``, ``width``, …).

    Returns
    -------
    Element
        An element whose ``type`` is the :func:`_TextInputImpl`
        function component. The factory itself never runs hooks —
        the reconciler mounts the function, which is what makes
        ``Box(TextInput(...), Text(...))`` safe to call from outside
        a render context.

    Selection (PR2)
    ---------------
    Shift + Left / Right / Up / Down / Home / End extend the selection
    from the current cursor. Ctrl+Shift+Left / Right extend by one
    word. Any non-Shift edit (typing, Backspace, Delete, paste,
    Ctrl+K/U/W) collapses the selection; Backspace / Delete on an
    active selection deletes the selection wholesale. The selected
    range is rendered with inverse video.

    Paste (PR2)
    -----------
    When the host terminal supports *bracketed paste mode* (DECSET
    2004, auto-enabled by :class:`pyink.render.terminal.Terminal`
    while in raw mode), pasted text is delivered as a single
    :class:`Key` event with a ``paste`` payload. ``TextInput`` splices
    the payload into the buffer at the cursor as one edit, fires
    ``on_change`` once, and replaces any active selection. When the
    terminal does *not* support bracketed paste the payload arrives as
    individual character events — typing still works, just without the
    single-edit semantics.

    Usage
    -----
    ::

        Box(
            Text("Name: "),
            TextInput(
                placeholder="Enter your name...",
                on_submit=lambda v: print(f"Hello, {v}"),
                cursor_color="green",
            ),
        )

    Multi-line editor::

        TextInput(
            multiline=True,
            placeholder="Type here...",
            on_change=lambda v: ...,
        )
    """
    # ``mask`` must be a single-cell glyph so the displayed width
    # matches the buffer length; silently take the first character if
    # the caller passed something longer.
    if mask is not None and len(mask) != 1:
        mask = None if mask == "" else mask[0]

    if cursor_style not in ("bar", "block", "underline"):
        raise ValueError(
            f"cursor_style must be 'bar' | 'block' | 'underline', got {cursor_style!r}"
        )

    if max_length is not None and max_length < 0:
        raise ValueError(f"max_length must be >= 0, got {max_length}")

    # Clip the initial value to ``max_length`` so the buffer never
    # starts out over-budget. Matches ink-text-input's behaviour.
    if max_length is not None and len(initial_value) > max_length:
        initial_value = initial_value[:max_length]

    return create_element(
        _TextInputImpl,
        initial_value=initial_value,
        placeholder=placeholder,
        on_change=on_change,
        on_submit=on_submit,
        on_cursor_change=on_cursor_change,
        mask=mask,
        max_length=max_length,
        color=color,
        cursor_color=cursor_color,
        cursor_style=cursor_style,
        is_active=is_active,
        multiline=multiline,
        box_props=box_props,
    )
