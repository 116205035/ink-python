"""Reconciler — turns an Element tree into an Instance tree (PR2).

Unlike a React reconciler, PyInk does not perform in-place diffing. The
signals model (PRD Decision 1) means component functions are invoked
exactly once on mount and never re-run because of prop changes; updates
propagate through signals directly to subscribers. Consequently the
reconciler exposes only two operations:

* :meth:`Reconciler.mount` — materialise an Element tree into Instances.
* :meth:`Reconciler.unmount` — recursively tear one down, invoking every
  registered effect cleanup in the process.

"Replacing" a tree is modelled as ``unmount`` the old + ``mount`` the new
at the call site (e.g. ``Instance.rerender`` in PR5).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ink.core.component import (
    ComponentInstance,
    HostInstance,
    Instance,
    TextLike,
    bind_current_component,
    restore_current_component,
)
from ink.core.context import pop_provider, push_provider
from ink.core.element import Element

__all__ = ["Reconciler"]


class Reconciler:
    """Mount/unmount Element trees into Instance trees.

    The reconciler is stateless across calls — every mount produces a fresh
    instance tree. ``Instance`` objects own their own mutable state.
    """

    def mount(
        self,
        element: Element,
        parent: Instance | None = None,
    ) -> Instance | None:
        """Materialise ``element`` and return the root Instance.

        Returns ``None`` when ``element`` represents nothing renderable
        (currently never — callers should filter ``None`` returns from
        function components before reaching here, but we defend anyway).
        """
        return self._mount_element(element, parent)

    def unmount(self, instance: Instance | None) -> None:
        """Recursively tear down ``instance``.

        Children are unmounted first (post-order). For ``ComponentInstance``
        every registered effect dispose is invoked in reverse registration
        order. Calling with ``None`` is a no-op.

        ``"provider"`` :class:`HostInstance` entries were already popped
        off the context stack at the end of their mount traversal (see
        :meth:`_mount_host`), so there is nothing extra to do here —
        ``use_context`` only reads during mount in the signals model.
        """
        if instance is None:
            return
        # Recurse into children first (post-order).
        children: list[Any] = getattr(instance, "children", [])
        for child in children:
            # Host text leaves are raw str/callable — skip them.
            if isinstance(child, (HostInstance, ComponentInstance)):
                self.unmount(child)
        instance.unmount()

    # -- Internals ---------------------------------------------------------

    def _mount_element(
        self,
        element: Element,
        parent: Instance | None,
    ) -> Instance | None:
        etype = element.type
        if isinstance(etype, str):
            return self._mount_host(element, parent)
        if callable(etype):
            return self._mount_component(element, parent)
        raise TypeError(  # pragma: no cover - guarded by create_element
            f"Unsupported element type {type(etype).__name__!r}"
        )

    def _mount_host(
        self,
        element: Element,
        parent: Instance | None,
    ) -> HostInstance:
        instance = HostInstance(element, parent)
        is_provider = element.type == "provider"
        if is_provider:
            # Push the Provider's value onto the context stack *before*
            # mounting children so descendant component bodies (which run
            # during ``_mount_element`` below) inherit the stack via the
            # ``ContextVar`` binding and see this value through
            # ``use_context``. We pop it again the moment this Provider's
            # subtree finishes mounting — that keeps the stack correct for
            # sibling subtrees, which otherwise would inherit a stale
            # entry. PyInk component bodies run exactly once at mount
            # (PRD Decision 1), so ``use_context`` only ever reads during
            # this traversal; we don't need the entry to live past it.
            push_provider(element.props["_provider_ctx_id"], element.props["_provider_value"])
        try:
            return self._mount_host_body(element, parent, instance)
        finally:
            if is_provider:
                pop_provider(element.props["_provider_ctx_id"])

    def _mount_host_body(
        self,
        element: Element,
        parent: Instance | None,
        instance: HostInstance,
    ) -> HostInstance:
        if element.type == "text":
            # Text leaves are kept raw — renderer resolves callables at
            # read time. ``str`` and ``Callable[[], str]`` only (enforced
            # by create_element's flatten step).
            instance.children = list(element.children)
        else:
            mounted: list[Instance] = []
            for child in element.children:
                if isinstance(child, Element):
                    sub = self._mount_element(child, instance)
                    if sub is not None:
                        mounted.append(sub)
                else:
                    # Non-text host with a bare str/callable child: per
                    # PRD Decision 8 only ``Text`` accepts string children.
                    # We still tolerate it (auto-wrap as a text leaf) so
                    # tests and PR2 pseudo-hosts work without forcing a
                    # ``Text`` wrapper everywhere.
                    sub = self._mount_element(
                        _make_text_element(child),
                        instance,
                    )
                    if sub is not None:
                        mounted.append(sub)
            instance.children = mounted
        instance.mount()
        return instance

    def _mount_component(
        self,
        element: Element,
        parent: Instance | None,
    ) -> ComponentInstance | None:
        instance = ComponentInstance(element, parent)
        token = bind_current_component(instance)
        rendered: Any
        try:
            fn: Callable[..., Any] = element.type  # type: ignore[assignment]
            rendered = fn(**element.props)
        finally:
            restore_current_component(token)

        children = self._mount_rendered(rendered, instance)
        instance.children = children
        instance.mount()
        return instance

    def _mount_rendered(
        self,
        rendered: Any,
        parent: ComponentInstance,
    ) -> list[Instance]:
        """Mount whatever a function component returned.

        Accepted shapes:

        * ``Element`` — single root.
        * ``tuple`` / ``list`` — fragment (multiple roots).
        * ``str`` / ``Callable[[], str]`` — auto-wrapped as a ``"text"`` host.
        * ``None`` — render nothing.
        """
        if rendered is None:
            return []
        if isinstance(rendered, Element):
            sub = self._mount_element(rendered, parent)
            return [sub] if sub is not None else []
        if isinstance(rendered, (tuple, list)):
            out: list[Instance] = []
            for item in rendered:
                out.extend(self._mount_rendered(item, parent))
            return out
        if isinstance(rendered, (str,)) or callable(rendered):
            sub = self._mount_element(_make_text_element(rendered), parent)
            return [sub] if sub is not None else []
        raise TypeError(
            f"Component returned unsupported type {type(rendered).__name__!r}"
        )


def _make_text_element(child: TextLike) -> Element:
    """Wrap a raw text leaf in a ``"text"`` host Element."""
    return Element(type="text", props={}, children=(child,))
