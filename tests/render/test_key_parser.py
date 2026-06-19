"""Tests for :mod:`pyink.render.key_parser` (PR6).

Each test feeds bytes through :class:`KeyParser` and asserts the
resulting :class:`Key`. The parser mirrors ink's ``input-parser.ts`` +
``parse-keypress.ts``; the cases here cover the same surface ink's
``test/parse-keypress.ts`` and ``test/input-parser.ts`` exercise.
"""

from __future__ import annotations

from pyink.render.key_parser import KeyParser, parse_key
from pyink.render.keys import Key


def _one(data: bytes | str) -> Key:
    """Feed ``data`` through a fresh parser, return the first Key."""
    parser = KeyParser()
    sequences = parser.feed(data)
    assert sequences, f"no sequences parsed from {data!r}"
    return parse_key(sequences[0])


# ---------------------------------------------------------------------------
# Single-character events
# ---------------------------------------------------------------------------


def test_lowercase_letter() -> None:
    k = _one(b"a")
    assert k.input == "a"
    assert not k.shift
    assert not k.ctrl


def test_uppercase_letter_sets_shift() -> None:
    k = _one(b"A")
    assert k.input == "A"
    assert k.shift


def test_digit() -> None:
    k = _one(b"5")
    assert k.input == "5"


def test_symbol() -> None:
    k = _one(b"!")
    assert k.input == "!"


def test_unicode_character() -> None:
    # Single codepoint above BMP — emoji.
    k = _one("é".encode())
    assert k.input == "é"


# ---------------------------------------------------------------------------
# Ctrl+letter
# ---------------------------------------------------------------------------


def test_ctrl_a_through_z() -> None:
    # Bytes 1..26 map to Ctrl+A..Z, except \t (9, Tab), \n (10), \r (13
    # — Enter), and \b (8 — Backspace) which ink treats as named keys
    # first. Verify the remaining ones carry the ctrl flag + letter.
    skip = {8, 9, 10, 13}
    for i, letter in enumerate("abcdefghijklmnopqrstuvwxyz"):
        code = i + 1
        if code in skip:
            continue
        byte = bytes([code])
        k = _one(byte)
        assert k.ctrl, f"expected ctrl for byte {byte!r}"
        assert k.input == letter, f"expected input {letter!r} for byte {byte!r}"


def test_ctrl_c_byte() -> None:
    k = _one(b"\x03")
    assert k.ctrl
    assert k.input == "c"


# ---------------------------------------------------------------------------
# Whitespace / control keys
# ---------------------------------------------------------------------------


def test_carriage_return_is_return_key() -> None:
    k = _one(b"\r")
    assert k.return_key
    assert k.input == "\r"


def test_linefeed_is_return_key() -> None:
    k = _one(b"\n")
    assert k.return_key


def test_tab_is_tab_key() -> None:
    k = _one(b"\t")
    assert k.tab


def test_backspace_byte_7f() -> None:
    k = _one(b"\x7f")
    assert k.backspace


def test_backspace_byte_08() -> None:
    k = _one(b"\x08")
    assert k.backspace


# ---------------------------------------------------------------------------
# Escape
# ---------------------------------------------------------------------------


def test_lone_escape_after_flush() -> None:
    parser = KeyParser()
    sequences = parser.feed(b"\x1b")
    assert sequences == []
    assert parser.has_pending_escape
    flushed = parser.flush_pending_escape()
    assert flushed == "\x1b"
    k = parse_key(flushed or "")
    assert k.escape


# ---------------------------------------------------------------------------
# Alt+X
# ---------------------------------------------------------------------------


def test_alt_x() -> None:
    k = _one(b"\x1bx")
    assert k.alt
    assert k.input == "x"


def test_alt_uppercase_letter() -> None:
    k = _one(b"\x1bX")
    assert k.alt
    assert k.shift


def test_alt_enter() -> None:
    k = _one(b"\x1b\r")
    assert k.alt
    assert k.return_key


# ---------------------------------------------------------------------------
# Arrows
# ---------------------------------------------------------------------------


def test_arrow_keys() -> None:
    cases = [
        (b"\x1b[A", "up_arrow"),
        (b"\x1b[B", "down_arrow"),
        (b"\x1b[C", "right_arrow"),
        (b"\x1b[D", "left_arrow"),
    ]
    for raw, attr in cases:
        k = _one(raw)
        assert getattr(k, attr), f"expected {attr} for {raw!r}, got {k}"


def test_arrow_keys_ss3_form() -> None:
    cases = [
        (b"\x1bOA", "up_arrow"),
        (b"\x1bOB", "down_arrow"),
        (b"\x1bOC", "right_arrow"),
        (b"\x1bOD", "left_arrow"),
    ]
    for raw, attr in cases:
        k = _one(raw)
        assert getattr(k, attr), f"expected {attr} for {raw!r}, got {k}"


