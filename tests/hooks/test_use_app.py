"""Tests for :func:`pyink.hooks.use_app` (PR6)."""

from __future__ import annotations

import io
import threading
import time
from unittest.mock import patch

import pytest

from pyink import Text, create_element, render, use_app, use_input
from pyink.core.element import Element
from pyink.hooks.app import AppHandle
from pyink.render import terminal as _term_mod
from pyink.render.keys import Key
from pyink.render.terminal import Terminal


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def test_use_app_returns_handle_with_exit() -> None:
    handle_box: dict[str, AppHandle] = {}

    def Comp() -> Element:
        handle_box["handle"] = use_app()
        return Text("hi")

    inst = render(
        create_element(Comp),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    handle = handle_box["handle"]
    assert callable(handle.exit)
    assert callable(handle.wait_until_render_flush)
    handle.exit(None)
    inst.wait_until_exit()


def test_use_app_exit_triggers_unmount() -> None:
    handle_box: dict[str, AppHandle] = {}

    def Comp() -> Element:
        handle_box["handle"] = use_app()
        return Text("hi")

    inst = render(
        create_element(Comp),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    handle = handle_box["handle"]

    exited = threading.Event()

    def call_exit() -> None:
        handle.exit(None)
        exited.set()

    threading.Thread(target=call_exit).start()
    inst.wait_until_exit()
    assert exited.is_set()
    assert inst._unmounted


def test_use_app_exit_from_input_handler() -> None:
    """Calling exit() from a use_input handler should tear down the Instance."""
    received: list[str] = []

    def Comp() -> Element:
        app = use_app()

        def on_key(key: Key) -> None:
            received.append(key.input)
            app.exit(None)

        use_input(on_key)
        return Text("hi")

    chunks = iter([b"q"])

    def fake_read(fd: int, n: int) -> bytes:
        try:
            return next(chunks)
        except StopIteration:
            time.sleep(0.05)
            return b""

    with (
        patch.object(_term_mod, "_read_stdin_chunk", fake_read),
        patch.object(_term_mod, "_wait_for_input", lambda fd, timeout: True),
        patch.object(Terminal, "_enter_raw_mode_unix", lambda self: None),
        patch.object(Terminal, "_exit_raw_mode_unix", lambda self: None),
        patch.object(Terminal, "_enter_raw_mode_windows", lambda self: None),
        patch.object(Terminal, "_exit_raw_mode_windows", lambda self: None),
    ):
        inst = render(
            create_element(Comp),
            stdout=_FakeTTY(),
            stdin=_FakeTTY(),
            columns=40,
            rows=3,
        )
        for _ in range(40):
            if inst._unmounted:
                break
            time.sleep(0.025)
        assert inst._unmounted
        assert received and received[0] == "q"


def test_use_app_outside_render_raises() -> None:
    with pytest.raises(RuntimeError, match="use_app"):
        use_app()


def test_use_app_wait_until_render_flush() -> None:
    handle_box: dict[str, AppHandle] = {}

    def Comp() -> Element:
        handle_box["handle"] = use_app()
        return Text("hi")

    inst = render(
        create_element(Comp),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    handle = handle_box["handle"]
    handle.wait_until_render_flush()
    inst.unmount()

