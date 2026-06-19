"""Parse raw stdin bytes into :class:`pyink.render.keys.Key` events (PR6).

Two-stage pipeline mirroring ink's ``input-parser.ts`` +
``parse-keypress.ts``:

1. :class:`KeyParser` accepts bytes chunks from stdin. It splits them
   into *sequences* — each sequence is either a single UTF-8 char or a
   complete ANSI escape (``\\x1b[A``, ``\\x1bOP``, …). Incomplete
   escapes are buffered until more bytes arrive.
2. :func:`parse_key` maps a single sequence string to a :class:`Key`.

Out of scope (PRD "MVP Out of Scope"):

* Kitty keyboard protocol — we parse the legacy escape sequences only.
* ``modifyOtherKeys`` / precise function-key disambiguation.
* Paste bracketing (``\\x1b[200~`` / ``\\x1b[201~``) — paste arrives as
  individual characters.

Reference: ink ``src/input-parser.ts`` (CSI parsing algorithm) and
``src/parse-keypress.ts`` (sequence → key name table).
"""

from __future__ import annotations

from pyink.render.keys import Key

__all__ = ["KeyParser", "parse_key"]


_ESC = "\x1b"


# ---------------------------------------------------------------------------
# Sequence splitting (mirrors ink's input-parser.ts)
# ---------------------------------------------------------------------------


def _is_csi_param(byte: int) -> bool:
    return 0x30 <= byte <= 0x3F


def _is_csi_intermediate(byte: int) -> bool:
    return 0x20 <= byte <= 0x2F


def _is_csi_final(byte: int) -> bool:
    return 0x40 <= byte <= 0x7E


class KeyParser:
    """Stateful parser turning byte chunks into escape sequence strings.

    Feed bytes via :meth:`feed`; you receive a list of *sequence strings*,
    each of which can be passed to :func:`parse_key` to get a
    :class:`Key`. Incomplete escape sequences are buffered internally.

    The parser is **not** thread-safe — wrap calls in a lock if multiple
    threads feed it (the :class:`pyink.render.terminal.Terminal` input
    loop does this).
    """

    __slots__ = ("_pending",)

    def __init__(self) -> None:
        # Buffer holding an in-progress escape sequence (begins with ESC).
        self._pending: str = ""

    def feed(self, data: bytes | str) -> list[str]:
        """Append ``data`` to the buffer and return complete sequences."""
        if isinstance(data, (bytes, bytearray)):
            text = bytes(data).decode("utf-8", errors="replace")
        else:
            text = data
        buf = self._pending + text
        sequences: list[str] = []
        self._pending = ""
        index = 0
        n = len(buf)
        while index < n:
            ch = buf[index]
            if ch != _ESC:
                # Non-escape run: emit chars up to the next ESC.
                next_esc = buf.find(_ESC, index)
                if next_esc == -1:
                    sequences.extend(_split_text(buf[index:]))
                    return sequences
                if next_esc > index:
                    sequences.extend(_split_text(buf[index:next_esc]))
                index = next_esc
                continue
            # We are at an ESC. Try to parse a full escape sequence.
            consumed, seq_or_pending = _parse_escape(buf, index)
            if consumed == 0:
                # Pending — incomplete sequence at end of buffer.
                self._pending = buf[index:]
                return sequences
            assert seq_or_pending is not None
            sequences.append(seq_or_pending)
            index += consumed
        return sequences

    @property
    def has_pending_escape(self) -> bool:
        """``True`` if the buffer holds a partial escape sequence."""
        return self._pending.startswith(_ESC)

    def flush_pending_escape(self) -> str | None:
        """Return and clear any buffered partial escape as a lone ESC.

        Call this after a short timeout when :attr:`has_pending_escape` is
        true — a lone ``ESC`` press arrives as a single ``\\x1b`` byte,
        indistinguishable from the start of a longer sequence until more
        bytes arrive (or don't).
        """
        if not self._pending.startswith(_ESC):
            return None
        # Return just the ESC byte. The remainder (if any) stays buffered
        # — a later :meth:`feed` call picks it up.
        self._pending = self._pending[1:]
        return _ESC

    def reset(self) -> None:
        """Drop any buffered partial escape sequence."""
        self._pending = ""


