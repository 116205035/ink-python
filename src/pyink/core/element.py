"""Element descriptor — the immutable tree node (PR2).

An :class:`Element` is the description of a node in the component tree, akin
to a React element. It is a frozen, hashable value produced by
:func:`create_element` and consumed by the reconciler on mount.

Two kinds of element ``type`` exist:

* **Host element** — a string (``"text"``, ``"box"``, …). The reconciler
  materialises a :class:`pyink.core.component.HostInstance` and treats the
  children as either text leaves (``str`` / ``Callable[[], str]`` for
  ``"text"``) or nested elements (everything else).
* **Function component** — any callable returning an ``Element``, a tuple of
  them (fragment), a ``str``/``Callable[[], str]`` (auto-treated as a text
  leaf), or ``None`` (render nothing). The reconciler calls the function
  exactly once on mount — see PRD Decision 1 (signals model).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias, runtime_checkable

__all__ = [
    "Element",
    "ElementChild",
    "ElementType",
    "HostType",
    "create_element",
]


@runtime_checkable
class _SupportsElement(Protocol):
    """Structural protocol implemented by :class:`Element`."""

    type: Any
    props: dict[str, Any]
    children: tuple[Any, ...]


#: A leaf that may appear as a host ``"text"`` child or be returned directly
#: from a function component. Callables are evaluated lazily by the renderer.
TextLike: TypeAlias = str | Callable[[], str]

#: Anything that may be passed as a positional child to :func:`create_element`.
ElementChild: TypeAlias = "Element | TextLike"

#: Host element type — a tag string such as ``"text"`` or ``"box"``.
HostType: TypeAlias = str

#: The ``type`` slot of an :class:`Element`. Either a host tag string or a
#: function component callable.
ElementType: TypeAlias = "HostType | Callable[..., Any]"


def _is_element(obj: object) -> bool:
    """Structural check for ``Element``-like values (avoids import cycle)."""
    return isinstance(obj, Element)


def _flatten_children(
    children: tuple[Any, ...],
) -> tuple[Element | str | Callable[[], str], ...]:
    """Normalise the children tuple passed to :func:`create_element`.

    * ``None`` / ``True`` / ``False`` are dropped (conditional rendering).
    * Nested ``tuple`` / ``list`` are flattened one level per call (fragments).
    * ``Element`` / ``str`` / callable are kept verbatim.
    * Any other type raises ``TypeError``.
    """
    out: list[Element | str | Callable[[], str]] = []
    for child in children:
        if child is None or isinstance(child, bool):
            continue
        if isinstance(child, (tuple, list)):
            out.extend(_flatten_children(tuple(child)))
        elif isinstance(child, (str, Element)):
            out.append(child)
        elif callable(child):
            # Treat as a lazy text leaf (``Callable[[], str]``).
            out.append(child)
        else:
            raise TypeError(
                f"Unsupported child type {type(child).__name__!r}; expected "
                "Element, str, callable, tuple/list, None, or bool."
            )
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Element:
    """Immutable description of a tree node.

    ``props`` is a regular ``dict`` and is therefore *not* hashable; the
    dataclass opts out of ``eq=False`` hashing by leaving ``frozen=True``
    to provide field-level ``__setattr__`` protection without attempting
    to hash the whole element. Use identity comparison for Elements.
    """

    type: ElementType
    props: dict[str, Any] = field(default_factory=dict)
    children: tuple[Element | str | Callable[[], str], ...] = ()

    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:  # noqa: PYI034 - identity hash, intentional
        return id(self)


def create_element(
    type_: ElementType,
    *children: Any,
    **props: Any,
) -> Element:
    """Construct an :class:`Element`.

    ``children`` is processed by :func:`_flatten_children`:

    * nested ``tuple`` / ``list`` are flattened (fragment unpacking),
    * ``None`` / ``True`` / ``False`` are filtered (conditional rendering),
    * ``str`` is preserved as a text leaf,
    * callables are preserved as lazy text leaves,
    * ``Element`` instances are preserved.

    ``props`` is captured verbatim. Caller-supplied ``props`` are not
    deep-copied; the reconciler treats the dict as read-only after mount.
    """
    normalised = _flatten_children(children)
    return Element(type=type_, props=dict(props), children=normalised)
