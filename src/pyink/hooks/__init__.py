"""PyInk hooks (PR6 + Phase 2 PR1 + PR5 + PR6 + PR7).

* :func:`use_input` — subscribe to keyboard events.
* :func:`use_app` — access the live :class:`Instance` (exit, flush).
* :func:`use_window_size` — read the current terminal size.
* :func:`use_interval` — periodically invoke a callback on a daemon thread.
* :func:`use_context` — read the nearest Provider's value (Phase 2 PR5).
* :func:`use_focus` — subscribe the calling component to a focus manager
  (Phase 2 PR6).
* :func:`use_focus_manager` — create a focus manager for the current
  subtree (Phase 2 PR6).
* :func:`use_box_metrics` — subscribe to an element's measured size
  (Phase 2 PR7). Pair with the ``ref`` prop on :func:`pyink.components.Box`
  and the imperative :func:`pyink.hooks.box_metrics.measure_element` API.

All hooks must be called from inside a function-component body mounted
via :func:`pyink.render.render` — they consult the active-Instance
ContextVar populated by the render pipeline. ``measure_element`` is the
lone exception: it is an imperative, side-effect-free read and may be
called from any thread at any time.
"""

from pyink.hooks._focus_runtime import (
    FocusHandle,
    FocusManager,
    FocusManagerHandle,
    NullFocusManager,
)
from pyink.hooks.app import AppHandle, use_app
from pyink.hooks.box_metrics import BoxMetrics, measure_element, use_box_metrics
from pyink.hooks.context import use_context
from pyink.hooks.focus import use_focus, use_focus_manager
from pyink.hooks.input import use_input
from pyink.hooks.interval import use_interval
from pyink.hooks.window_size import WindowSize, use_window_size

__all__ = [
    "AppHandle",
    "BoxMetrics",
    "FocusHandle",
    "FocusManager",
    "FocusManagerHandle",
    "NullFocusManager",
    "WindowSize",
    "measure_element",
    "use_app",
    "use_box_metrics",
    "use_context",
    "use_focus",
    "use_focus_manager",
    "use_input",
    "use_interval",
    "use_window_size",
]