def _split_text(text: str) -> list[str]:
    """Split a non-escape run into per-character events.

    Backspace bytes (``\\x7f`` and ``\\x08``) are emitted individually so
    a held-backspace burst (terminal sends ``\\x7f\\x7f\\x7f``) is parsed
    as three separate Key events rather than one multi-char event.
    """
    out: list[str] = []
    for ch in text:
        out.append(ch)
    return out


def _parse_escape(buf: str, start: int) -> tuple[int, str | None]:
    """Try to parse a full escape sequence starting at ``buf[start]``.

    Returns ``(consumed_chars, sequence_string)``. ``consumed == 0`` and
    ``sequence is None`` means the buffer ends mid-sequence (pending).
    """
    n = len(buf)
    # ESC at the very end — pending.
    if start >= n - 1:
        return 0, None
    next_ch = buf[start + 1]
    if next_ch == "[":
        return _parse_csi(buf, start)
    if next_ch == "O":
        # SS3 sequence: ESC O <letter> — always 3 bytes.
        if start + 2 >= n:
            return 0, None
        return 3, buf[start : start + 3]
    # ESC + single char (Alt+X, or ESC ESC, …).
    return 2, buf[start : start + 2]


def _parse_csi(buf: str, start: int) -> tuple[int, str | None]:
    """Parse a ``ESC [`` CSI sequence starting at ``buf[start]``.

    A CSI sequence is ``ESC [`` followed by zero-or-more parameter /
    intermediate bytes, then one final byte in 0x40–0x7E.
    """
    n = len(buf)
    index = start + 2  # skip ESC [
    while index < n:
        byte = ord(buf[index])
        if _is_csi_final(byte):
            return index - start + 1, buf[start : index + 1]
        if _is_csi_param(byte) or _is_csi_intermediate(byte):
            index += 1
            continue
        # Allow legacy ``ESC [[A`` style (Cygwin) at the payload start.
        if byte == 0x5B and index == start + 2:  # '[' right after ESC[
            index += 1
            continue
        # Unexpected byte — treat as a 2-char ESC [ and let the caller
        # re-parse from start+2.
        return 2, buf[start : start + 2]
    return 0, None


# ---------------------------------------------------------------------------
# Sequence → Key (mirrors ink's parse-keypress.ts)
# ---------------------------------------------------------------------------


# Legacy CSI / SS3 suffix → ``(base_key_name, preset_modifiers)``. Keys
# are the suffix *after* the leading ``ESC``. E.g. ``ESC [ A`` → key
# ``"[A"`` → ``("up", 0)``. ``preset_modifiers`` is a bitfield (1=shift,
# 2=alt, 4=ctrl) baked into the suffix itself — e.g. ``[Z`` (Shift+Tab)
# carries shift=1, ``[a`` (Shift+Up in rxvt) carries shift=1.
_SPECIAL_SUFFIXES: dict[str, tuple[str, int]] = {
    # xterm ESC [ letter
    "[A": ("up", 0),
    "[B": ("down", 0),
    "[C": ("right", 0),
    "[D": ("left", 0),
    "[E": ("clear", 0),
    "[F": ("end", 0),
    "[H": ("home", 0),
    # xterm ESC O letter
    "OA": ("up", 0),
    "OB": ("down", 0),
    "OC": ("right", 0),
    "OD": ("left", 0),
    "OE": ("clear", 0),
    "OF": ("end", 0),
    "OH": ("home", 0),
    # xterm/rxvt ESC [ number ~
    "[1~": ("home", 0),
    "[2~": ("insert", 0),
    "[3~": ("delete", 0),
    "[4~": ("end", 0),
    "[5~": ("pageup", 0),
    "[6~": ("pagedown", 0),
    "[7~": ("home", 0),
    "[8~": ("end", 0),
    # Function keys
    "OP": ("f1", 0),
    "OQ": ("f2", 0),
    "OR": ("f3", 0),
    "OS": ("f4", 0),
    "[11~": ("f1", 0),
    "[12~": ("f2", 0),
    "[13~": ("f3", 0),
    "[14~": ("f4", 0),
    "[15~": ("f5", 0),
    "[17~": ("f6", 0),
    "[18~": ("f7", 0),
    "[19~": ("f8", 0),
    "[20~": ("f9", 0),
    "[21~": ("f10", 0),
    "[23~": ("f11", 0),
    "[24~": ("f12", 0),
    # Shift+Tab
    "[Z": ("tab", 1),
    # rxvt shifted arrows / function keys with modifier shorthand
    "[a": ("up", 1),
    "[b": ("down", 1),
    "[c": ("right", 1),
    "[d": ("left", 1),
    "[e": ("clear", 1),
    "[2$": ("insert", 1),
    "[3$": ("delete", 1),
    "[5$": ("pageup", 1),
    "[6$": ("pagedown", 1),
    "[7$": ("home", 1),
    "[8$": ("end", 1),
    "Oa": ("up", 1),
    "Ob": ("down", 1),
    "Oc": ("right", 1),
    "Od": ("left", 1),
    "Oe": ("clear", 1),
    "[2^": ("insert", 4),
    "[3^": ("delete", 4),
    "[5^": ("pageup", 4),
    "[6^": ("pagedown", 4),
    "[7^": ("home", 4),
    "[8^": ("end", 4),
}


