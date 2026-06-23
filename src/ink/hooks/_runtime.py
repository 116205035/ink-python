"""Internal runtime registry linking hooks to the live :class:`Instance`.

PyInk function-component bodies run inside the reconciler's
``bind_current_component`` block. Hooks (``use_input`` / ``use_app`` /
``use_window_size``) need access to the *active* :class:`Instance` —
which owns the :class:`Terminal`, the render loop, and the unmount
handle — so they can subscribe to events and bind cleanups.

We expose that via a :class:`contextvars.ContextVar` set by
:func:`ink.render.pipeline.render` around the initial mount. Because
component bodies run synchronously inside ``render``, the ContextVar is
populated by the time ``use_input`` is called.

Hook → current-component binding (for automatic dispose-on-unmount)
goes through :mod:`ink.core.signal`'s ``_current_component`` exactly
the same way ``effect`` does.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ink.render.instance import Instance

__all__ = ["_get_current_instance", "_set_current_instance", "_reset_current_instance"]


#: Active Instance during a ``render()`` mount. ``None`` outside mount.
_current_instance: ContextVar[Instance | None] = ContextVar(
    "ink_current_instance", default=None
)


def _set_current_instance(instance: Instance | None) -> Token[Instance | None]:
    return _current_instance.set(instance)


def _reset_current_instance(token: Token[Instance | None]) -> None:
    _current_instance.reset(token)


def _get_current_instance() -> Instance | None:
    return _current_instance.get()
