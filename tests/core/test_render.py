"""Tests for render_to_string + Scheduler (PR2)."""

from __future__ import annotations

from collections.abc import Callable

from pyink.core.element import create_element
from pyink.core.scheduler import Scheduler
from pyink.core.signal import signal
from pyink.render import render_to_string

# ---------------------------------------------------------------------------
# render_to_string
# ---------------------------------------------------------------------------


def test_render_single_text() -> None:
    assert render_to_string(create_element("text", "hello")) == "hello"


def test_render_nested_hosts_concatenate() -> None:
    tree = create_element(
        "box",
        create_element("text", "a"),
        create_element("text", "b"),
    )
    assert render_to_string(tree) == "ab"


def test_render_callable_child_evaluated_once() -> None:
    calls = {"n": 0}

    def lazy() -> str:
        calls["n"] += 1
        return "lazy"

    assert render_to_string(create_element("text", lazy)) == "lazy"
    assert calls["n"] == 1


def test_render_function_component_output() -> None:
    def Greeting(name: str) -> object:
        return create_element("text", f"hi {name}")

    assert render_to_string(create_element(Greeting, name="world")) == "hi world"


def test_render_function_component_returning_str() -> None:
    def Plain() -> str:
        return "plain"

    assert render_to_string(create_element(Plain)) == "plain"


def test_render_fragment_multiple_roots() -> None:
    def Header() -> tuple[object, object]:
        return (
            create_element("text", "title"),
            create_element("text", "subtitle"),
        )

    assert render_to_string(create_element(Header)) == "titlesubtitle"


def test_render_nested_function_components() -> None:
    def Leaf() -> object:
        return create_element("text", "leaf")

    def Root() -> object:
        return create_element("box", create_element(Leaf))

    assert render_to_string(create_element(Root)) == "leaf"


def test_render_callable_returning_none_yields_empty() -> None:
    def f() -> None:
        return None

    assert render_to_string(create_element("text", f)) == ""


def test_render_columns_option_does_not_break_output() -> None:
    # PR2 only records columns; passing it must not change the snapshot.
    assert render_to_string(create_element("text", "x"), columns=42) == "x"


def test_render_signal_inside_callable_reads_snapshot_only() -> None:
    s = signal(5)

    def lazy() -> str:
        return f"v={s.value}"

    out = render_to_string(create_element("text", lazy))
    assert out == "v=5"
    # Mutating after render does not retroactively change the snapshot.
    s.value = 99
    assert out == "v=5"


def test_render_unmounts_tree_after_snapshot() -> None:
    cleanups: list[str] = []

    def Comp() -> object:
        # An effect with a cleanup; render_to_string must unmount it.
        from pyink.core.signal import effect

        def setup() -> Callable[[], None]:
            return lambda: cleanups.append("done")
        effect(setup)
        return create_element("text", "x")

    render_to_string(create_element(Comp))
    assert cleanups == ["done"]


def test_render_empty_children() -> None:
    assert render_to_string(create_element("box")) == ""


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def test_scheduler_flush_runs_in_order() -> None:
    s = Scheduler()
    log: list[int] = []
    s.schedule(lambda: log.append(1))
    s.schedule(lambda: log.append(2))
    s.schedule(lambda: log.append(3))
    assert log == []
    s.flush()
    assert log == [1, 2, 3]
    assert len(s) == 0


def test_scheduler_dedupes_by_identity() -> None:
    s = Scheduler()
    log: list[int] = []

    def cb() -> None:
        log.append(1)

    s.schedule(cb)
    s.schedule(cb)
    assert len(s) == 1
    s.flush()
    assert log == [1]


def test_scheduler_flush_picks_up_callbacks_scheduled_during_flush() -> None:
    s = Scheduler()
    log: list[str] = []

    def first() -> None:
        log.append("first")
        s.schedule(lambda: log.append("second"))

    s.schedule(first)
    s.flush()
    assert log == ["first", "second"]


def test_scheduler_callback_exception_does_not_block_others() -> None:
    s = Scheduler()
    log: list[int] = []

    def boom() -> None:
        raise RuntimeError("boom")

    s.schedule(boom)
    s.schedule(lambda: log.append(1))
    s.flush()
    assert log == [1]
