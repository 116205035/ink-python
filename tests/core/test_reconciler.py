"""Tests for Element / Component / Reconciler (PR2)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from pyink.core.component import ComponentInstance, HostInstance
from pyink.core.element import Element, create_element
from pyink.core.reconciler import Reconciler
from pyink.core.signal import effect, signal

# ---------------------------------------------------------------------------
# Element / create_element
# ---------------------------------------------------------------------------


def test_create_element_basic_type_props_children() -> None:
    child = create_element("text", "hi")
    el = create_element("box", child, padding=1, flexDirection="column")
    assert el.type == "box"
    assert el.props == {"padding": 1, "flexDirection": "column"}
    assert el.children == (child,)


def test_create_element_flattens_nested_tuples() -> None:
    a = create_element("text", "a")
    b = create_element("text", "b")
    c = create_element("text", "c")
    el = create_element("box", (a, b), [c])
    assert el.children == (a, b, c)


def test_create_element_filters_none_and_bools() -> None:
    keep = create_element("text", "keep")
    el = create_element("box", None, True, False, keep, None)
    assert el.children == (keep,)


def test_create_element_keeps_str_child() -> None:
    el = create_element("text", "hello")
    assert el.children == ("hello",)


def test_create_element_keeps_callable_child() -> None:
    def lazy() -> str:
        return "lazy"

    el = create_element("text", lazy)
    assert el.children == (lazy,)


def test_create_element_no_children_empty_tuple() -> None:
    el = create_element("box")
    assert el.children == ()
    assert el.props == {}


def test_create_element_rejects_unsupported_child_type() -> None:
    with pytest.raises(TypeError):
        create_element("box", 123)


def test_element_is_frozen_against_field_mutation() -> None:
    el = create_element("box")
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass
        el.type = "text"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Reconciler — host elements
# ---------------------------------------------------------------------------


def test_reconciler_mount_single_host() -> None:
    r = Reconciler()
    root = r.mount(create_element("text", "hi"))
    assert isinstance(root, HostInstance)
    assert root.element.type == "text"
    assert root.children == ["hi"]


def test_reconciler_mount_nested_hosts() -> None:
    r = Reconciler()
    root = r.mount(
        create_element(
            "box",
            create_element("text", "a"),
            create_element("text", "b"),
        )
    )
    assert isinstance(root, HostInstance)
    assert root.element.type == "box"
    assert len(root.children) == 2
    assert all(isinstance(c, HostInstance) for c in root.children)
    assert [c.children for c in root.children] == [["a"], ["b"]]
    # Parents link up.
    assert all(c.parent is root for c in root.children)


def test_reconciler_mount_text_callable_child_kept_raw() -> None:
    def lazy() -> str:
        return "lazy"

    r = Reconciler()
    root = r.mount(create_element("text", lazy))
    assert root is not None
    assert root.children == [lazy]


# ---------------------------------------------------------------------------
# Reconciler — function components
# ---------------------------------------------------------------------------


def test_reconciler_function_component_invoked_once() -> None:
    calls = {"n": 0}

    def Comp() -> Element:
        calls["n"] += 1
        return create_element("text", "body")

    r = Reconciler()
    root = r.mount(create_element(Comp))
    assert isinstance(root, ComponentInstance)
    assert calls["n"] == 1
    assert len(root.children) == 1
    inner = root.children[0]
    assert isinstance(inner, HostInstance)
    assert inner.element.type == "text"


def test_reconciler_function_component_props_passed() -> None:
    seen: dict[str, object] = {}

    def Comp(label: str, count: int = 0) -> Element:
        seen["label"] = label
        seen["count"] = count
        return create_element("text", str(label))

    r = Reconciler()
    r.mount(create_element(Comp, label="hi", count=3))
    assert seen == {"label": "hi", "count": 3}


def test_reconciler_function_component_returns_fragment() -> None:
    def Comp() -> tuple[Element, Element]:
        return (
            create_element("text", "a"),
            create_element("text", "b"),
        )

    r = Reconciler()
    root = r.mount(create_element(Comp))
    assert isinstance(root, ComponentInstance)
    assert len(root.children) == 2
    assert all(isinstance(c, HostInstance) for c in root.children)


def test_reconciler_function_component_returns_str_as_text() -> None:
    def Comp() -> str:
        return "raw"

    r = Reconciler()
    root = r.mount(create_element(Comp))
    assert isinstance(root, ComponentInstance)
    assert len(root.children) == 1
    inner = root.children[0]
    assert isinstance(inner, HostInstance)
    assert inner.element.type == "text"
    assert inner.children == ["raw"]


def test_reconciler_function_component_returns_none_renders_nothing() -> None:
    def Comp() -> None:
        return None

    r = Reconciler()
    root = r.mount(create_element(Comp))
    assert isinstance(root, ComponentInstance)
    assert root.children == []


def test_reconciler_nested_function_components() -> None:
    def Leaf() -> Element:
        return create_element("text", "leaf")

    def Wrapper() -> Element:
        return create_element("box", create_element(Leaf))

    r = Reconciler()
    root = r.mount(create_element(Wrapper))
    assert isinstance(root, ComponentInstance)
    assert len(root.children) == 1
    box = root.children[0]
    assert isinstance(box, HostInstance)
    assert box.element.type == "box"
    leaf_comp = box.children[0]
    assert isinstance(leaf_comp, ComponentInstance)
    text = leaf_comp.children[0]
    assert isinstance(text, HostInstance)
    assert text.children == ["leaf"]


# ---------------------------------------------------------------------------
# Reconciler — effects + unmount
# ---------------------------------------------------------------------------


def test_reconciler_mount_runs_effect_on_component() -> None:
    events: list[str] = []

    def Comp() -> Element:
        def setup() -> Callable[[], None]:
            events.append("mount")
            return lambda: events.append("cleanup")
        effect(setup)
        return create_element("text", "x")

    r = Reconciler()
    r.mount(create_element(Comp))
    assert events == ["mount"]


def test_reconciler_unmount_runs_effect_cleanup() -> None:
    events: list[str] = []

    def Comp() -> Element:
        def setup() -> Callable[[], None]:
            events.append("mount")
            return lambda: events.append("cleanup")
        effect(setup)
        return create_element("text", "x")

    r = Reconciler()
    root = r.mount(create_element(Comp))
    r.unmount(root)
    assert events == ["mount", "cleanup"]


def test_reconciler_unmount_runs_cleanups_in_reverse_order() -> None:
    events: list[str] = []

    def Comp() -> Element:
        def a() -> Callable[[], None]:
            events.append("a-mount")
            return lambda: events.append("a-cleanup")

        def b() -> Callable[[], None]:
            events.append("b-mount")
            return lambda: events.append("b-cleanup")

        effect(a)
        effect(b)
        return create_element("text", "x")

    r = Reconciler()
    root = r.mount(create_element(Comp))
    r.unmount(root)
    assert events == [
        "a-mount",
        "b-mount",
        "b-cleanup",
        "a-cleanup",
    ]


def test_reconciler_unmount_nested_components_cleanup_post_order() -> None:
    events: list[str] = []

    def Leaf() -> Element:
        def setup() -> Callable[[], None]:
            events.append("leaf-mount")
            return lambda: events.append("leaf-cleanup")

        effect(setup)
        return create_element("text", "leaf")

    def Root() -> Element:
        def setup() -> Callable[[], None]:
            events.append("root-mount")
            return lambda: events.append("root-cleanup")

        effect(setup)
        return create_element("box", create_element(Leaf))

    r = Reconciler()
    inst = r.mount(create_element(Root))
    r.unmount(inst)
    # Root mounted first, leaf mounted during root's body; on unmount leaf
    # cleanup runs before root cleanup.
    assert events == [
        "root-mount",
        "leaf-mount",
        "leaf-cleanup",
        "root-cleanup",
    ]


def test_reconciler_effect_auto_cleanup_does_not_double_dispose() -> None:
    disposed: list[str] = []

    def Comp() -> Element:
        def setup() -> Callable[[], None]:
            def cleanup() -> None:
                disposed.append("once")
            return cleanup

        dispose = effect(setup)

        def manual_cleanup() -> Callable[[], None]:
            # Manually dispose the first effect during mount — the auto-bind
            # should remove it from the instance's pending list so the
            # subsequent unmount does not invoke ``cleanup`` a second time.
            dispose()
            return lambda: None

        effect(manual_cleanup)
        return create_element("text", "x")

    r = Reconciler()
    root = r.mount(create_element(Comp))
    # Manual dispose fired the first effect's cleanup exactly once.
    assert disposed == ["once"]
    r.unmount(root)
    # Unmount must NOT re-run the manually-disposed cleanup.
    assert disposed == ["once"]


# ---------------------------------------------------------------------------
# Reconciler — effects scoped to the correct instance
# ---------------------------------------------------------------------------


def test_reconciler_effects_bind_to_correct_instance_when_nested() -> None:
    root_cleanups: list[str] = []
    leaf_cleanups: list[str] = []

    def Leaf() -> Element:
        def setup() -> Callable[[], None]:
            return lambda: leaf_cleanups.append("leaf")
        effect(setup)
        return create_element("text", "leaf")

    def Root() -> Element:
        def setup() -> Callable[[], None]:
            return lambda: root_cleanups.append("root")
        effect(setup)
        return create_element("box", create_element(Leaf))

    r = Reconciler()
    inst = r.mount(create_element(Root))
    # Sanity: each instance owns exactly its own effect.
    assert len(root_cleanups) == 0
    assert len(leaf_cleanups) == 0
    r.unmount(inst)
    assert root_cleanups == ["root"]
    assert leaf_cleanups == ["leaf"]


def test_reconciler_unmount_is_idempotent() -> None:
    events: list[str] = []

    def Comp() -> Element:
        def setup() -> Callable[[], None]:
            return lambda: events.append("cleanup")
        effect(setup)
        return create_element("text", "x")

    r = Reconciler()
    root = r.mount(create_element(Comp))
    r.unmount(root)
    r.unmount(root)  # second call must be a no-op
    assert events == ["cleanup"]


def test_reconciler_unmount_none_is_noop() -> None:
    r = Reconciler()
    r.unmount(None)  # should not raise


# ---------------------------------------------------------------------------
# Edge case: signals read inside mounted component effects
# ---------------------------------------------------------------------------


def test_reconciler_signal_inside_effect_subscribes_until_unmount() -> None:
    s = signal(0)
    seen: list[int] = []

    def Comp() -> Element:
        def setup() -> None:
            seen.append(s.value)
        effect(setup)
        return create_element("text", "x")

    r = Reconciler()
    root = r.mount(create_element(Comp))
    # Mount triggered one read.
    assert seen == [0]
    s.value = 1
    assert seen == [0, 1]
    r.unmount(root)
    # After unmount the effect's dispose has fired — no further updates.
    s.value = 2
    assert seen == [0, 1]


# ---------------------------------------------------------------------------
# Edge cases: cross-component cleanup, multi-component subscriptions,
# callable leaves, nested fragments, mount-time exceptions
# ---------------------------------------------------------------------------


def test_parent_unmount_propagates_to_child_component_effect_cleanup() -> None:
    """Component A mounts component B; B registers an effect. Unmounting A
    must trigger B's effect cleanup (the auto-bind routes B's dispose to
    B's instance, which A's unmount recurses into).
    """
    events: list[str] = []

    def Child() -> Element:
        def setup() -> Callable[[], None]:
            return lambda: events.append("child-cleanup")
        effect(setup)
        return create_element("text", "child")

    def Parent() -> Element:
        def setup() -> Callable[[], None]:
            return lambda: events.append("parent-cleanup")
        effect(setup)
        return create_element("box", create_element(Child))

    r = Reconciler()
    root = r.mount(create_element(Parent))
    r.unmount(root)
    # Child cleanup runs before parent cleanup (post-order traversal).
    assert events == ["child-cleanup", "parent-cleanup"]


def test_multiple_components_subscribe_to_same_signal() -> None:
    """Two components each register an effect subscribed to a shared signal;
    writes fan out to both, and unmounting one stops its updates without
    affecting the other.
    """
    s = signal(0)
    seen_a: list[int] = []
    seen_b: list[int] = []

    def CompA() -> Element:
        def setup() -> None:
            seen_a.append(s.value)
        effect(setup)
        return create_element("text", "a")

    def CompB() -> Element:
        def setup() -> None:
            seen_b.append(s.value)
        effect(setup)
        return create_element("text", "b")

    r = Reconciler()
    root_a = r.mount(create_element(CompA))
    root_b = r.mount(create_element(CompB))
    assert seen_a == [0]
    assert seen_b == [0]
    s.value = 1
    assert seen_a == [0, 1]
    assert seen_b == [0, 1]
    r.unmount(root_a)
    s.value = 2
    assert seen_a == [0, 1]  # A's effect disposed
    assert seen_b == [0, 1, 2]  # B still subscribed
    r.unmount(root_b)


def test_component_returning_callable_string_evaluated_lazily() -> None:
    """A component returning a ``Callable[[], str]`` must wrap it as a text
    leaf that the renderer resolves exactly once.
    """
    calls = {"n": 0}

    def Comp() -> Callable[[], str]:
        def lazy() -> str:
            calls["n"] += 1
            return f"v{calls['n']}"
        return lazy

    r = Reconciler()
    root = r.mount(create_element(Comp))
    assert isinstance(root, ComponentInstance)
    inner = root.children[0]
    assert isinstance(inner, HostInstance)
    assert inner.element.type == "text"
    assert len(inner.children) == 1
    # Stored raw; not yet evaluated.
    assert calls["n"] == 0
    leaf = inner.children[0]
    assert callable(leaf)
    assert leaf() == "v1"
    r.unmount(root)


def test_nested_fragment_unwraps_into_parent_children() -> None:
    """A tuple-of-tuples fragment returned by a component must be fully
    flattened so each leaf element becomes a direct child instance.
    """
    a = create_element("text", "a")
    b = create_element("text", "b")
    c = create_element("text", "c")
    d = create_element("text", "d")

    def Comp() -> object:
        return ((a, b), (c, d))  # nested fragment

    r = Reconciler()
    root = r.mount(create_element(Comp))
    assert isinstance(root, ComponentInstance)
    assert len(root.children) == 4
    text_leaves = [
        child.children[0] for child in root.children if isinstance(child, HostInstance)
    ]
    assert text_leaves == ["a", "b", "c", "d"]


def test_component_function_raising_during_mount_propagates_and_keeps_state_consistent() -> None:
    """If a component function raises during mount, the reconciler must
    propagate the exception and leave its internal state consistent so a
    subsequent mount on the same Reconciler works correctly (the failed
    instance is not left half-bound, and effects registered by the next
    component bind to the next component, not to a stale one).
    """
    events: list[str] = []

    def Boom() -> Element:
        raise RuntimeError("mount failed")

    def Good() -> Element:
        def setup() -> Callable[[], None]:
            events.append("good-mount")
            return lambda: events.append("good-cleanup")
        effect(setup)
        return create_element("text", "ok")

    r = Reconciler()
    with pytest.raises(RuntimeError, match="mount failed"):
        r.mount(create_element(Boom))

    # The reconciler survives the failed mount; a subsequent component's
    # effects still bind to it and run cleanup on unmount.
    root = r.mount(create_element(Good))
    assert events == ["good-mount"]
    r.unmount(root)
    assert events == ["good-mount", "good-cleanup"]
