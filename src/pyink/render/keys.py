"""Key data structure for keyboard input (PR6).

A :class:`Key` is the parsed, declarative form of a single keypress — the
Python analogue of ink's ``Key`` TS type. Raw bytes from stdin are turned
into a :class:`Key` by :mod:`pyink.render.key_parser` (which mirrors ink's
``parse-keypress.ts``).

Design notes:

* ``return_key`` (not ``return``) — ``return`` is a Python keyword.
* All flags default to ``False`` so a single-char press is just
  ``Key(input='a')``.
* ``input`` carries the printable character (or the letter name for
  Ctrl+letter). Arrow / function / modifier-only keys leave it empty
  string — handlers should consult the boolean flags.
* The dataclass is frozen + slotted so keys are hashable and cheap.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Key"]


@dataclass(frozen=True, slots=True)
class Key:
    """A single parsed keypress event."""

    #: The printable character that was pressed (e.g. ``'a'``, ``'\\r'``).
    #: For Ctrl+letter this is the letter name (``'a'`` for Ctrl+A). For
    #: special keys (arrows, function keys) it is the empty string —
    #: consult the boolean flags instead.
    input: str

    # ------------------------------------------------------------------
    # Modifier keys
    # ------------------------------------------------------------------

    #: Ctrl was held.
    ctrl: bool = False
    #: Shift was held (set automatically for uppercase letters).
    shift: bool = False
    #: Alt (Option on macOS) was held. Sent by terminals as ``ESC + char``.
    alt: bool = False
    #: Meta (Cmd on macOS). Only populated under the kitty keyboard
    #: protocol, which is out of scope for the MVP — always ``False``.
    meta: bool = False

    # ------------------------------------------------------------------
    # Special keys
    # ------------------------------------------------------------------

    up_arrow: bool = False
    down_arrow: bool = False
    left_arrow: bool = False
    right_arrow: bool = False
    #: Enter / Return. Named ``return_key`` because ``return`` is a
    #: Python keyword.
    return_key: bool = False
    escape: bool = False
    tab: bool = False
    backspace: bool = False
    delete: bool = False
    page_up: bool = False
    page_down: bool = False
    home: bool = False
    end: bool = False
