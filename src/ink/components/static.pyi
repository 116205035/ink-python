"""Stubs for :mod:`ink.components.static` (PR7 — Decision 8)."""

from collections.abc import Callable
from typing import TypeVar

from ink.core.element import Element
from ink.core.signal import Signal

_T = TypeVar("_T")

def Static(
    items: list[_T] | Signal[list[_T]] | Callable[[], list[_T]],
    render_item: Callable[[_T, int], Element],
    *,
    style: dict[str, object] | None = ...,
) -> Element: ...
