"""Context system — Provider/Consumer with tree-scoped values (Phase 2 PR5).

Mirrors React Context / Vue provide-inject / SolidJS context: a Provider
host element only affects descendants, and a consumer reads the nearest
Provider's value. The implementation is a single :class:`contextvars.ContextVar`
holding a stack of ``(context_id, value)`` pairs:

* :func:`create_context` mints a :class:`Context` with a unique id and a
  default value.
* :func:`Provider` returns a host ``"provider"`` :class:`Element` carrying
  ``_provider_ctx_id`` / ``_provider_value`` props. The reconciler pushes
  ``(ctx_id, value)`` on mount and pops on unmount (LIFO).
* :func:`use_context` (in :mod:`pyink.hooks.context`) walks the stack from
  top to bottom and returns the value for the first matching id, falling
  back to ``ctx.default`` when no Provider is active.

Why a ``ContextVar`` stack instead of walking the Instance tree:

* The signals model means component functions run exactly once at mount
  and never re-execute on prop changes. ``use_context`` therefore only
  needs to read once at mount — but it can be called from any descendant
  depth, and we want to avoid re-traversing parent links for every read.
* A flat ``ContextVar`` list is O(depth) for the linear scan but the
  scan stops at the first match, which in practice is one or two hops.
  It also keeps the :class:`Instance` Protocol untouched — no new
  parent-context attribute to maintain.
* ``ContextVar`` is async-safe by construction (PyInk is fully sync, but
  the door is open for a future async render loop without rewriting
  this module).

Reactive note: a Provider ``value`` is read by ``use_context`` exactly
once at component mount. Changing the value *without* also re-mounting
the consumer does not propagate — that is intentional. To make a
Provider's value reactive, pass a :class:`Signal` (or any callable) as
the value and have the consumer read it through ``use_context`` then
``.value`` / call it inside an effect or a callable ``Text`` leaf. This
matches PRD Decision 1: prop changes don't re-run component bodies.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from typing import Any, Generic, TypeVar

from pyink.core.element import Element, create_element

__all__ = [
    "Context",
    "Provider",
    "create_context",
    "get_context_stack",
    "pop_provider",
    "push_provider",
]

T = TypeVar("T")


class Context(Generic[T]):
    """Provider/Consumer context descriptor.

    Created via :func:`create_context`. Hold one as a module-level
    constant and share it between the Provider and the consumers — the
    id is what matches a Provider to its readers, not object identity
    (though they coincide in normal usage).

    The default value is returned by :func:`pyink.hooks.context.use_context`
    when no Provider for this Context is currently on the stack.
    """

    __slots__ = ("default", "id")

    default: T
    id: int

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Context(id={self.id}, default={self.default!r})"


# ---------------------------------------------------------------------------
# ID minting (module-level counter under a lock — module import is
# single-threaded, but ``create_context`` may be called from any thread
# at runtime, e.g. inside a lazily-initialised module).
# ---------------------------------------------------------------------------

_context_counter: int = 0
_context_counter_lock = threading.Lock()


def create_context(default: T) -> Context[T]:
    """Create a new :class:`Context` carrying ``default``.

    The returned Context should be kept as a module-level constant —
    callers share one Context between a Provider and its consumers so
    they agree on id. Creating a fresh Context on every render would
    orphan the consumers (their id never matches a Provider's id).
    """
    global _context_counter
    with _context_counter_lock:
        cid = _context_counter
        _context_counter += 1
    ctx: Context[T] = Context.__new__(Context)
    ctx.default = default
    ctx.id = cid
    return ctx


# ---------------------------------------------------------------------------
# Provider stack
# ---------------------------------------------------------------------------

#: Per-context stack of ``(context_id, value)`` pairs, top of stack at the
#: end of the list. A fresh Python context inherits an empty list (the
#: ``ContextVar`` default), so a worker thread that did not inherit an
# in-progress mount sees ``[]`` rather than a parent thread's half-built
#: stack. Pushes and pops go through :func:`push_provider` /
#: :func:`pop_provider` so the reconciler's integration stays the single
#: source of truth.
#: Per-Python-context stack of ``(context_id, value)`` pairs, top of
#: stack at the end of the list. The default is ``None`` and lazily
#: materialised into a fresh list by :func:`_stack` — ruff (B039)
#: forbids mutable ``ContextVar`` defaults, and the lazy-init path also
#: gives every Python context its own independent list (a worker thread
#: that did not inherit an in-progress mount sees an empty stack rather
#: than the parent thread's half-built one).
_context_stack: ContextVar[list[tuple[int, Any]] | None] = ContextVar(
    "pyink_context_stack",
    default=None,
)


def _stack() -> list[tuple[int, Any]]:
    """Return the active list, lazily installing a fresh one if needed."""
    current = _context_stack.get()
    if current is None:
        current = []
        _context_stack.set(current)
    return current


def get_context_stack() -> list[tuple[int, Any]]:
    """Return the current context stack (top at the end).

    Exposed so :func:`pyink.hooks.context.use_context` can scan it
    without reaching into a private attribute. Callers must treat the
    returned list as read-only.
    """
    return _stack()


def push_provider(ctx_id: int, value: Any) -> None:
    """Append ``(ctx_id, value)`` to the active stack.

    Called by the reconciler when a ``"provider"`` host instance mounts.
    Mutates the active list in place so that any descendant component
    bodies invoked afterwards — which inherit the same ``ContextVar``
    binding — see the new entry.
    """
    _stack().append((ctx_id, value))


def pop_provider(ctx_id: int) -> None:
    """Pop the topmost stack entry, asserting it belongs to ``ctx_id``.

    Called by the reconciler when a ``"provider"`` host instance's
    subtree finishes mounting. Nested Providers pop in strict LIFO order
    (the mount traversal is depth-first); a mismatch here means the
    reconciler is mis-ordered and we warn loudly rather than corrupting
    sibling Consumers.

    A missing entry is silently ignored — it can happen when the same
    Instance is unmounted twice (defensive cleanup), in which case we
    have nothing to pop.
    """
    stack = _context_stack.get()
    if not stack:
        return
    top_id, _top_value = stack[-1]
    if top_id != ctx_id:
        # Reconciler ordering bug: the top of the stack isn't us. Pop
        # ourselves out of the stack by value so we don't leak a stale
        # entry, and surface the mismatch as a RuntimeWarning instead of
        # silently corrupting descendants.
        import warnings

        warnings.warn(
            f"Context provider pop mismatch: expected ctx_id={ctx_id} "
            f"but top of stack is ctx_id={top_id}. The reconciler may be "
            "unmounting providers out of LIFO order.",
            RuntimeWarning,
            stacklevel=2,
        )
        # Remove the nearest matching entry from the top so we still
        # honour the unmount contract for the caller.
        for i in range(len(stack) - 1, -1, -1):
            if stack[i][0] == ctx_id:
                del stack[i]
                return
        return
    stack.pop()


# ---------------------------------------------------------------------------
# Provider host element
# ---------------------------------------------------------------------------


def Provider(ctx: Context[T], value: T, *children: Any) -> Element:
    """Build a host ``"provider"`` :class:`Element`.

    Mount side effect (handled by the reconciler):

    * push ``(ctx.id, value)`` onto the context stack on mount
    * pop on unmount

    Descendant component bodies inherit the stack via ``ContextVar`` and
    see ``value`` through :func:`pyink.hooks.context.use_context`.

    The ``_provider_*`` props are reserved (underscore-prefixed) so they
    cannot collide with user-supplied props. Callers always go through
    this helper — never build a ``"provider"`` element by hand.
    """
    return create_element(
        "provider",
        *children,
        _provider_ctx_id=ctx.id,
        _provider_value=value,
    )