# ---------------------------------------------------------------------------
# Home / End / PageUp / PageDown / Delete
# ---------------------------------------------------------------------------


def test_home_end_csi() -> None:
    assert _one(b"\x1b[H").home
    assert _one(b"\x1b[F").end


def test_home_end_tilde_form() -> None:
    assert _one(b"\x1b[1~").home
    assert _one(b"\x1b[4~").end
    assert _one(b"\x1b[7~").home
    assert _one(b"\x1b[8~").end


def test_page_up_down() -> None:
    assert _one(b"\x1b[5~").page_up
    assert _one(b"\x1b[6~").page_down


def test_delete_key() -> None:
    assert _one(b"\x1b[3~").delete


# ---------------------------------------------------------------------------
# Shift+Tab
# ---------------------------------------------------------------------------


def test_shift_tab() -> None:
    k = _one(b"\x1b[Z")
    assert k.tab
    assert k.shift


# ---------------------------------------------------------------------------
# Modified arrows (Ctrl+Arrow, Shift+Arrow)
# ---------------------------------------------------------------------------


def test_ctrl_up_arrow() -> None:
    k = _one(b"\x1b[1;5A")
    assert k.up_arrow
    assert k.ctrl


def test_ctrl_right_arrow() -> None:
    k = _one(b"\x1b[1;5C")
    assert k.right_arrow
    assert k.ctrl


def test_shift_up_arrow() -> None:
    k = _one(b"\x1b[1;2A")
    assert k.up_arrow
    assert k.shift


def test_alt_up_arrow() -> None:
    k = _one(b"\x1b[1;3A")
    assert k.up_arrow
    assert k.alt


# ---------------------------------------------------------------------------
# Streaming: incomplete sequence then completion
# ---------------------------------------------------------------------------


def test_partial_csi_buffers_then_completes() -> None:
    parser = KeyParser()
    # Feed ESC [ alone — no sequence yet.
    assert parser.feed(b"\x1b[") == []
    # Then 'A' completes the Up sequence.
    seqs = parser.feed(b"A")
    assert len(seqs) == 1
    assert parse_key(seqs[0]).up_arrow


def test_flush_pending_escape_preserves_remaining_bytes() -> None:
    """flush_pending_escape returns just ESC; trailing bytes stay buffered."""
    parser = KeyParser()
    # ESC [ is incomplete.
    assert parser.feed(b"\x1b[") == []
    assert parser.has_pending_escape
    flushed = parser.flush_pending_escape()
    assert flushed == "\x1b"
    # The '[' is still buffered — a later feed treats it as a plain char
    # because the ESC has been pulled out. The next feed sees just "[".
    seqs = parser.feed(b"A")
    assert seqs == ["[", "A"]
    assert [parse_key(s).input for s in seqs] == ["[", "A"]


def test_flush_pending_escape_returns_none_when_no_pending() -> None:
    parser = KeyParser()
    parser.feed(b"a")
    assert parser.flush_pending_escape() is None


def test_multi_byte_chunk_yields_multiple_keys() -> None:
    parser = KeyParser()
    seqs = parser.feed(b"abc")
    assert len(seqs) == 3
    assert [parse_key(s).input for s in seqs] == ["a", "b", "c"]


def test_escape_then_plain_text_split_correctly() -> None:
    parser = KeyParser()
    seqs = parser.feed(b"a\x1b[Ab")
    assert len(seqs) == 3
    assert parse_key(seqs[0]).input == "a"
    assert parse_key(seqs[1]).up_arrow
    assert parse_key(seqs[2]).input == "b"


def test_reset_drops_pending() -> None:
    parser = KeyParser()
    parser.feed(b"\x1b[")
    assert parser.has_pending_escape
    parser.reset()
    assert not parser.has_pending_escape


# ---------------------------------------------------------------------------
# Function keys
# ---------------------------------------------------------------------------


def test_f1_through_f4_ss3_form() -> None:
    for raw, _name in [
        (b"\x1bOP", "f1"),
        (b"\x1bOQ", "f2"),
        (b"\x1bOR", "f3"),
        (b"\x1bOS", "f4"),
    ]:
        # Function keys are not surfaced as Key flags in the MVP — we
        # only assert that parsing doesn't blow up and yields one event.
        parser = KeyParser()
        seqs = parser.feed(raw)
        assert len(seqs) == 1


def test_f5_through_f12_tilde_form() -> None:
    for raw in [
        b"\x1b[15~",  # F5
        b"\x1b[17~",  # F6
        b"\x1b[18~",  # F7
        b"\x1b[19~",  # F8
        b"\x1b[20~",  # F9
        b"\x1b[21~",  # F10
        b"\x1b[23~",  # F11
        b"\x1b[24~",  # F12
    ]:
        parser = KeyParser()
        seqs = parser.feed(raw)
        assert len(seqs) == 1
