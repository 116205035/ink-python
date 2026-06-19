"""Tests for raw-mode + on_key on :class:`pyink.render.terminal.Terminal`.

The Terminal raw-mode entry/exit logic is Unix-only and would mess with
the test runner's own stdin. These tests use monkey-patching to fake a
TTY (so ``is_raw_mode_supported`` returns True) and a fake
``os.read`` to drive synthetic bytes through the input loop.
"""

from __future__ import annotations

import io
import os as _os
import time
from collections.abc import Callable
from unittest.mock import patch

import pytest

from pyink.render import terminal as _term_mod
from pyink.render.keys import Key
from pyink.render.terminal import Terminal


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
        patch.object(_os, "read", fake_read),
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
        patch.object(_os, "read", fake_read),
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
        patch.object(_os, "read", fake_read),
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
        patch.object(_os, "read", fake_read),
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
