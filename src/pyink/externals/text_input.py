"""``TextInput`` — single-line text input (Phase 4 PR1).

Mirrors ``ink-text-input`` at the level Phase 4 PR1 needs: single-line
editing with a visible cursor, basic Emacs-style word/line kill
shortcuts, password masking and ``max_length`` truncation. Multi-line
editing, selection and paste-bracketing land in PR2.

Design (per PRD Decision 1):

* ``TextInput`` is a factory returning an :class:`Element` whose
  ``type`` is the :func:`_TextInputImpl` function component. The
  factory itself never runs hooks; only the wrapped function does,
  when the reconciler mounts it.
* Two writable :class:`Signal` s live inside the component: ``value``
  (current string) and ``cursor`` (character offset, ``0 <= cursor <=
  len(value)``). Every keypress updates one or both; the layout-time
  callable child subscribes to both so the render loop re-paints on
  each change.
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

``cursor_color`` wraps the cursor cell in a foreground SGR for ``bar``
and ``underline``; for ``block`` the colour is applied *under* the
inverse video so terminals that honour ``inverse`` as "swap fg/bg"
still tint the cell. The layout measure pass strips CSI sequences, so
the cursor escapes never inflate the column budget.

Out of scope (PR1):

* Multi-line editing (Enter inserts ``\\n`` only in PR2).
* Selection (Shift+arrows).
* Paste bracketing.
* Vim mode (Phase 4.5).

Esc / Ctrl+C are deliberately *not* swallowed so the surrounding
``exit_on_ctrl_c`` pipeline still owns them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element, create_element
from pyink.core.signal import Ref, Signal, ref, signal
from pyink.hooks.input import use_input
from pyink.render.ansi import apply_style, parse_color
from pyink.render.keys import Key

__all__ = ["TextInput"]

#: Allowed values for the ``cursor_style`` prop.
CursorStyle = Literal["bar", "block", "underline"]

#: SGR "inverse video" sequence — used by ``bar`` and ``block`` cursors.
_SGR_INVERSE = "\x1b[7m"
#: SGR "underline" sequence — used by the ``underline`` cursor.
_SGR_UNDERLINE = "\x1b[4m"
#: SGR reset — closes every cursor SGR run.
_SGR_RESET = "\x1b[0m"


def _is_word_char(ch: str) -> bool:
    """``True`` for characters that count as part of a word (alphanumeric / underscore)."""
    return ch.isalnum() or ch == "_"


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


def _build_displayed(
    value: str,
    cursor: int,
    *,
    mask: str | None,
    placeholder: str | None,
    cursor_style: CursorStyle,
    cursor_color: str | None,
) -> str:
    """Build the rendered text-with-cursor string.

    The visible text is ``mask * len(value)`` when ``mask`` is set
    (password mode), else ``value`` verbatim. The cursor is then
    spliced in according to ``cursor_style``:

    * ``bar`` — a one-cell inverse-video bar is *inserted* at the cursor
      position, shifting the following characters one column to the
      right. This matches the VSCode / vim insert-mode cursor.
    * ``block`` / ``underline`` — the cursor is rendered *over* the
      character at the cursor position (replacing it with inverse /
      underline styling). When the cursor sits past the end of the
      buffer, a one-cell space is used instead.

    When ``value`` is empty and ``placeholder`` is provided the
    placeholder is shown dimmed and the cursor is drawn at column 0.
    """
    displayed = mask * len(value) if mask else value

    if not displayed and placeholder:
        # Render the placeholder dimmed, then drop the cursor at the
        # start (column 0). The cursor must remain visible so the user
        # knows the input is focused even before typing.
        cursor_cell = _cursor_cell(
            cursor_style, char="", cursor_color=cursor_color
        )
        return apply_style(placeholder, dimColor=True) + cursor_cell

    if cursor_style == "bar":
        # Bar cursor inserts an extra cell — no character is consumed.
        # ``before`` is everything up to the cursor; ``rest`` is the
        # remainder, unchanged.
        before = displayed[:cursor]
        rest = displayed[cursor:]
        cursor_cell = _cursor_cell(
            cursor_style, char="", cursor_color=cursor_color
        )
        return before + cursor_cell + rest

    # Block / underline sit *on* a character. At end-of-input there is
    # no character to sit on — emit the cursor cell with an empty
    # underlying char (renders as a styled space).
    before = displayed[:cursor]
    rest = displayed[cursor:]
    if rest:
        target = rest[0]
        after = rest[1:]
    else:
        target = ""
        after = ""
    cursor_cell = _cursor_cell(
        cursor_style, char=target, cursor_color=cursor_color
    )
    return before + cursor_cell + after


def _TextInputImpl(**props: Any) -> Element:
    """Function component body — runs inside the reconciler render context.

    Owns the ``value`` / ``cursor`` signals, registers the keyboard
    handler via :func:`use_input`, and returns a :func:`Box` containing
    a callable :func:`Text` child that re-evaluates on every signal
    write.
    """
    initial_value: str = props["initial_value"]
    placeholder: str | None = props["placeholder"]
    on_change: Callable[[str], None] | None = props["on_change"]
    on_submit: Callable[[str], None] | None = props["on_submit"]
    mask: str | None = props["mask"]
    max_length: int | None = props["max_length"]
    color: str | None = props["color"]
    cursor_color: str | None = props["cursor_color"]
    cursor_style: CursorStyle = props["cursor_style"]
    is_active: bool = props["is_active"]
    box_props: dict[str, Any] = props["box_props"]

    # Initial cursor sits at end of the initial value — matches
    # ink-text-input and the common "open input, type to append" UX.
    value: Signal[str] = signal(initial_value)
    cursor: Signal[int] = signal(len(initial_value))

    # Keep the latest callback without resubscribing the input handler.
    # ``use_input``'s closure captures the ref objects, so updating
    # ``.value`` here is enough to redirect future keypresses.
    on_change_ref: Ref[Callable[[str], None] | None] = ref(on_change)
    on_submit_ref: Ref[Callable[[str], None] | None] = ref(on_submit)
    on_change_ref.value = on_change
    on_submit_ref.value = on_submit

    def _notify_change(old: str, new: str) -> None:
        if new != old:
            cb = on_change_ref.value
            if cb is not None:
                cb(new)

    def handle_key(key: Key) -> None:
        if not is_active:
            return

        old_value = value.value
        old_cursor = cursor.value

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
            and key.input.isprintable()
        ):
            new_value = old_value[:old_cursor] + key.input + old_value[old_cursor:]
            if max_length is None or len(new_value) <= max_length:
                value.value = new_value
                cursor.value = old_cursor + 1
            _notify_change(old_value, value.value)
            return

        if key.backspace and not key.ctrl and not key.alt:
            if old_cursor > 0:
                value.value = old_value[: old_cursor - 1] + old_value[old_cursor:]
                cursor.value = old_cursor - 1
                _notify_change(old_value, value.value)
            return

        if key.delete and not key.ctrl and not key.alt:
            if old_cursor < len(old_value):
                value.value = old_value[:old_cursor] + old_value[old_cursor + 1 :]
                _notify_change(old_value, value.value)
            return

        if key.left_arrow and not key.ctrl and not key.alt:
            cursor.value = max(0, old_cursor - 1)
            return

        if key.right_arrow and not key.ctrl and not key.alt:
            cursor.value = min(len(old_value), old_cursor + 1)
            return

        if key.home and not key.ctrl and not key.alt:
            cursor.value = 0
            return

        if key.end and not key.ctrl and not key.alt:
            cursor.value = len(old_value)
            return

        # Ctrl+letter shortcuts — Emacs-style line / word editing.
        if key.ctrl and not key.alt and key.input:
            letter = key.input.lower()
            if letter == "a":
                cursor.value = 0
                return
            if letter == "e":
                cursor.value = len(old_value)
                return
            if letter == "k":
                # Kill to end of line — drop everything past the cursor.
                if old_cursor < len(old_value):
                    value.value = old_value[:old_cursor]
                    _notify_change(old_value, value.value)
                return
            if letter == "u":
                # Kill to start of line — drop everything before the cursor.
                if old_cursor > 0:
                    value.value = old_value[old_cursor:]
                    cursor.value = 0
                    _notify_change(old_value, value.value)
                return
            if letter == "w":
                # Delete the previous word (whitespace + word chars
                # immediately to the left of the cursor). Mirrors
                # readline's ``backward-kill-word``.
                if old_cursor > 0:
                    new_cursor = old_cursor
                    # Skip whitespace to the left.
                    while new_cursor > 0 and old_value[new_cursor - 1].isspace():
                        new_cursor -= 1
                    # Skip the word to the left.
                    while new_cursor > 0 and _is_word_char(
                        old_value[new_cursor - 1]
                    ):
                        new_cursor -= 1
                    if new_cursor != old_cursor:
                        value.value = (
                            old_value[:new_cursor] + old_value[old_cursor:]
                        )
                        cursor.value = new_cursor
                        _notify_change(old_value, value.value)
                return

        # Enter triggers on_submit when in single-line mode (PR1 always
        # single-line — multi-line arrives in PR2).
        if key.return_key and not key.alt:
            cb = on_submit_ref.value
            if cb is not None:
                cb(value.value)
            return

        # All other keys (Esc, Tab, Alt+X, function keys, …) are
        # intentionally left for the surrounding pipeline to handle.

    use_input(handle_key, is_active=is_active)

    def render_text() -> str:
        return _build_displayed(
            value.value,
            cursor.value,
            mask=mask,
            placeholder=placeholder,
            cursor_style=cursor_style,
            cursor_color=cursor_color,
        )

    return Box(Text(render_text, color=color), **box_props)


def TextInput(
    *,
    initial_value: str = "",
    placeholder: str | None = None,
    on_change: Callable[[str], None] | None = None,
    on_submit: Callable[[str], None] | None = None,
    mask: str | None = None,
    max_length: int | None = None,
    color: str | None = None,
    cursor_color: str | None = None,
    cursor_style: CursorStyle = "bar",
    is_active: bool = True,
    **box_props: Any,
) -> Element:
    """Create a single-line text input.

    Parameters
    ----------
    initial_value:
        Initial string placed in the buffer on mount. The cursor is
        positioned at the end of this string so the user can append by
        typing.
    placeholder:
        Dimmed hint shown when the buffer is empty. ``None`` (default)
        renders an empty cell.
    on_change:
        Called with the new buffer value whenever the user edits it
        (typing, deleting, Ctrl+K/U/W). Not invoked for cursor-only
        moves (arrows / Home / End / Ctrl+A/E).
    on_submit:
        Called with the current buffer value when the user presses
        Enter. ``None`` (default) disables submit.
    mask:
        When set, every visible character is replaced by this string
        (password mode). E.g. ``mask="*"`` renders ``"secret"`` as
        ``"******"``. The buffer itself is unaffected — only the
        rendered output is masked.
    max_length:
        Hard ceiling on the buffer length. Keystrokes that would
        exceed it are silently dropped.
    color:
        Colour spec for the rendered text (``"red"``, ``"#ff0000"``,
        ``"rgb(255,0,0)"``, ``"ansi256(9)"``). Forwarded to
        :func:`Text`.
    cursor_color:
        Colour spec applied to the cursor cell. Independent of
        ``color``.
    cursor_style:
        Cursor visual: ``"bar"`` (default — thin inverse bar between
        characters, VSCode / vim insert feel), ``"block"`` (inverse
        video over the cursor character), or ``"underline"`` (underline
        under the cursor character).
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

    Usage
    -----
    ::

        Box(
            Text("Name: "),
            TextInput(
                placeholder="Enter your name...",
                on_submit=lambda v: print(f"Hello, {v}"),
                cursor_style="bar",
                cursor_color="green",
            ),
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
        mask=mask,
        max_length=max_length,
        color=color,
        cursor_color=cursor_color,
        cursor_style=cursor_style,
        is_active=is_active,
        box_props=box_props,
    )
