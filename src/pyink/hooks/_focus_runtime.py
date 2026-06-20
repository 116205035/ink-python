"""Focus runtime data structures for :mod:`pyink.hooks.focus` (Phase 2 PR6).

Mirrors ink's ``use-focus`` / ``use-focus-manager`` pair (see
``D:/Projects/github/ink/ink-master/src/hooks/use-focus-manager.ts``).

Three pieces live here so :mod:`pyink.hooks.focus` can stay focused on the
hook integration glue:

* :class:`FocusHandle` — the per-component handle returned by
  :func:`pyink.hooks.focus.use_focus`. Carries a unique id and a
  ``is_focused`` :class:`~pyink.core.signal.Signal` consumers can subscribe
  to (read ``.value`` inside a callable ``Text`` leaf or an ``effect`` to
  re-render on focus changes).
* :class:`FocusManager` — the per-subtree registry of handles. Owns the
  ordered list of registered handles, the active index, and an ``enabled``
  flag. Tab/Shift+Tab cycle through ``active`` handles; the manager sets
  ``is_focused.value`` on exactly one handle at a time.
* :class:`NullFocusManager` — a no-op fallback returned by
  :func:`~pyink.core.context.use_context` when a ``use_focus`` consumer is
  mounted outside any ``use_focus_manager`` subtree. Its ``is_focused``
  signal is always ``False`` and every method is a no-op, so consumer code
  doesn't need to special-case "no manager".

State is held in :class:`~pyink.core.signal.Signal` objects so the render
loop re-paints on writes — the focus bookkeeping itself never re-builds
the component tree (PRD Decision 1 — component bodies run once).

Thread-safety: PyInk is fully sync, but the underlying signals carry their
own per-instance ``RLock``. The manager methods are safe to call from the
input-reader thread (the typical Tab handler context) and from the render
loop; no extra locking is needed here.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import TYPE_CHECKING

from pyink.core.signal import Signal

if TYPE_CHECKING:
    from pyink.core.element import Element

__all__ = [
    "FocusHandle",
    "FocusManager",
    "FocusManagerHandle",
    "NullFocusManager",
]


#: Process-wide focus handle id source. Sequential ids give clean debug
#: output and stable ordering within a single render. Component bodies
#: always supply their own id via ``options["id"]`` when they care about
#: identity (e.g. ``focus("input-2")``); the auto id is purely a fallback.
_handle_id_counter = itertools.count(1)


def _next_handle_id() -> str:
    """Return the next auto-generated handle id (monotonic across the process)."""
    return f"pyink-focus-{next(_handle_id_counter)}"


class FocusHandle:
    """A single focusable component's handle.

    Created by :func:`pyink.hooks.focus.use_focus` and registered with the
    nearest :class:`FocusManager`. The ``is_focused`` signal is the only
    piece consumers normally read — write access goes through
    :meth:`focus_self` / :meth:`blur`, both of which delegate to the owning
    manager so the manager's bookkeeping stays consistent.

    Attributes
    ----------
    id:
        Unique identifier. Either user-supplied via ``options["id"]`` or
        auto-generated. Stable across the component's lifetime.
    is_focused:
        :class:`~pyink.core.signal.Signal` of ``bool``. ``True`` while
        this handle is the manager's active handle. Read ``.value`` inside
        a callable style prop (``Text`` / ``Box`` / …) or an ``effect`` to
        subscribe to focus changes. Always ``False`` under a
        :class:`NullFocusManager`.
    is_active:
        Mutable ``bool`` flag. When ``False`` the handle stays registered
        but is skipped by :meth:`FocusManager.focus_next` /
        :meth:`FocusManager.focus_previous`. Toggling this is the standard
        way to temporarily disable a focus target (e.g. a disabled input).
    """

    __slots__ = (
        "id",
        "is_focused",
        "is_active",
        "_manager",
    )

    def __init__(
        self,
        id: str,
        manager: FocusManager,
        *,
        is_active: bool = True,
    ) -> None:
        self.id: str = id
        self.is_focused: Signal[bool] = signal_bool(False)
        self.is_active: bool = is_active
        # ``_manager`` is set lazily after registration so the manager can
        # construct the handle before pushing it into its registry. For a
        # NullFocusManager the value stays ``None`` and the methods no-op.
        self._manager: FocusManager | None = manager

    def focus_self(self) -> None:
        """Ask the owning manager to focus this handle.

        No-op under a :class:`NullFocusManager` or before registration
        completes. The manager updates ``is_focused`` on the previous
        active handle (if any) and on this one.
        """
        mgr = self._manager
        if mgr is None:
            return
        mgr.focus(self.id)

    def blur(self) -> None:
        """Clear focus from this handle.

        Under a real :class:`FocusManager` this moves focus to the next
        active handle (so Tab navigation keeps working). If no other
        active handle exists, focus is cleared entirely. Under a
        :class:`NullFocusManager` it is a no-op.
        """
        mgr = self._manager
        if mgr is None:
            return
        # If this handle isn't currently focused, blur is a no-op — there
        # is nothing to clear. This matches ink's behaviour (``blur`` only
        # matters for the active node).
        if not self.is_focused.value:
            return
        # Find the next distinct active handle. If none exists, clear focus
        # entirely instead of wrapping back onto ourselves — that's the
        # behaviour callers expect from ``blur`` (focus should leave this
        # handle, not ping-pong back to it).
        active_indices = mgr._active_indices()
        if len(active_indices) <= 1:
            mgr._set_active_index(None)
            return
        mgr.focus_next()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FocusHandle(id={self.id!r}, is_focused={self.is_focused.value})"


def signal_bool(initial: bool) -> Signal[bool]:
    """Construct a ``Signal[bool]`` — kept here as a small alias.

    The explicit type annotation helps mypy strict narrow the generic
    parameter without an extra ``# type: ignore`` at every call site.
    """
    return Signal(initial)


class FocusManager:
    """Registry + active-index tracker for a focus subtree.

    A new instance is created on every call to
    :func:`pyink.hooks.focus.use_focus_manager`. The instance is then
    injected into the subtree via a :class:`~pyink.core.context.Provider`
    and read back by :func:`~pyink.hooks.focus.use_focus` via
    :func:`~pyink.hooks.context.use_context`.

    The manager is intentionally permissive:

    * Handles register in arbitrary order; the manager preserves
      registration order for Tab navigation (first-registered is first in
      the cycle).
    * ``focus_next`` / ``focus_previous`` wrap around the active handles.
    * When the active handle is unregistered the manager clears focus
      rather than silently moving it — callers that want "jump to next"
      semantics on unmount should call ``focus_next`` themselves.
    * ``enabled=False`` clears every handle's ``is_focused`` signal.
      Re-enabling restores the previously active handle if it's still
      registered and active.
    """

    __slots__ = (
        "handles",
        "active_index",
        "enabled",
        "_last_active_id",
    )

    def __init__(self) -> None:
        # ``handles`` is a Signal carrying the live list so reads inside
        # an effect/computed subscribe to register/unregister events.
        # We mutate the list in place under a re-assign-style write so the
        # signal notices the change (signals compare by ``==``; a list
        # mutated in place would still ``==`` itself and swallow the
        # notification). The cleanest way is to assign a fresh list each
        # mutation.
        self.handles: Signal[list[FocusHandle]] = Signal([])
        self.active_index: Signal[int | None] = Signal(None)
        self.enabled: Signal[bool] = Signal(True)
        # Remember the id of the last focused handle so disable/enable can
        # restore the same target. Plain attribute (no signal needed —
        # this is internal bookkeeping that doesn't drive renders).
        self._last_active_id: str | None = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, handle: FocusHandle) -> Callable[[], None]:
        """Append ``handle`` to the registry and return its unregister fn.

        The returned callable removes the handle (idempotent — safe to
        call more than once, e.g. once manually and once on unmount).
        Removing the currently focused handle clears focus.
        """
        handle._manager = self
        current = self.handles.value
        # Avoid duplicate registration of the same id — a component that
        # mounts twice without unmounting (shouldn't happen in normal
        # usage but is defensive) would otherwise show up twice.
        if any(h.id == handle.id for h in current):
            return lambda: None
        self.handles.value = [*current, handle]
        return lambda: self._unregister(handle)

    def _unregister(self, handle: FocusHandle) -> None:
        current = self.handles.value
        if handle not in current:
            return
        remaining = [h for h in current if h is not handle]
        self.handles.value = remaining
        idx = self.active_index.value
        if idx is not None:
            active = current[idx] if idx < len(current) else None
            if active is handle:
                # Active handle removed — clear ``is_focused`` on it and
                # reset the active index. ``_set_active_index`` would
                # no-op here because ``prev_idx == None`` is already the
                # case we want, so we clear the signal explicitly first
                # to keep the removed handle visually consistent with
                # ``active_index is None``.
                if handle.is_focused.value:
                    handle.is_focused.value = False
                self.active_index.value = None

    # ------------------------------------------------------------------
    # Focus navigation
    # ------------------------------------------------------------------

    def focus_next(self) -> None:
        """Move focus to the next active handle (wraps around).

        No-op when no active handles are registered.
        """
        if not self.enabled.value:
            return
        active_indices = self._active_indices()
        if not active_indices:
            self._set_active_index(None)
            return
        current = self.active_index.value
        if current is None or current not in active_indices:
            next_idx = active_indices[0]
        else:
            pos = active_indices.index(current)
            next_idx = active_indices[(pos + 1) % len(active_indices)]
        self._set_active_index(next_idx)

    def focus_previous(self) -> None:
        """Move focus to the previous active handle (wraps around)."""
        if not self.enabled.value:
            return
        active_indices = self._active_indices()
        if not active_indices:
            self._set_active_index(None)
            return
        current = self.active_index.value
        if current is None or current not in active_indices:
            next_idx = active_indices[-1]
        else:
            pos = active_indices.index(current)
            next_idx = active_indices[(pos - 1) % len(active_indices)]
        self._set_active_index(next_idx)

    def focus(self, id: str) -> None:
        """Focus the handle with the given id (no-op if not registered)."""
        if not self.enabled.value:
            return
        for i, h in enumerate(self.handles.value):
            if h.id == id:
                self._set_active_index(i)
                return
        # Unknown id — leave focus untouched. This matches ink's
        # ``focus(id=...)`` tolerance of stale ids (e.g. a child that
        # already unmounted).

    # ------------------------------------------------------------------
    # Enable / disable
    # ------------------------------------------------------------------

    def enable(self) -> None:
        """Re-enable focus management.

        Restores focus to the last active handle if it's still registered
        and active; otherwise clears focus.
        """
        if self.enabled.value:
            return
        self.enabled.value = True
        last_id = self._last_active_id
        if last_id is not None:
            for i, h in enumerate(self.handles.value):
                if h.id == last_id and h.is_active:
                    self._set_active_index(i)
                    return
        self._set_active_index(None)

    def disable(self) -> None:
        """Disable focus management — clears every handle's ``is_focused``."""
        if not self.enabled.value:
            return
        # Remember the active id so ``enable()`` can restore it.
        idx = self.active_index.value
        if idx is not None:
            handles = self.handles.value
            if 0 <= idx < len(handles):
                self._last_active_id = handles[idx].id
        self.enabled.value = False
        self._set_active_index(None)

    # ------------------------------------------------------------------
    # Read-only views
    # ------------------------------------------------------------------

    @property
    def active_id(self) -> str | None:
        """Id of the currently focused handle, or ``None``."""
        idx = self.active_index.value
        if idx is None:
            return None
        handles = self.handles.value
        if 0 <= idx < len(handles):
            return handles[idx].id
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _active_indices(self) -> list[int]:
        """Indices of handles whose ``is_active`` flag is ``True``."""
        return [i for i, h in enumerate(self.handles.value) if h.is_active]

    def _set_active_index(self, idx: int | None) -> None:
        """Update ``active_index`` and flip ``is_focused`` on the affected handles.

        Writes are batched per-call so consumers reading
        ``is_focused.value`` in a single render pass observe a consistent
        snapshot. We don't use :func:`pyink.core.signal.batch` here to
        avoid a hard dependency on the batch API from inside the manager
        (and the multi-write overhead is bounded by the number of affected
        handles, which is at most 2).
        """
        prev_idx = self.active_index.value
        if prev_idx == idx:
            return
        handles = self.handles.value
        # Clear previous.
        if prev_idx is not None and 0 <= prev_idx < len(handles):
            prev_handle = handles[prev_idx]
            if prev_handle.is_focused.value:
                prev_handle.is_focused.value = False
        # Set new.
        if idx is not None and 0 <= idx < len(handles):
            new_handle = handles[idx]
            if not new_handle.is_focused.value:
                new_handle.is_focused.value = True
            self._last_active_id = new_handle.id
        else:
            # Going to no-focus — keep ``_last_active_id`` so a later
            # ``enable()`` can restore it.
            pass
        self.active_index.value = idx


