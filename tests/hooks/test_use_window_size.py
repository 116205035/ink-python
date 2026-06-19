"""Tests for :func:`pyink.hooks.use_window_size` (PR6)."""

from __future__ import annotations

import io

import pytest

from pyink import Text, create_element, render, use_window_size
from pyink.core.element import Element
from pyink.hooks.window_size import WindowSize


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def test_use_window_size_returns_initial_size() -> None:
    size_box: dict[str, WindowSize] = {}

    def Comp() -> Element:
        size = use_window_size()
        size_box["size"] = size
        return Text("hi")

    inst = render(
        create_element(Comp),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=42,
        rows=10,
        exit_on_ctrl_c=False,
    )
    size = size_box["size"]
    assert size.columns == 42
    assert size.rows == 10
    inst.unmount()


def test_use_window_size_returns_snapshot_columns_rows() -> None:
    size_box: dict[str, WindowSize] = {}

    def Comp() -> Element:
        size = use_window_size()
        size_box["size"] = size
        return Text("hi")

    inst = render(
        create_element(Comp),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=80,
        rows=24,
        exit_on_ctrl_c=False,
    )
    size = size_box["size"]
    assert size.columns == 80
    assert size.rows == 24
    inst.unmount()


def test_use_window_size_outside_render_falls_back() -> None:
    size = use_window_size()
    assert size.columns >= 1
    assert size.rows >= 1


def test_window_size_is_frozen_dataclass() -> None:
    size = use_window_size()
    # FrozenInstanceError is a subclass of AttributeError.
    with pytest.raises(AttributeError):
        size.columns = 0  # type: ignore[misc]
