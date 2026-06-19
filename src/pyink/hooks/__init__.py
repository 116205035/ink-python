"""PyInk hooks (PR6).

* :func:`use_input` — subscribe to keyboard events.
* :func:`use_app` — access the live :class:`Instance` (exit, flush).
* :func:`use_window_size` — read the current terminal size.

All three must be called from inside a function-component body mounted
via :func:`pyink.render.render` — they consult the active-Instance
ContextVar populated by the render pipeline.
"""

from pyink.hooks.app import AppHandle, use_app
from pyink.hooks.input import use_input
from pyink.hooks.window_size import WindowSize, use_window_size

__all__ = [
    "AppHandle",
    "WindowSize",
    "use_app",
    "use_input",
    "use_window_size",
]
