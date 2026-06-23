"""``use_app`` — access the live :class:`Instance` from inside a component (PR6).

Returns an :class:`AppHandle` with ``exit`` and ``wait_until_render_flush``
methods. ``exit`` triggers :meth:`Instance.unmount`; useful for wiring
``Ctrl+C`` / ``q`` handlers in raw mode.

Mirrors ink's ``useApp`` hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ink.hooks._runtime import _get_current_instance

__all__ = ["AppHandle", "use_app"]


@dataclass(frozen=True, slots=True)
class AppHandle:
    """Handle to the active :class:`Instance`.

    Both callables are safe to invoke from any thread (the underlying
    Instance methods take their own lock).
    """

    #: Trigger ``Instance.unmount``. ``exit_code`` is accepted for
    #: parity with ink's API but currently unused — PyInk does not have
    #: a process-level exit code concept. Pass anything (or ``None``).
    exit: Any  # Callable[[Any], None]
    #: Block until the next throttled render frame has been written.
    wait_until_render_flush: Any  # Callable[[], None]


def use_app() -> AppHandle:
    """Return the :class:`AppHandle` for the active :class:`Instance`.

    Must be called from inside a function-component body mounted via
    :func:`ink.render.render`. Outside that context the call raises
    ``RuntimeError`` (defensive — silently returning a no-op handle
    would hide bugs).
    """
    inst = _get_current_instance()
    if inst is None:
        raise RuntimeError(
            "use_app() must be called from inside a function component "
            "mounted via ink.render.render()"
        )

    def _exit(_code: Any = None) -> None:
        inst.unmount()

    def _wait_flush() -> None:
        # Pump one final frame so any pending signal writes are painted
        # before the caller observes the screen.
        inst._paint_now()
        # Wait for any throttle-scheduled paint to land.
        throttle = getattr(inst, "throttle", None)
        if throttle is not None:
            # Schedule a no-op so the throttle thread knows there's
            # work, then briefly wait for it to drain.
            import time

            deadline = time.monotonic() + 1.0
            pending_attr = "_pending"
            while time.monotonic() < deadline:
                if not getattr(throttle, pending_attr, None):
                    break
                time.sleep(0.005)

    return AppHandle(exit=_exit, wait_until_render_flush=_wait_flush)
