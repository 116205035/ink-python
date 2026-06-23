"""``use_window_size`` — subscribe to terminal resize events (PR6).

Returns a :class:`WindowSize` snapshot. A resize triggers a re-render
of the active :class:`Instance` (the Instance already re-paints on
resize via its own ``on_resize`` subscription; this hook merely returns
the current size so the component reads fresh values during the
re-render).

Mirrors ink's ``useWindowSize`` hook.
"""

from __future__ import annotations

from dataclasses import dataclass

from ink.hooks._runtime import _get_current_instance

__all__ = ["WindowSize", "use_window_size"]


@dataclass(frozen=True, slots=True)
class WindowSize:
    """Terminal viewport size, in character cells."""

    columns: int
    rows: int


def use_window_size() -> WindowSize:
    """Return the current terminal size.

    The Instance's existing resize subscription triggers a re-render on
    resize; on re-render the component body re-reads the (now updated)
    terminal columns, so reading the live size here is sufficient — no
    extra subscription is needed at the hook level.
    """
    inst = _get_current_instance()
    if inst is None:
        # Fall back to a ``shutil.get_terminal_size`` snapshot so
        # ``use_window_size`` is also usable outside render() (e.g. in
        # tests that exercise layout).
        import shutil

        ts = shutil.get_terminal_size()
        return WindowSize(columns=max(1, ts.columns), rows=max(1, ts.lines))
    # Prefer the option-driven size if the caller pinned it; otherwise
    # fall back to whatever the live terminal reports.
    options = getattr(inst, "options", None)
    columns_override = getattr(options, "columns", None)
    rows_override = getattr(options, "rows", None)
    columns = (
        int(columns_override)
        if isinstance(columns_override, int) and columns_override > 0
        else inst.columns
    )
    rows = (
        int(rows_override)
        if isinstance(rows_override, int) and rows_override > 0
        else inst.rows
    )
    return WindowSize(columns=columns, rows=rows)
