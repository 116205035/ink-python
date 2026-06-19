"""Render pipeline — entry point for live TUI rendering (PR5).

``render(tree, ...)`` mounts an Element tree, sets up the reactive
render loop, registers terminal-resize and Ctrl-C handlers, and returns
an :class:`pyink.render.instance.Instance`.

The render loop is a regular :func:`pyink.core.signal.effect`. Its body
runs a layout pass so any callable ``Text`` children that read
``signal.value`` do so *inside* the effect's tracking context — those
reads establish the subscriptions. The actual stdout write goes through
a :class:`pyink.render.instance._FpsThrottle`, which coalesces a burst
of signal writes into at most one paint per ``1/max_fps`` seconds.

Inline mode (PRD Decision 3) is the default. Alternate screen is
opt-in via ``alternate_screen=True``.
"""

from __future__ import annotations

import atexit
import signal
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TextIO

from pyink.core.element import Element
from pyink.core.reconciler import Reconciler
from pyink.hooks._runtime import (
    _reset_current_instance,
    _set_current_instance,
)
from pyink.render.instance import Instance, _FpsThrottle
from pyink.render.terminal import Terminal

__all__ = ["RenderOptions", "render"]


@dataclass(slots=True)
class RenderOptions:
    """Options bag for :func:`render`.

    Kept deliberately small — terminal-specific knobs (alternate screen,
    exit_on_ctrl_c, max_fps) are keyword-only on :func:`render` so the
    call-site is self-documenting.
    """

    columns: int | None = None
    rows: int | None = None


def render(
    tree: Element,
    *,
    stdout: TextIO | None = None,
    stdin: TextIO | None = None,
    columns: int | None = None,
    rows: int | None = None,
    alternate_screen: bool = False,
    exit_on_ctrl_c: bool = True,
    max_fps: int = 30,
) -> Instance:
    """Mount ``tree`` and start the reactive render loop.

    Parameters
    ----------
    tree:
        Root Element. Function-component bodies run exactly once during
        mount; subsequent state changes propagate through signals to the
        render loop.
    stdout:
        Where to write ANSI. Defaults to :data:`sys.stdout`.
    stdin:
        Where to read keyboard input from. Defaults to :data:`sys.stdin`.
        Useful for tests that drive synthetic input through the Terminal.
    columns / rows:
        Fixed viewport size. ``None`` auto-detects via
        :class:`Terminal` (and re-detects on resize). Useful for tests.
    alternate_screen:
        Enter the terminal's alternate screen buffer on mount and exit
        it on unmount. Default ``False`` (inline mode, PRD Decision 3).
    exit_on_ctrl_c:
        Treat ``SIGINT`` as a request to call :meth:`Instance.unmount`.
        Default ``True``. Disable to drive the lifecycle from Python
        directly.
    max_fps:
        Maximum frame rate the render loop will honour. Multiple signal
        writes inside one ``1/max_fps`` window collapse into a single
        paint.

    Returns
    -------
    Instance
        The live handle. Call :meth:`Instance.wait_until_exit` to block
        the calling thread, :meth:`Instance.unmount` to tear down.
    """
    out: TextIO = stdout if stdout is not None else sys.stdout
    terminal = Terminal(out, stdin=stdin)
    options = RenderOptions(columns=columns, rows=rows)
    reconciler = Reconciler()
    throttle = _FpsThrottle(max_fps=max_fps)
    inst = Instance(
        stdout=out,
        terminal=terminal,
        options=options,
        reconciler=reconciler,
        throttle=throttle,
    )
    inst.columns = terminal.columns
    inst.rows = terminal.rows

    # Mount the tree (component functions run here, exactly once). We
    # bind the Instance to a ContextVar for the duration of mount so
    # hooks (``use_input`` / ``use_app`` / ``use_window_size``) called
    # from inside component bodies can find it.
    token = _set_current_instance(inst)
    try:
        inst._mount_initial(tree)
    finally:
        _reset_current_instance(token)

    # Optional alternate screen.
    if alternate_screen:
        terminal.enter_alternate_screen()

    # Build + start the render loop (initial paint runs synchronously).
    inst._start_render_loop()

    # Resize -> force-flush a paint (bypasses the FPS throttle so a
    # window resize is reflected immediately).
    inst._resize_dispose = terminal.on_resize(lambda _c, _r: throttle.schedule(inst._paint_now))

    # atexit + SIGINT handlers.
    inst._atexit_registered = True
    atexit.register(inst.cleanup)
    if exit_on_ctrl_c:
        inst._sigint_dispose = _install_sigint(inst)

    # Mark mount complete before subscribing the raw-mode Ctrl+C
    # listener — that subscription starts the input-reader thread, and
    # we don't want a queued key to fire ``app.exit()`` → ``unmount``
    # before the rest of the setup is finished.
    inst._mount_complete = True

    if exit_on_ctrl_c:
        _install_raw_ctrl_c(terminal, inst)

    # Now that all disposes are wired up, start the input-reader thread
    # (if any subscribers exist). Order matters: subscribers register
    # during component mount, but the reader only starts here.
    terminal.enable_input()

    return inst


# ---------------------------------------------------------------------------
# SIGINT handler
# ---------------------------------------------------------------------------


#: Process-wide SIGINT registry. ``signal.signal`` may only be called from
#: the main thread; off-main-thread callers silently skip SIGINT handling
#: (the atexit hook still runs on interpreter exit).
_sigint_lock = threading.Lock()
_sigint_installed: bool = False
_sigint_prev_handler: object | None = None
_sigint_instances: list[Instance] = []


def _install_sigint(inst: Instance) -> Callable[[], None]:
    """Register ``inst`` for unmount-on-SIGINT. Returns a dispose callable."""
    global _sigint_installed, _sigint_prev_handler
    with _sigint_lock:
        _sigint_instances.append(inst)
        if not _sigint_installed:
            try:
                _sigint_prev_handler = signal.getsignal(signal.SIGINT)
                signal.signal(signal.SIGINT, _on_sigint)
                _sigint_installed = True
            except (ValueError, OSError):  # pragma: no cover
                # Off-main-thread callers fall back to atexit-only.
                _sigint_installed = False

    def dispose() -> None:
        with _sigint_lock, suppress(ValueError):
            _sigint_instances.remove(inst)

    return dispose


def _on_sigint(signum: int, frame: object) -> None:
    """SIGINT handler — unmount every active Instance, then chain.

    Uses a snapshot to survive a callback that removes itself mid-loop.
    """
    with _sigint_lock:
        snapshot = list(_sigint_instances)
    for inst in snapshot:
        with suppress(Exception):
            inst.unmount()
    prev = _sigint_prev_handler
    if callable(prev) and prev is not None:
        with suppress(Exception):
            prev(signum, frame)


def _install_raw_ctrl_c(terminal: Terminal, inst: Instance) -> None:
    """Subscribe a Ctrl+C listener for raw mode. No-op on non-TTY.

    Should only be called once ``inst._mount_complete`` is ``True`` —
    otherwise the reader thread may fire a handler that calls
    ``inst.unmount()`` before the rest of the mount has finished.
    """
    from pyink.render.keys import Key

    def on_key(key: Key) -> None:
        # In raw mode Ctrl+C arrives as byte 0x03 → Key(input='c', ctrl=True).
        if key.ctrl and key.input == "c":
            with suppress(Exception):
                inst.unmount()

    inst._ctrl_c_dispose = terminal.on_key(on_key)