class NullFocusManager(FocusManager):
    """No-op fallback used when no ``use_focus_manager`` is mounted.

    All navigation / enable / disable methods are no-ops. ``register``
    still wires the handle's ``_manager`` to ``self`` so the handle's
    :meth:`FocusHandle.focus_self` / :meth:`FocusHandle.blur` don't crash,
    but they have no effect — ``is_focused`` stays ``False`` for the
    handle's entire lifetime.
    """

    __slots__ = ()

    def __init__(self) -> None:
        # Skip ``FocusManager.__init__`` — we don't need the signals. But
        # ``FocusHandle`` callers may still read ``.handles.value`` etc.
        # (e.g. for introspection), so populate the same attributes with
        # inert values.
        self.handles = Signal([])
        self.active_index = Signal(None)
        self.enabled = Signal(False)
        self._last_active_id = None

    def register(self, handle: FocusHandle) -> Callable[[], None]:
        # Wire the handle back to us so ``focus_self`` / ``blur`` don't
        # blow up — but never set ``is_focused`` to ``True``.
        handle._manager = self
        return lambda: None

    def _unregister(self, handle: FocusHandle) -> None:
        return None

    def focus_next(self) -> None:
        return None

    def focus_previous(self) -> None:
        return None

    def focus(self, id: str) -> None:
        return None

    def enable(self) -> None:
        return None

    def disable(self) -> None:
        return None

    def _set_active_index(self, idx: int | None) -> None:
        return None

    @property
    def active_id(self) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Control handle returned by use_focus_manager
