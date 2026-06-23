"""Tests for raw-mode + on_key on :class:`ink.render.terminal.Terminal`.

The Terminal raw-mode entry/exit logic is Unix-only and would mess with
the test runner's own stdin. These tests use monkey-patching to fake a
TTY (so ``is_raw_mode_supported`` returns True) and a fake
``_read_stdin_chunk`` to drive synthetic bytes through the input loop.
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable
from unittest.mock import patch

import pytest

from ink.render import terminal as _term_mod
from ink.render.keys import Key
from ink.render.terminal import Terminal


class _FakeTTY(io.StringIO):
    """A StringIO that pretends to be a TTY."""

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        # Return an arbitrary fd — os.read is patched per-test.
        return 99


@pytest.fixture
def tty_terminal() -> Terminal:
    """A Terminal wired to a fake TTY stdin/stdout."""
    stdout = _FakeTTY()
    term = Terminal(stdout=stdout)
    term.stdin = _FakeTTY()
    return term


def test_is_raw_mode_supported_false_for_non_tty() -> None:
    term = Terminal(stdout=io.StringIO())
    assert term.is_raw_mode_supported is False


def test_is_raw_mode_supported_true_for_tty(tty_terminal: Terminal) -> None:
    assert tty_terminal.is_raw_mode_supported is True


class _RawModePatcher:
    """Patch both Unix and Windows raw-mode entry/exit methods."""

    def __init__(
        self,
        enter_fn: Callable[[Terminal], None],
        exit_fn: Callable[[Terminal], None],
    ) -> None:
        self._patches = [
            patch.object(Terminal, "_enter_raw_mode_unix", enter_fn),
            patch.object(Terminal, "_exit_raw_mode_unix", exit_fn),
            patch.object(Terminal, "_enter_raw_mode_windows", enter_fn),
            patch.object(Terminal, "_exit_raw_mode_windows", exit_fn),
        ]

    def __enter__(self) -> _RawModePatcher:
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc: object) -> None:
        for p in self._patches:
            p.stop()


def _patch_raw(
    enter_fn: Callable[[Terminal], None],
    exit_fn: Callable[[Terminal], None],
) -> _RawModePatcher:
    return _RawModePatcher(enter_fn, exit_fn)


def test_enter_raw_mode_idempotent(tty_terminal: Terminal) -> None:
    called = {"count": 0}

    def fake_enter(self: Terminal) -> None:
        called["count"] += 1

    with _patch_raw(fake_enter, lambda self: None):
        tty_terminal.enter_raw_mode()
        tty_terminal.enter_raw_mode()
    assert called["count"] == 1
    assert tty_terminal.in_raw_mode


def test_exit_raw_mode_idempotent(tty_terminal: Terminal) -> None:
    called = {"count": 0}

    def fake_exit(self: Terminal) -> None:
        called["count"] += 1

    with _patch_raw(lambda self: None, fake_exit):
        tty_terminal.enter_raw_mode()
        tty_terminal.exit_raw_mode()
        # Exit again — no-op.
        tty_terminal.exit_raw_mode()
    assert called["count"] == 1
    assert not tty_terminal.in_raw_mode


def test_enter_exit_enter_cycle(tty_terminal: Terminal) -> None:
    calls: list[str] = []

    def fake_enter(self: Terminal) -> None:
        calls.append("enter")

    def fake_exit(self: Terminal) -> None:
        calls.append("exit")

    with _patch_raw(fake_enter, fake_exit):
        tty_terminal.enter_raw_mode()
        tty_terminal.exit_raw_mode()
        tty_terminal.enter_raw_mode()
        tty_terminal.exit_raw_mode()
    assert calls == ["enter", "exit", "enter", "exit"]


def test_enter_raw_mode_noop_on_non_tty() -> None:
    term = Terminal(stdout=io.StringIO())
    term.enter_raw_mode()  # no exception
    assert not term.in_raw_mode


def test_on_key_without_raw_support_is_safe_noop() -> None:
    term = Terminal(stdout=io.StringIO())
    received: list[Key] = []
    dispose = term.on_key(lambda k: received.append(k))
    # No reader thread should be running.
    assert term._key_thread is None
    dispose()


def test_enable_input_noop_without_raw_support() -> None:
    """enable_input on a non-TTY must not start any thread."""
    term = Terminal(stdout=io.StringIO())
    term.on_key(lambda k: None)
    term.enable_input()
    assert term._key_thread is None
    assert not term.in_raw_mode


def test_on_key_subscribes_and_starts_reader(tty_terminal: Terminal) -> None:
    received: list[Key] = []

    chunks = iter([b"a"])

    def fake_read(fd: int, n: int) -> bytes:
        try:
            return next(chunks)
        except StopIteration:
            # Block so the reader thread doesn't busy-spin.
            time.sleep(0.05)
            return b""

    with (
        patch.object(_term_mod, "_read_stdin_chunk", fake_read),
        patch.object(_term_mod, "_wait_for_input", lambda fd, timeout: True),
        _patch_raw(lambda self: None, lambda self: None),
    ):
        dispose = tty_terminal.on_key(lambda k: received.append(k))
        # ``on_key`` registers but does not start the reader until
        # ``enable_input`` is called (mirrors the render-pipeline order).
        tty_terminal.enable_input()
        # Wait briefly for the reader thread to deliver.
        for _ in range(40):
            if received:
                break
            time.sleep(0.025)
        dispose()
    assert received
    assert received[0].input == "a"


def test_on_key_dispose_stops_callbacks(tty_terminal: Terminal) -> None:
    def fake_read(fd: int, n: int) -> bytes:
        time.sleep(0.05)
        return b""

    with (
        patch.object(_term_mod, "_read_stdin_chunk", fake_read),
        _patch_raw(lambda self: None, lambda self: None),
    ):
        dispose = tty_terminal.on_key(lambda k: None)
        tty_terminal.enable_input()
        # Reader thread was started.
        assert tty_terminal._key_thread is not None
        dispose()
        # Reader thread torn down after dispose.
        assert tty_terminal._key_thread is None


def test_multiple_subscribers_all_receive_key(tty_terminal: Terminal) -> None:
    received_a: list[Key] = []
    received_b: list[Key] = []

    def fake_read(fd: int, n: int) -> bytes:
        time.sleep(0.02)
        return b"x"

    with (
        patch.object(_term_mod, "_read_stdin_chunk", fake_read),
        patch.object(_term_mod, "_wait_for_input", lambda fd, timeout: True),
        _patch_raw(lambda self: None, lambda self: None),
    ):
        disp_a = tty_terminal.on_key(lambda k: received_a.append(k))
        disp_b = tty_terminal.on_key(lambda k: received_b.append(k))
        tty_terminal.enable_input()
        for _ in range(80):
            if received_a and received_b:
                break
            time.sleep(0.025)
        disp_a()
        disp_b()
    assert received_a and received_a[0].input == "x"
    assert received_b and received_b[0].input == "x"


def test_bad_handler_does_not_kill_reader_loop(tty_terminal: Terminal) -> None:
    """A handler that raises must be swallowed — the loop keeps running so
    a second handler still receives subsequent keys."""
    received_after_crash: list[Key] = []
    call_count = {"n": 0}

    def bad_handler(_key: Key) -> None:
        call_count["n"] += 1
        raise RuntimeError("boom")

    # Two chunks: the first triggers the crashing handler; the second is
    # delivered after, proving the loop survived.
    chunks = iter([b"a", b"b"])

    def fake_read(fd: int, n: int) -> bytes:
        try:
            return next(chunks)
        except StopIteration:
            time.sleep(0.05)
            return b""

    with (
        patch.object(_term_mod, "_read_stdin_chunk", fake_read),
        patch.object(_term_mod, "_wait_for_input", lambda fd, timeout: True),
        _patch_raw(lambda self: None, lambda self: None),
    ):
        tty_terminal.on_key(bad_handler)
        good = tty_terminal.on_key(lambda k: received_after_crash.append(k))
        tty_terminal.enable_input()
        for _ in range(80):
            if received_after_crash:
                break
            time.sleep(0.025)
        good()
    assert call_count["n"] >= 1
    assert received_after_crash, "good handler should still receive keys after a crash"


# ---------------------------------------------------------------------------
# Windows-specific tests
#
# These exercise the Windows raw-mode SetConsoleMode logic and the
# Windows input-read path (``msvcrt``-based) by mocking the ctypes /
# msvcrt modules. They run on any platform because we never touch a real
# console handle — we patch ``_PLATFORM`` to "windows" and stub the
# Win32 API surface.
# ---------------------------------------------------------------------------


def test_windows_raw_mode_enables_virtual_terminal_input(
    tty_terminal: Terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``enter_raw_mode`` on Windows must OR-in ``ENABLE_VIRTUAL_TERMINAL_INPUT``.

    Without this flag the Windows console emits legacy ``INPUT_RECORD``
    structs (not ANSI sequences), so arrow / Tab / function keys would
    never arrive as bytes through ``msvcrt``.
    """
    captured: dict[str, int] = {}

    class _FakeDWORD:
        def __init__(self, value: int = 0) -> None:
            self.value = value

    class _FakeKernel32:
        STD_INPUT_HANDLE = -10

        def GetStdHandle(self, _handle: int) -> int:
            return 1234

        def GetConsoleMode(self, _handle: int, mode_ref: object) -> int:
            # Pre-existing mode includes echo + line + processed input.
            assert isinstance(mode_ref, _FakeDWORD)
            mode_ref.value = 0xE7  # arbitrary existing console mode
            return 1

        def SetConsoleMode(self, _handle: int, mode: int) -> int:
            captured["new_mode"] = mode
            return 1

        def GetConsoleCP(self) -> int:
            return 936

        def GetConsoleOutputCP(self) -> int:
            return 936

        def SetConsoleCP(self, cp: int) -> int:
            captured["set_input_cp"] = cp
            return 1

        def SetConsoleOutputCP(self, cp: int) -> int:
            captured["set_output_cp"] = cp
            return 1

    # Build a fake ``ctypes`` module surface.
    class _FakeCtypes:
        windll = type("windll", (), {"kernel32": _FakeKernel32()})()

        @staticmethod
        def byref(obj: object) -> object:
            return obj

        class wintypes:  # noqa: N801 - mirrors real module name
            DWORD = _FakeDWORD

    # ``_enter_raw_mode_windows`` does a local ``import ctypes`` and a
    # ``from ctypes import wintypes``; patch sys.modules to swap them.
    import sys

    monkeypatch.setitem(sys.modules, "ctypes", _FakeCtypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", _FakeCtypes.wintypes)

    tty_terminal._enter_raw_mode_windows()

    ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
    ENABLE_ECHO_INPUT = 0x0004
    ENABLE_LINE_INPUT = 0x0002
    ENABLE_PROCESSED_INPUT = 0x0001
    new_mode = captured["new_mode"]
    assert new_mode & ENABLE_VIRTUAL_TERMINAL_INPUT, (
        "ENABLE_VIRTUAL_TERMINAL_INPUT must be set so the console "
        "translates special keys into ANSI escape sequences"
    )
    assert not (new_mode & ENABLE_ECHO_INPUT), "echo must be disabled"
    assert not (new_mode & ENABLE_LINE_INPUT), "line buffering must be disabled"
    assert not (new_mode & ENABLE_PROCESSED_INPUT), (
        "processed input must be disabled so Ctrl+C arrives as 0x03"
    )
    # Bug 9 follow-up: input + output codepages must be switched to UTF-8.
    assert captured.get("set_input_cp") == 65001
    assert captured.get("set_output_cp") == 65001
    # The original codepage must be captured for later restore.
    assert tty_terminal._prev_console_input_cp == 936
    assert tty_terminal._prev_console_output_cp == 936


def test_windows_raw_mode_exit_restores_previous_mode(
    tty_terminal: Terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``exit_raw_mode`` restores the previously-saved console mode + codepage."""
    restored: dict[str, int] = {}

    class _FakeKernel32:
        def GetStdHandle(self, _h: int) -> int:
            return 1234

        def SetConsoleMode(self, _h: int, mode: int) -> int:
            restored["mode"] = mode
            return 1

        def SetConsoleCP(self, cp: int) -> int:
            restored["input_cp"] = cp
            return 1

        def SetConsoleOutputCP(self, cp: int) -> int:
            restored["output_cp"] = cp
            return 1

    class _FakeCtypes:
        windll = type("windll", (), {"kernel32": _FakeKernel32()})()

    import sys

    monkeypatch.setitem(sys.modules, "ctypes", _FakeCtypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", type("wintypes", (), {}))

    tty_terminal._prev_console_mode = 0x1A7
    tty_terminal._prev_console_input_cp = 936
    tty_terminal._prev_console_output_cp = 936
    tty_terminal._exit_raw_mode_windows()
    assert restored["mode"] == 0x1A7
    # Bug 9 follow-up: original codepages must be restored on exit.
    assert restored["input_cp"] == 936
    assert restored["output_cp"] == 936
    assert tty_terminal._prev_console_mode is None
    assert tty_terminal._prev_console_input_cp is None
    assert tty_terminal._prev_console_output_cp is None


def test_windows_read_drains_multi_byte_escape_sequence(
    tty_terminal: Terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows read path must drain a full ``\\x1b[A`` in one chunk.

    Windows + ``ENABLE_VIRTUAL_TERMINAL_INPUT`` delivers arrow keys as
    three separate ``msvcrt.getch()`` calls. The reader has to pull all
    three bytes in one ``_read_stdin_chunk`` call so the key parser sees
    the complete sequence; otherwise the second ``os.read`` loop tick
    would lose the tail and only the lone ``\\x1b`` would surface.
    """
    # Bytes the mocked msvcrt.getch() yields in order.
    queued = [b"\x1b", b"[", b"A"]
    kbhit_returns = [True, True, True, False]

    class _FakeMsvcrt:
        @staticmethod
        def kbhit() -> bool:
            return kbhit_returns.pop(0) if kbhit_returns else False

        @staticmethod
        def getch() -> bytes:
            return queued.pop(0)

    import sys

    monkeypatch.setitem(sys.modules, "msvcrt", _FakeMsvcrt)
    monkeypatch.setattr(_term_mod, "_PLATFORM", "windows")

    chunk = _term_mod._read_stdin_chunk(fd=0, max_bytes=64)
    assert chunk == b"\x1b[A", (
        f"expected full ESC[A in one chunk, got {chunk!r}"
    )


def test_windows_input_loop_delivers_arrow_key(
    tty_terminal: Terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end Windows path: msvcrt bytes → Key(up_arrow=True).

    The reader thread pulls via ``_read_stdin_chunk`` (Windows branch),
    feeds the chunk through the key parser, and dispatches the resulting
    Key to every subscriber. A multi-byte escape sequence arriving in a
    single chunk is the exact scenario that broke on real Windows before
    the fix.
    """
    received: list[Key] = []

    chunks = iter([b"\x1b[A"])  # one Up arrow, delivered in a single chunk

    def fake_read(fd: int, n: int) -> bytes:
        try:
            return next(chunks)
        except StopIteration:
            time.sleep(0.05)
            return b""

    with (
        patch.object(_term_mod, "_read_stdin_chunk", fake_read),
        patch.object(_term_mod, "_wait_for_input", lambda fd, timeout: True),
        _patch_raw(lambda self: None, lambda self: None),
    ):
        dispose = tty_terminal.on_key(lambda k: received.append(k))
        tty_terminal.enable_input()
        for _ in range(80):
            if received:
                break
            time.sleep(0.025)
        dispose()
    assert received
    assert received[0].up_arrow, (
        f"arrow escape should parse to up_arrow=True, got {received[0]!r}"
    )
