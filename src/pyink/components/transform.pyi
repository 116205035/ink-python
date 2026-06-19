"""Stubs for :mod:`pyink.components.transform` (PR7 — Decision 8)."""

from collections.abc import Callable
from typing import Any

from pyink.core.element import Element

def Transform(
    *children: Any,
    transform: Callable[[str, int], str],
    **_props: Any,
) -> Element: ...
