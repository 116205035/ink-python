"""Sync test renderer — flattens an Element tree to a string (PR2).

This module deliberately does **not** do layout, ANSI styling or frame
diffing (those land in PR3/PR4/PR5). It mounts an Element tree, walks the
resulting Instance tree in document order, and concatenates every text
leaf — resolving callables exactly once. Subscriptions are *not*
established: ``render_to_string`` is a one-shot snapshot used by tests
and CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyink.core.component import ComponentInstance, HostInstance, Instance
from pyink.core.element import Element
from pyink.core.reconciler import Reconciler

__all__ = ["RenderOptions", "render_to_string"]


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """Options bag for :func:`render_to_string`.

    PR2 only records ``columns`` — it is not used to wrap text. PR3's flex
    engine will consume this value when measuring host nodes.
    """

    columns: int = 80


def render_to_string(
    tree: Element,
    *,
    columns: int = 80,
) -> str:
    """Render ``tree`` to a plain string snapshot.

    The tree is mounted, walked once, then unmounted — the function does
    not keep the tree alive. Callable leaves are evaluated synchronously
    a single time; signal reads inside them return current snapshot
    values but do **not** establish subscriptions.
    """
    options = RenderOptions(columns=columns)
    reconciler = Reconciler()
    root = reconciler.mount(tree, parent=None)
    try:
        return _render_instance(root, options)
    finally:
        reconciler.unmount(root)


def _render_instance(instance: Instance | None, options: RenderOptions) -> str:
    if instance is None:
        return ""
    if isinstance(instance, HostInstance):
        return _render_host(instance, options)
    if isinstance(instance, ComponentInstance):
        # ComponentInstance.children already contains the mounted tree the
        # component function returned — just recurse.
        return _concat_children(instance.children, options)
    # Unknown instance kind — defensive, should not happen in PR2.
    return ""


def _render_host(instance: HostInstance, options: RenderOptions) -> str:
    if instance.element.type == "text":
        parts: list[str] = []
        for leaf in instance.children:
            parts.append(_resolve_leaf(leaf))
        return "".join(parts)
    # Non-text host: concatenate rendered children. PR2 has no layout, so
    # no separator / wrapping is applied. PR3 will replace this branch
    # with a flex measurement pass.
    return _concat_children(instance.children, options)


def _concat_children(children: list[Any], options: RenderOptions) -> str:
    parts: list[str] = []
    for child in children:
        if isinstance(child, (HostInstance, ComponentInstance)):
            parts.append(_render_instance(child, options))
        else:
            # Defensive: a non-text host that ended up with raw leaves
            # (e.g. via the auto-wrap fallback in the reconciler).
            parts.append(_resolve_leaf(child))
    return "".join(parts)


def _resolve_leaf(leaf: Any) -> str:
    if isinstance(leaf, str):
        return leaf
    if callable(leaf):
        result = leaf()
        if result is None:
            return ""
        return str(result)
    # Element leaked into a text position — render it as an empty leaf
    # rather than raising so a malformed tree doesn't crash tests.
    return ""
