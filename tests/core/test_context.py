"""Tests for the Context system + ``use_context`` hook (Phase 2 PR5).

Covers:

* :func:`create_context` returns a :class:`Context` with a unique id and
  the supplied default.
* :func:`Provider` mounts a host ``"provider"`` element whose value is
  visible to descendants via :func:`use_context`.
* Default fallback when no Provider is on the stack.
* Nested Providers (inner overrides outer).
* Cross-component boundary propagation.
* Multiple Context instances are independent.
* Unmount pops the stack in LIFO order so the stack is empty afterwards.
* Provider value can be a :class:`Signal` (reactive read pattern).
* Mutating a Provider's value does not re-run descendant component
  bodies (PRD Decision 1 — signals model).
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable

import pytest

from ink import Box, Provider, Text, create_context, create_element, render, signal
from ink.core.context import Context, get_context_stack
from ink.core.context import Provider as ProviderFn
from ink.core.context import create_context as create_context_fn
from ink.core.element import Element
from ink.core.signal import Signal
from ink.hooks.context import use_context
from ink.render.instance import Instance

# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _FakeTTY(io.StringIO):
    """A minimally TTY-shaped buffer so the Terminal wrapper is happy."""

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _render_comp(make_component: Callable[[], Element]) -> Instance:
    """Mount ``make_component`` via the live render pipeline and return it."""
    inst = render(
        create_element(make_component),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=40,
        rows=5,
        exit_on_ctrl_c=False,
    )
    return inst


def _settle() -> None:
    """Let the render loop paint once before unmounting."""
    time.sleep(0.05)


# ---------------------------------------------------------------------------
# create_context
# ---------------------------------------------------------------------------


def test_create_context_returns_context_with_default() -> None:
    ctx = create_context("light")
    assert isinstance(ctx, Context)
    assert ctx.default == "light"
    assert isinstance(ctx.id, int)


def test_create_context_assigns_unique_ids() -> None:
    a = create_context(1)
    b = create_context(2)
    c = create_context(3)
    assert len({a.id, b.id, c.id}) == 3


def test_create_context_preserves_arbitrary_default_types() -> None:
    ctx_list = create_context([1, 2, 3])
    assert ctx_list.default == [1, 2, 3]
    ctx_none = create_context(None)
    assert ctx_none.default is None


# ---------------------------------------------------------------------------
# Provider + use_context basics
# ---------------------------------------------------------------------------


def test_use_context_outside_component_raises() -> None:
    Theme = create_context("light")
    with pytest.raises(RuntimeError, match="use_context"):
        use_context(Theme)


def test_use_context_reads_provider_value() -> None:
    Theme = create_context("light")
    seen: list[str] = []

    def Consumer() -> Element:
        seen.append(use_context(Theme))
        return Text("c")

    def App() -> Element:
        return Box(Provider(Theme, "dark", create_element(Consumer)))

    inst = _render_comp(App)
    _settle()
    inst.unmount()
    assert seen == ["dark"]


def test_use_context_falls_back_to_default_without_provider() -> None:
    Theme = create_context("light")
    seen: list[str] = []

    def Consumer() -> Element:
        seen.append(use_context(Theme))
        return Text("c")

    def App() -> Element:
        return Box(create_element(Consumer))

    inst = _render_comp(App)
    _settle()
    inst.unmount()
    assert seen == ["light"]


# ---------------------------------------------------------------------------
# Nested + cross-component
# ---------------------------------------------------------------------------


def test_nested_providers_inner_overrides_outer() -> None:
    Theme = create_context("light")
    seen: list[str] = []

    def Consumer() -> Element:
        seen.append(use_context(Theme))
        return Text("c")

    def App() -> Element:
        return Box(
            Provider(Theme, "outer",
                create_element(Consumer),  # reads "outer"
                Provider(Theme, "inner",
                    create_element(Consumer),  # reads "inner"
                ),
            ),
        )

    inst = _render_comp(App)
    _settle()
    inst.unmount()
    # Source order: outer Consumer first, then inner Consumer.
    assert seen == ["outer", "inner"]


def test_outer_value_visible_after_inner_provider_subtree() -> None:
    """A sibling Consumer outside the inner Provider still sees the outer one."""
    Theme = create_context("light")
    seen: list[str] = []

    def Consumer() -> Element:
        seen.append(use_context(Theme))
        return Text("c")

    def App() -> Element:
        return Box(
            Provider(Theme, "outer",
                Provider(Theme, "inner",
                    create_element(Consumer),  # inner
                ),
                # outer sibling — mounted after inner subtree pops itself
                create_element(Consumer),
            ),
        )

    inst = _render_comp(App)
    _settle()
    inst.unmount()
    # Both Consumers mounted once; inner first, outer second.
    assert seen == ["inner", "outer"]


def test_context_crosses_component_boundaries() -> None:
    """A Provider in component A is visible in deeply nested B/C/D."""
    Theme = create_context("light")
    seen: list[str] = []

    def D() -> Element:
        seen.append(use_context(Theme))
        return Text("d")

    def C() -> Element:
        return Box(create_element(D))

    def B() -> Element:
        return Box(create_element(C))

    def A() -> Element:
        return Box(Provider(Theme, "from-a", create_element(B)))

    inst = _render_comp(A)
    _settle()
    inst.unmount()
    assert seen == ["from-a"]


def test_multiple_contexts_are_independent() -> None:
    Theme = create_context("light")
    Locale = create_context("en")

    seen: dict[str, str] = {}

    def Consumer() -> Element:
        seen["theme"] = use_context(Theme)
        seen["locale"] = use_context(Locale)
        return Text("c")

    def App() -> Element:
        return Box(
            Provider(Theme, "dark",
                Provider(Locale, "ja",
                    create_element(Consumer),
                ),
            ),
        )

    inst = _render_comp(App)
    _settle()
    inst.unmount()
    assert seen == {"theme": "dark", "locale": "ja"}


# ---------------------------------------------------------------------------
# Unmount LIFO
# ---------------------------------------------------------------------------


def test_unmount_clears_stack_after_nested_providers() -> None:
    """After the whole tree mounts the stack is empty again — Provider
    entries are scoped to the mount traversal of their own subtree, so
    siblings and ancestors never observe leftover entries.
    """
    Theme = create_context("light")

    def Leaf() -> Element:
        return Text("x")

    def App() -> Element:
        return Box(
            Provider(Theme, "outer",
                Provider(Theme, "inner",
                    create_element(Leaf),
                ),
            ),
        )

    # Stack should start empty.
    assert get_context_stack() == []
    inst = _render_comp(App)
    _settle()
    # Each Provider pops itself once its subtree finishes mounting, so
    # by the time ``render`` returns the stack is empty again.
    assert get_context_stack() == []
    inst.unmount()
    assert get_context_stack() == []


def test_stack_is_transient_during_mount_only() -> None:
    """A Provider observed mid-mount is gone by the time control returns.

    This is the core invariant: the stack only carries entries for the
    *currently mounting* subtree chain. Once that subtree's host returns,
    its entry is popped — siblings and ancestors see a clean stack.
    """
    Theme = create_context("light")
    mid_mount_stack: list[int] = []

    def Consumer() -> Element:
        # We're inside the Provider's subtree mount — the entry should
        # be live right now.
        mid_mount_stack.append(len(get_context_stack()))
        return Text("c")

    def App() -> Element:
        return Box(
            Provider(Theme, "v",
                create_element(Consumer),
            ),
        )

    assert get_context_stack() == []
    inst = _render_comp(App)
    _settle()
    inst.unmount()
    # The Consumer saw exactly one entry while mounting.
    assert mid_mount_stack == [1]
    # And the stack is empty afterwards.
    assert get_context_stack() == []


def test_get_context_stack_returns_list() -> None:
    """Sanity: get_context_stack returns the live list, defaulting to []."""
    assert isinstance(get_context_stack(), list)


# ---------------------------------------------------------------------------
# Reactive value patterns
# ---------------------------------------------------------------------------


def test_provider_value_can_be_a_signal() -> None:
    """Storing a Signal in the Provider lets the consumer opt into
    reactivity by reading ``.value`` inside an effect or a callable leaf —
    the Context plumbing itself is mount-once."""
    # Default is a throwaway Signal — the Provider always supplies the
    # real one, but the Context needs *some* default of the right type.
    Theme: Context[Signal[str]] = create_context(signal(""))
    theme_signal = signal("dark")
    seen: list[str] = []

    def Consumer() -> Element:
        # Read the signal's current value at mount; that's enough to
        # confirm the Signal made it through the Provider.
        s = use_context(Theme)
        seen.append(s.value)
        return Text(lambda: s.value)

    def App() -> Element:
        return Box(Provider(Theme, theme_signal, create_element(Consumer)))

    inst = _render_comp(App)
    _settle()
    theme_signal.value = "darker"
    _settle()
    inst.unmount()
    # Component body ran once at mount; the signal write does not
    # re-run the body (only the callable Text leaf re-evaluates inside
    # the render loop). So ``seen`` captures exactly one mount-time read.
    assert seen == ["dark"]


def test_changing_provider_value_does_not_remount_consumer() -> None:
    """PRD Decision 1: prop/value changes do not re-run component bodies.

    A second mount of the same Context with a different value is a
    fresh Provider instance; the consumer mounted under the *first*
    Provider is unaffected by the second.
    """
    Theme = create_context("light")
    mount_count = {"n": 0}

    def Consumer() -> Element:
        mount_count["n"] += 1
        return Text("c")

    def App() -> Element:
        return Box(
            Provider(Theme, "first", create_element(Consumer)),
        )

    inst = _render_comp(App)
    _settle()
    inst.unmount()
    # Only one mount regardless of any external value churn — there is
    # no API to mutate a Provider's value in place, so this is the
    # closest assertion we can make.
    assert mount_count["n"] == 1


# ---------------------------------------------------------------------------
# Provider helper aliasing
# ---------------------------------------------------------------------------


def test_provider_module_export_matches_core_helper() -> None:
    """``ink.Provider`` is the same callable as ``ink.core.context.Provider``."""
    assert Provider is ProviderFn


def test_create_context_module_export_matches_core_helper() -> None:
    assert create_context is create_context_fn


# ---------------------------------------------------------------------------
# Reconciler integration: provider host instance carries its props
# ---------------------------------------------------------------------------


def test_provider_element_carries_reserved_props() -> None:
    Theme = create_context("light")
    el = Provider(Theme, "dark", Text("child"))
    assert el.type == "provider"
    assert el.props["_provider_ctx_id"] == Theme.id
    assert el.props["_provider_value"] == "dark"
    assert len(el.children) == 1


def test_use_context_with_typed_value_round_trip() -> None:
    """Use the TypeVar-typed API with an int context end-to-end."""
    Count = create_context(0)
    seen: list[int] = []

    def Consumer() -> Element:
        v: int = use_context(Count)
        seen.append(v)
        return Text(str(v))

    def App() -> Element:
        return Box(Provider(Count, 42, create_element(Consumer)))

    inst = _render_comp(App)
    _settle()
    inst.unmount()
    assert seen == [42]