# ---------------------------------------------------------------------------


class FocusManagerHandle:
    """Control surface returned by :func:`pyink.hooks.focus.use_focus_manager`.

    Carries the underlying :class:`FocusManager` (for direct access when
    needed) and a ``wrap`` callable that builds a
    :class:`~pyink.core.context.Provider` injecting the manager into the
    subtree. This dual API lets the caller both drive focus navigation
    (``focus_next`` / ``focus_previous`` / ``focus`` / ``enable`` /
    ``disable`` / ``active_id``) and mount the Provider that descendant
    ``use_focus`` consumers read from.

    Why a wrapper object rather than just returning the manager:

    * The manager needs to live in a :class:`~pyink.core.context.Provider`
      for descendants to read it — but the manager itself doesn't know
      about the Element tree, so it can't construct the Provider.
    * The caller wants a single value that exposes both the control API
      and the Provider builder (otherwise they'd juggle two values).
    * Forwarding every manager method on this object keeps the call sites
      short (``focus.focus_next()`` instead of ``focus.manager.focus_next()``).
    """

    __slots__ = ("manager",)

    def __init__(self, manager: FocusManager) -> None:
        self.manager: FocusManager = manager

    # -- Control API (delegated to the manager) ---------------------------

    def focus_next(self) -> None:
        self.manager.focus_next()

    def focus_previous(self) -> None:
        self.manager.focus_previous()

    def focus(self, id: str) -> None:
        self.manager.focus(id)

    def enable(self) -> None:
        self.manager.enable()

    def disable(self) -> None:
        self.manager.disable()

    @property
    def active_id(self) -> str | None:
        return self.manager.active_id

    @property
    def enabled(self) -> bool:
        return self.manager.enabled.value

    # -- Provider builder -------------------------------------------------

    def wrap(self, *children: object) -> Element:
        """Build a Provider Element injecting this manager into the subtree.

        Imported lazily inside the method so this module doesn't import
        ``pyink.core.context`` at module load (which would create a cycle
        when ``pyink.core.context`` itself ever needs to introspect focus
        state — it currently doesn't, but the laziness is cheap insurance).
        """
        from pyink.core.context import Provider  # local import: avoid cycle
        from pyink.core.element import Element as _Element
        from pyink.hooks.focus import _FOCUS_MANAGER_CONTEXT

        result: _Element = Provider(_FOCUS_MANAGER_CONTEXT, self.manager, *children)
        return result

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FocusManagerHandle(active_id={self.active_id!r})"