def parse_key(sequence: str) -> Key:
    """Map a single escape sequence string to a :class:`Key`.

    The input is one of the strings returned by :meth:`KeyParser.feed` —
    either a single character or a full ANSI escape (``\\x1b[A``, etc.).
    """
    if not sequence:
        return Key(input="")

    # Lone escape.
    if sequence == _ESC:
        return Key(input=_ESC, escape=True)

    # Single-character events.
    if len(sequence) == 1:
        ch = sequence
        code = ord(ch)
        # Enter (CR or LF).
        if ch in ("\r", "\n"):
            return Key(input=ch, return_key=True)
        # Tab.
        if ch == "\t":
            return Key(input=ch, tab=True)
        # Backspace (DEL or BS).
        if code in (0x7F, 0x08):
            return Key(input=ch, backspace=True)
        # Ctrl+letter: \x01..\x1a → a..z
        if 0x01 <= code <= 0x1A:
            letter = chr(code + ord("a") - 1)
            return Key(input=letter, ctrl=True)
        # Ctrl+other: [\x1b..\x1f] map to specific control sequences we
        # don't surface as named keys; emit raw.
        if code < 0x20:
            return Key(input=ch)
        # Printable ASCII / Unicode.
        if "A" <= ch <= "Z":
            return Key(input=ch, shift=True)
        return Key(input=ch)

    # Multi-character escape.
    # Alt+X: ESC + single char.
    if len(sequence) == 2 and sequence[0] == _ESC:
        inner = sequence[1]
        code = ord(inner)
        # Alt+Enter, Alt+Backspace, etc.
        if inner in ("\r", "\n"):
            return Key(input=inner, return_key=True, alt=True)
        if inner == "\t":
            return Key(input=inner, tab=True, alt=True)
        if code in (0x7F, 0x08):
            return Key(input=inner, backspace=True, alt=True)
        if 0x01 <= code <= 0x1A:
            letter = chr(code + ord("a") - 1)
            return Key(input=letter, ctrl=True, alt=True)
        if "A" <= inner <= "Z":
            return Key(input=inner, shift=True, alt=True)
        if "a" <= inner <= "z" or "0" <= inner <= "9":
            return Key(input=inner, alt=True)
        # Alt + symbol.
        return Key(input=inner, alt=True)

    # ESC-prefixed longer sequences (CSI / SS3).
    if sequence[0] == _ESC:
        return _parse_escaped_sequence(sequence)

    return Key(input=sequence)


