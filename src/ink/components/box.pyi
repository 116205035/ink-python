"""Stubs for :mod:`ink.components.box` (PR4 — Decision 8)."""

from typing import Any, TypeVar

from ink.core.element import Element

_T = TypeVar("_T")


def Box(*children: Any, **props: Any) -> Element: ...
