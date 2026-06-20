"""``use_context`` — read the nearest Provider's value (Phase 2 PR5).

Must be called inside a function-component body mounted via
:func:`pyink.render.render`. Walks the active context stack (set up by
:mod:`pyink.core.context` and pushed/popped by Provider host elements
in :mod:`pyink.core.reconciler`) from top to bottom and returns the
value for the first matching Context id. Falls back to ``ctx.default``
when no Provider for ``ctx`` is currently mounted.

Reactive note: this hook reads the stack exactly once at component
mount. Changing a Provider's value *without* re-mounting the consumer
does not propagate — that mirrors PRD Decision 1 (component bodies run
once). To make a Context value reactive, put a :class:`Signal` (or any
callable) inside it and read it through this hook, then dereference it
inside an :func:`effect` or a callable ``Text`` leaf so a signal read
establishes a subscription.
"""

from __future__ import annotations

from typing import TypeVar

from pyink.core.context import Context, get_context_stack
from pyink.hooks._runtime import _get_current_instance

__all__ = ["use_context"]

T = TypeVar("T")


def use_context(ctx: Context[T]) -> T:
    """Return the nearest Provider's value for ``ctx``, else ``ctx.default``.

    Raises
    ------
    RuntimeError
        If called outside a function-component body mounted via
        :func:`pyink.render.render`. The hook itself doesn't need the
        active Instance for anything other than this sanity check, but
        matching the other hooks' contract keeps the API predictable
        and surfaces misuse early (e.g. calling ``use_context`` from a
        background thread).
    """
    if _get_current_instance() is None:
        raise RuntimeError(
            "use_context() must be called from inside a function component "
            "mounted via pyink.render.render()"
        )
    target = ctx.id
    for cid, value in reversed(get_context_stack()):
        if cid == target:
            return value  # type: ignore[no-any-return]
    return ctx.default