def _parse_escaped_sequence(sequence: str) -> Key:
    """Parse a multi-byte ``ESC ...`` sequence to a :class:`Key`."""
    # Strip the leading ESC(s). If there are two leading ESCs the meta
    # flag is set (matches ink's parse-keypress.ts handling of fnKeyRe).
    meta_from_prefix = False
    body = sequence
    while body.startswith(_ESC):
        if body is sequence and body[1:].startswith(_ESC):
            # ``ESC ESC ...`` — meta flag set, strip just one ESC here.
            meta_from_prefix = True
            body = body[1:]
            continue
        body = body[1:]
        break

    # Try to strip a ``;N`` xterm modifier suffix from CSI sequences.
    extra_mods, base_suffix = _extract_modifier(body)
    entry = _SPECIAL_SUFFIXES.get(base_suffix)
    if entry is None:
        # Unknown escape — emit raw with no flags.
        return Key(input="")

    name, preset_mods = entry
    # xterm modifier encoding: 1 + (shift | alt | ctrl | meta). Subtract
    # 1 to get the bitfield (1=shift, 2=alt, 4=ctrl, …).
    mod_bits = preset_mods
    if extra_mods is not None:
        mod_bits |= max(0, extra_mods - 1)
    shift = bool(mod_bits & 1)
    alt = bool(mod_bits & 2) or meta_from_prefix
    ctrl = bool(mod_bits & 4)
    meta = alt  # MVP collapses meta into alt for compatibility with ink.

    return _key_from_name(
        name,
        ctrl=ctrl,
        shift=shift,
        meta=meta,
        alt=alt,
    )


def _extract_modifier(body: str) -> tuple[int | None, str]:
    """Pull a ``;N`` modifier out of a CSI suffix, returning ``(mods, clean)``.

    ``clean`` is the body with any ``;N`` stripped so it matches the keys
    of :data:`_SPECIAL_SUFFIXES`. ``mods`` is ``None`` when no modifier
    was present.
    """
    if not body:
        return None, body
    # body looks like ``[1;5A`` (param + modifier + final byte) or ``[A``.
    # SS3 ``OA`` has no number form in practice; just return as-is.
    if body[0] == "O":
        return None, body
    if body[0] != "[":
        return None, body
    # Find a ';' inside the parameter portion.
    semi = body.find(";")
    if semi == -1:
        return None, body
    # Final byte is the last char (always 0x40-0x7E for valid CSI).
    final_byte = body[-1]
    # Reconstruct the unmodified suffix: ``[`` + final_byte, or
    # ``[<digits>~`` for number-terminated sequences.
    # Determine whether the original was a ``~``-terminated numeric key.
    if final_byte == "~":
        # ``[<n>;<mods>~`` → canonical ``[<n>~``.
        # Parse the first number for canonicalisation.
        first_num_end = semi
        first_num = body[1:first_num_end]
        return _parse_int_safe(body[semi + 1 : -1]), f"[{first_num}~"
    # Letter-terminated: ``[1;<mods>A`` → ``[A``.
    return _parse_int_safe(body[semi + 1 : -1]), f"[{final_byte}"


def _parse_int_safe(text: str) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _key_from_name(
    name: str,
    *,
    ctrl: bool,
    shift: bool,
    meta: bool,
    alt: bool,
) -> Key:
    """Build a :class:`Key` from a special-key name + modifier flags."""
    kwargs: dict[str, object] = {
        "input": "",
        "ctrl": ctrl,
        "shift": shift,
        "meta": meta,
        "alt": alt,
    }
    if name == "up":
        kwargs["up_arrow"] = True
    elif name == "down":
        kwargs["down_arrow"] = True
    elif name == "left":
        kwargs["left_arrow"] = True
    elif name == "right":
        kwargs["right_arrow"] = True
    elif name == "home":
        kwargs["home"] = True
    elif name == "end":
        kwargs["end"] = True
    elif name == "pageup":
        kwargs["page_up"] = True
    elif name == "pagedown":
        kwargs["page_down"] = True
    elif name == "delete":
        kwargs["delete"] = True
    elif name == "insert":
        # Not a first-class Key flag; leave as delete=False, just modifiers.
        pass
    elif name == "clear":
        pass
    elif name == "tab":
        kwargs["tab"] = True
    # Function keys f1..f12 are not surfaced as Key flags in the MVP —
    # handlers can match on the raw escape sequence if needed. This
    # matches ink's Key type, which has no `f1` flag either.
    return Key(**kwargs)  # type: ignore[arg-type]
