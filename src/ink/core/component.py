"""Component instances produced by the reconciler (PR2).

A component instance is the mutable runtime counterpart of an
:class:`ink.core.element.Element`. Two concrete kinds exist:

* :class:`HostInstance` — materialised from a host element (``type`` is a
  tag string such as ``"text"`` or ``"box"``). Holds the original element
  and the recursive list of child instances (for non-text hosts) or the
  raw text leaves (for ``"text"`` hosts).
* :class:`ComponentInstance` — materialised from a function component. Owns
  the closure created by invoking the component function once, plus the
  list of effect-dispose callables registered inside the body. On unmount
  every dispose is invoked (in reverse registration order) so the user
  does not need to manually manage effect cleanup.

The :class:`Instance` :class:`Protocol` describes the shared surface used by
the reconciler and renderer. The reconciler is responsible for calling
``mount`` and ``unmount``; instances themselves are inert data holders.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from contextvars import Token
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ink.core import signal as _signal
from ink.core.element import Element

if TYPE_CHECKING:
    pass

__all__ = [
    "ComponentInstance",
    "HostInstance",
    "Instance",
    "TextLike",
]

#: A text leaf stored on a ``"text"`` HostInstance.
TextLike = str | Callable[[], str]


@runtime_checkable
class Instance(Protocol):
    """Common surface for all instance kinds.

    Attributes mirror the slot names used throughout the reconciler.
    """

    element: Element
    parent: Any
    children: list[Any]

    def mount(self) -> None: ...

    def unmount(self) -> None: ...


class HostInstance:
    """Runtime node for a host element (``type`` is a tag string).

    For ``type == "text"`` the ``children`` list contains raw text leaves
    (``str`` / ``Callable[[], str]``) — *not* nested instances. For any
    other tag the reconciler populates ``children`` with nested instances.
    """

    __slots__ = ("element", "parent", "children")

    def __init__(self, element: Element, parent: Instance | None) -> None:
        self.element: Element = element
        self.parent: Instance | None = parent
        # For "text": text leaves. Otherwise: list[Instance].
        self.children: list[Any] = []

    def mount(self) -> None:
        # Host instances are inert in PR2; layout/ANSI arrive in later PRs.
        return None

    def unmount(self) -> None:
        # No resources of our own; the reconciler handles child cleanup.
        return None


class ComponentInstance:
    """Runtime node for a function component.

    On mount the reconciler:

    1. Binds this instance as the current component via
       :func:`ink.core.signal._set_current_component` so ``effect(...)``
       calls register their dispose with us.
    2. Invokes ``element.type(**element.props)`` exactly once.
    3. Mounts the returned tree (Element / fragment / text / None) and
       stores the root instances in ``children``.

    On unmount the reconciler invokes every registered dispose in reverse
    order (mirror of registration) before recursing into children.
    """

    __slots__ = (
        "element",
        "parent",
        "children",
        "effect_disposes",
        "_disposed",
    )

    def __init__(self, element: Element, parent: Instance | None) -> None:
        self.element: Element = element
        self.parent: Instance | None = parent
        self.children: list[Instance] = []
        self.effect_disposes: list[Callable[[], None]] = []
        self._disposed: bool = False

    # -- Effect binder hook (called from ink.core.signal.effect) ---------

    def _on_effect_created(self, dispose: Callable[[], None]) -> None:
        """Register an effect dispose created while this instance is active."""
        if self._disposed:
            # Component already torn down — dispose immediately to honour the
            # side effect's cleanup contract even if creation raced unmount.
            dispose()
            return
        self.effect_disposes.append(dispose)

    def _on_effect_disposed(self, dispose: Callable[[], None]) -> None:
        """Remove a dispose that was invoked manually before unmount."""
        with suppress(ValueError):
            self.effect_disposes.remove(dispose)

    # -- Lifecycle ---------------------------------------------------------

    def mount(self) -> None:
        # The reconciler drives the actual function invocation; this method
        # is a no-op so callers can treat instances uniformly.
        return None

    def unmount(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        # Reverse order mirrors React's child-before-parent cleanup for the
        # dispose list itself; the reconciler is responsible for recursing
        # into ``children`` separately.
        for dispose in reversed(self.effect_disposes):
            with suppress(Exception):
                # Cleanup failures must not cascade — keep unmounting.
                dispose()
        self.effect_disposes.clear()


def bind_current_component(instance: ComponentInstance | None) -> Token[object | None]:
    """Set ``instance`` as the active component for ``effect(...)`` binding.

    Returns the opaque token to pass to :func:`restore_current_component`.
    """
    return _signal._set_current_component(instance)


def restore_current_component(token: Token[object | None]) -> None:
    """Restore the previous component binding."""
    _signal._reset_current_component(token)
