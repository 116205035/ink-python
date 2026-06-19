"""Integration tests for the bundled examples (PR8).

Each example is mounted against a ``io.StringIO`` stdout, allowed to
run briefly, then unmounted. The tests assert:

* the mount does not raise
* the rendered output contains the expected landmark string
* no full-screen clear (``\\x1b[2J``) ever appears (PRD Decision 3)

These tests do **not** drive interactive keystrokes — they verify the
mount + initial paint + unmount path for each example. End-to-end TTY
validation is left to manual runs (``python examples/<name>/<file>.py``).

Examples that rely on a real TTY for input (select-input, use-input,
use-focus) mount cleanly even with ``stdin=StringIO``: the
:class:`Terminal`'s reader thread simply never delivers any keys, so the
component sits in its initial state until unmount.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _load_example_module(rel_path: str, module_name: str) -> Any:
    """Import an example file by relative path under ``examples/``."""
    file_path = EXAMPLES_DIR / rel_path
    sys.path.insert(0, str(file_path.parent))
    try:
        if module_name in sys.modules:
            return sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        # Best-effort path cleanup; we keep the module cached so repeated
        # imports in the same test session are cheap.
        with contextlib.suppress(ValueError):
            sys.path.remove(str(file_path.parent))


def _run_example(
    build_tree: Any,
    *,
    columns: int = 60,
    rows: int = 8,
    run_seconds: float = 0.4,
) -> str:
    """Mount ``build_tree`` to a StringIO stdout, wait, unmount, return output.

    The returned string captures everything written up to (but not
    including) the unmount clear — unmount erases the live frame, so
    inspecting ``out.getvalue()`` after unmount would see only the
    blanked rows. We snapshot the buffer just before tearing down.
    """
    from pyink import render

    out = io.StringIO()
    inst = render(
        build_tree,
        stdout=out,
        stdin=io.StringIO(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    time.sleep(run_seconds)
    # Snapshot before unmount clears the frame.
    snapshot = out.getvalue()
    inst.unmount()
    return snapshot


# ---------------------------------------------------------------------------
# Each example
# ---------------------------------------------------------------------------


def test_counter_example_runs() -> None:
    mod = _load_example_module("counter/counter.py", "pyink_example_counter")
    out = _run_example(mod.Counter(), columns=40, rows=3, run_seconds=0.6)
    assert "tests passed" in out
    assert "\x1b[2J" not in out


def test_select_input_example_runs() -> None:
    mod = _load_example_module(
        "select-input/select_input.py", "pyink_example_select_input"
    )
    out = _run_example(mod.SelectInput(), columns=40, rows=10, run_seconds=0.3)
    assert "Pick a fruit" in out
    # All options should be rendered on mount.
    for label in ("Apple", "Banana", "Cherry", "Date", "Elderberry"):
        assert label in out
    assert "\x1b[2J" not in out


def test_borders_example_runs() -> None:
    mod = _load_example_module("borders/borders.py", "pyink_example_borders")
    out = _run_example(mod.Borders(), columns=60, rows=5, run_seconds=0.2)
    for label in ("single", "double", "round", "bold"):
        assert label in out
    assert "\x1b[2J" not in out


def test_static_example_runs() -> None:
    mod = _load_example_module("static/static.py", "pyink_example_static")
    # The static example pushes one item every 0.5 s — let it run long
    # enough to flush at least two items.
    out = _run_example(mod.App(), columns=50, rows=6, run_seconds=1.6)
    assert "Task 0 completed" in out
    assert "Completed:" in out
    assert "\x1b[2J" not in out


def test_use_input_example_runs() -> None:
    mod = _load_example_module(
        "use-input/use_input_demo.py", "pyink_example_use_input"
    )
    out = _run_example(mod.InputDemo(), columns=50, rows=6, run_seconds=0.2)
    assert "Press any key" in out
    assert "Input:" in out
    assert "Flags:" in out
    assert "\x1b[2J" not in out


def test_use_focus_example_runs() -> None:
    mod = _load_example_module(
        "use-focus/use_focus_demo.py", "pyink_example_use_focus"
    )
    out = _run_example(mod.FocusDemo(), columns=40, rows=12, run_seconds=0.2)
    assert "Tab switches focus" in out
    assert "Input A" in out
    assert "Input B" in out
    assert "\x1b[2J" not in out


# ---------------------------------------------------------------------------
# Lifecycle — every example must unmount cleanly even if signals keep
# writing from a background thread (counter, static).
# ---------------------------------------------------------------------------


def test_counter_unmount_is_idempotent_after_run() -> None:
    mod = _load_example_module("counter/counter.py", "pyink_example_counter_idem")
    from pyink import render

    out = io.StringIO()
    inst = render(
        mod.Counter(),
        stdout=out,
        stdin=io.StringIO(),
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    time.sleep(0.3)
    inst.unmount()
    inst.unmount()  # second unmount must not raise
    # Give the spawned timer thread a moment to observe ``running=False``
    # so it doesn't leak into subsequent tests.
    time.sleep(0.05)


# ---------------------------------------------------------------------------
# Sanity check — all six examples live where the test expects them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        "counter/counter.py",
        "select-input/select_input.py",
        "borders/borders.py",
        "static/static.py",
        "use-input/use_input_demo.py",
        "use-focus/use_focus_demo.py",
    ],
)
def test_example_file_exists(rel_path: str) -> None:
    assert (EXAMPLES_DIR / rel_path).is_file(), f"missing example: {rel_path}"


# ---------------------------------------------------------------------------
# Concurrent run — examples don't leak global state into each other.
# ---------------------------------------------------------------------------


def test_two_examples_back_to_back_in_one_process() -> None:
    """Mount borders, unmount, then mount use-focus in the same process."""
    borders = _load_example_module("borders/borders.py", "pyink_example_borders_2")
    focus = _load_example_module(
        "use-focus/use_focus_demo.py", "pyink_example_use_focus_2"
    )
    out1 = _run_example(borders.Borders(), columns=60, rows=5, run_seconds=0.1)
    assert "single" in out1
    out2 = _run_example(focus.FocusDemo(), columns=40, rows=12, run_seconds=0.1)
    assert "Input A" in out2
    assert "Input B" in out2


# ---------------------------------------------------------------------------
# Threaded wait_until_exit works for the counter example.
# ---------------------------------------------------------------------------


def test_counter_wait_until_exit_returns_on_unmount() -> None:
    mod = _load_example_module(
        "counter/counter.py", "pyink_example_counter_wait"
    )
    from pyink import render

    out = io.StringIO()
    inst = render(
        mod.Counter(),
        stdout=out,
        stdin=io.StringIO(),
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )

    captured: dict[str, str] = {}

    def worker() -> None:
        time.sleep(0.2)
        # Snapshot before unmount clears the frame.
        captured["out"] = out.getvalue()
        inst.unmount()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    inst.wait_until_exit()
    t.join(timeout=1.0)
    assert not t.is_alive()
    assert "tests passed" in captured["out"]
