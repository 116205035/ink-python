"""``VirtualList`` — windowed / virtualised list (Phase 5 PR2).

Mirrors the shape of ``react-window`` / ``ink``'s ad-hoc
``VirtualMessageList``: given a large list of items, only the slice that
fits inside the viewport (plus a small ``overscan`` margin) is actually
rendered. Scrolling is driven by an internal ``scroll_offset`` signal
that callers can read/write via ``on_scroll`` or by passing their own
``scroll_signal``.

Design (per PRD Decision 2 + Phase 5 PR2 notes):

* ``VirtualList`` is a factory returning an :class:`Element` whose
  ``type`` is the :func:`_VirtualListImpl` function component. The
  factory itself never runs hooks; only the wrapped function does,
  when the reconciler mounts it. Same shape as
  :func:`pyink.externals.SelectInput` / :func:`pyink.externals.TextInput`
  so callers can freely nest ``Box(VirtualList(...), Text(...))``
  outside of a render context.
* The component owns a ``scroll_offset: Signal[int]`` (or reuses one
  passed by the caller via ``scroll_signal``) that records which item
  index is currently at the top of the viewport. The visible range is

      [scroll_offset - overscan, scroll_offset + viewport_height + overscan)

  clamped to ``[0, len(items))``. A callable child assembles only that
  slice's text payload at paint time, so the work done per frame is
  proportional to ``viewport_height + 2*overscan`` — not to
  ``len(items)``.
* The fixed-height fast path (``item_height=int``) renders each visible
  item via :func:`render_item`, flattens the returned :class:`Element`
  to text through :func:`pyink.render.render_to_string`, and joins the
  per-item chunks with newlines into a single multi-line payload. The
  wrapping :func:`Box` height is pinned to ``viewport_height`` so the
  layout grants the viewport exactly; :func:`Text`'s public
  ``scroll_offset`` prop — driven by the same signal — then slices the
  payload so only the in-viewport rows are visible. Together this gives
  O(``viewport_height + overscan``) layout cost per paint, independent
  of ``len(items)``. Dynamic-height mode (``item_height=None``) is not
  implemented in PR2 — the factory raises :class:`NotImplementedError`
  so callers know it is intentionally unavailable rather than silently
  falling back to a wrong heuristic.
* ``on_scroll`` fires whenever the internal offset changes (including
  when the caller writes ``scroll_signal.value`` from outside), letting
  the host app react to scrolling (drive a status line, lazy-load more
  data, etc.). ``initial_scroll`` seeds the offset at mount;
  out-of-range values are clamped to the valid
  ``[0, max(0, n - viewport_height)]`` window.
* ``key`` is accepted for forward-compatibility with a future
  reconciliation pass; PR2's reconciler does not consult it, so the
  argument is validated (must be callable) but otherwise stored.

Rendering details:

* The wrapping :func:`Box` carries ``flexDirection="column"`` and a
  pinned ``height=viewport_height`` so the surrounding layout grants
  the viewport exactly (the ``layout-hardening`` clip-to-box then
  guarantees content outside the viewport is never painted).
* Each visible item is rendered through ``render_item(item, index)``
  and the returned :class:`Element` is independently rendered via
  :func:`render_to_string` to a single-line text chunk (multiline item
  payloads collapse to as many physical rows as the inner Element
  produces — callers wanting strict one-row items should ensure their
  ``render_item`` returns a single-line :func:`Text`).
* The Text ``scroll_offset`` is set to ``offset - start`` (where
  ``start = max(0, offset - overscan)``) so that when the overscan
  prepends rows above the viewport, the painter's slice still begins
  exactly at the viewport's top row. This keeps the overscan rows
  available (pre-mounted, as it were, in the joined payload) so a
  small scroll up does not need to re-render the whole slice — only
  the scroll_offset signal moves.

Out of scope (PR2):

* Dynamic-height measurement (``item_height=None``). PR2 raises
  :class:`NotImplementedError`; a future PR can add cumulative-offset
  bookkeeping + :func:`pyink.hooks.measure_element` integration.
* Keyboard bindings. ``VirtualList`` does not call
  :func:`pyink.hooks.use_input`; keystroke dispatch is owned by the
  surrounding focus manager (PRD Decision 3). Callers drive scrolling
  by writing ``scroll_signal.value`` or by wrapping
  ``on_scroll``/``scroll_signal`` in their own hook.
* Nested virtualisation (a ``VirtualList`` inside another
  ``VirtualList``). Extreme scenario — left for a later task.
* Per-item sub-element reconciliation. The ``render_item`` result is
  rendered through :func:`render_to_string` and joined into a single
  multi-line text payload, so each item is mounted independently on
  every paint. A future incremental-rendering pass (PRD Decision 4)
  could cache subtrees; PR2 ships without it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element, create_element
from pyink.core.signal import Signal, effect, signal
from pyink.hooks._runtime import _get_current_instance
from pyink.render import render_to_string

__all__ = ["VirtualList"]


def _resolve_items(source: Any) -> list[Any]:
    """Resolve ``items`` from a plain list / :class:`Signal` / callable.

    Order matters: ``Signal`` objects are callable (``.value`` is a
    ``@property``), so the ``isinstance(..., Signal)`` branch must run
    before the ``callable(...)`` branch. Mirrors the ``is_active``
    resolution in :mod:`pyink.externals.text_input`.
    """
    if isinstance(source, Signal):
        return list(source.value)
    if callable(source):
        return list(source())
    return list(source)


def _clamp_offset(offset: int, n: int, viewport_height: int) -> int:
    """Clamp ``offset`` to ``[0, max(0, n - viewport_height)]``.

    When the list is shorter than the viewport (``n <= viewport_height``)
    the only valid offset is ``0`` — scrolling past the first row would
    leave empty space at the bottom, which the pinned-height box would
    then expose as blank rows.
    """
    if n <= viewport_height:
        return 0
    return max(0, min(offset, n - viewport_height))


def _VirtualListImpl(**props: Any) -> Element:
    """Function component body — runs inside the reconciler render context.

    Owns the ``scroll_offset`` signal (or borrows the caller-provided
    one), registers the ``on_scroll`` side-effect, and returns a column
    :func:`Box` wrapping a single :func:`Text` whose callable child
    assembles the visible slice.
    """
    items_source: Any = props["items"]
    render_item: Callable[[Any, int], Element] = props["render_item"]
    viewport_height: int = props["viewport_height"]
    item_height: int | None = props["item_height"]
    overscan: int = props["overscan"]
    on_scroll: Callable[[int], None] | None = props["on_scroll"]
    key_fn: Callable[[Any, int], str] | None = props["key"]
    initial_scroll: int = props["initial_scroll"]
    scroll_signal: Signal[int] | None = props["scroll_signal"]
    box_props: dict[str, Any] = props["box_props"]

    # Dynamic-height mode is intentionally unimplemented in PR2 — raise
    # rather than silently fall back to a wrong heuristic. See module
    # docstring "Out of scope".
    if item_height is None:
        raise NotImplementedError(
            "VirtualList dynamic-height mode (item_height=None) is not "
            "implemented in Phase 5 PR2; pass item_height=<int> for the "
            "fixed-height fast path."
        )

    # ``render_item`` is invoked once per visible row on every paint, so
    # the inner Element tree is rebuilt from scratch each time. We need
    # the active render Instance to get the current terminal width —
    # without it, per-item layout falls back to the default 80 columns
    # and long lines would wrap differently from the surrounding frame.
    instance = _get_current_instance()
    columns = getattr(instance, "columns", 0) if instance is not None else 0
    if not isinstance(columns, int) or columns < 1:
        columns = 80

    items: list[Any] = _resolve_items(items_source)
    n = len(items)

    # Borrow the caller-provided scroll signal when available; otherwise
    # create an internal one. Either way, the value is clamped to the
    # valid range for the current item count so a stale external signal
    # (e.g. after the list shrank) cannot leave the viewport pointing
    # past the end.
    #
    # ``initial_scroll`` only applies to the *internal* signal — when
    # the caller provides their own ``scroll_signal`` we trust whatever
    # value they initialised it with. Overriding an external signal
    # here would silently reset it on every mount, which breaks the
    # "external owns the scroll state" contract.
    if scroll_signal is not None:
        offset_signal = scroll_signal
        offset_signal.value = _clamp_offset(
            offset_signal.value, n, viewport_height
        )
    else:
        offset_signal = signal(_clamp_offset(initial_scroll, n, viewport_height))

    # Re-clamp whenever the offset changes from outside (e.g. caller
    # writes ``scroll_signal.value = 9999``) OR whenever the items
    # source shrinks (e.g. a ``Signal[list]`` mutation that drops
    # elements past the current offset). The clamp write is a no-op
    # when the value is already in range, so this does not create a
    # feedback loop. Subscribed via ``effect`` with explicit deps so
    # the effect re-runs on every relevant write.
    #
    # The deps list intentionally includes ``items_source`` when it is
    # itself a Signal — that way an items-shrink re-runs the clamp and
    # pulls the offset back into the valid range even if the offset
    # itself was not touched. A plain list / callable source has no
    # signal to subscribe to; in those cases the deps list collapses to
    # ``[offset_signal]`` and the clamp only fires on offset writes.
    clamp_deps: list[Signal[object] | Signal[int]] = [offset_signal]
    if isinstance(items_source, Signal):
        clamp_deps.append(items_source)

    def _clamp_on_change() -> None:
        offset_signal.value = _clamp_offset(
            offset_signal.value,
            len(_resolve_items(items_source)),
            viewport_height,
        )

    effect(_clamp_on_change, deps=clamp_deps)

    # Fire ``on_scroll`` whenever the offset settles. The effect deps
    # capture ``offset_signal`` so the callback fires exactly once per
    # scroll change (not once per items re-render). Subscribed alongside
    # the clamp effect so both observe the same notification epoch.
    if on_scroll is not None:
        captured_on_scroll = on_scroll

        def _notify_scroll() -> None:
            captured_on_scroll(offset_signal.value)

        effect(_notify_scroll, deps=[offset_signal])

    # ``key_fn`` is currently unused by the reconciler but accepted for
    # forward-compatibility with a future reconciliation pass. Reference
    # it here so static analysers don't flag it as dead code.
    _ = key_fn

    def render_visible() -> str:
        """Build the visible slice's joined text payload at paint time.

        Reads the offset signal (which subscribes this callable to
        offset writes) plus the items source (which subscribes it to
        items writes when the source is itself a Signal). The returned
        string carries one row per visible item, ``\\n``-joined; the
        surrounding Text's ``scroll_offset`` then slices this payload
        to the viewport's top row.
        """
        offset = offset_signal.value
        current_items = _resolve_items(items_source)
        cur_n = len(current_items)
        # Re-clamp defensively in case items shrank between the
        # ``effect`` clamp and this paint (the ``effect`` runs in phase
        # 2 of the notification flush; the paint may race ahead).
        offset = _clamp_offset(offset, cur_n, viewport_height)
        start = max(0, offset - overscan)
        end = min(cur_n, offset + viewport_height + overscan)

        chunks: list[str] = []
        for i in range(start, end):
            element = render_item(current_items[i], i)
            chunks.append(render_to_string(element, columns=columns))

        # The Text ``scroll_offset`` is expressed relative to the
        # joined payload. We rendered ``[start, end)`` so the viewport's
        # first row sits at ``offset - start`` inside the payload —
        # set the signal so the painter's slice lines up.
        text_offset = offset - start
        offset_signal_value_delta = text_offset

        # Use a private attribute on the closure to expose the
        # text-relative offset to the surrounding Text element. We
        # cannot mutate ``offset_signal`` (it tracks the absolute item
        # index); instead we keep a second signal that the Text reads.
        _text_offset_signal.value = offset_signal_value_delta

        return "\n".join(chunks)

    # Separate signal tracking the Text's payload-relative offset.
    # ``offset_signal`` stays in item-index coordinates so callers'
    # ``on_scroll`` / external ``scroll_signal`` see item indices; the
    # Text painter only cares about "how many rows into the payload
    # does the viewport start", which is what this signal holds.
    _text_offset_signal: Signal[int] = signal(0)

    return Box(
        Text(render_visible, scroll_offset=_text_offset_signal),
        flexDirection="column",
        height=viewport_height,
        **box_props,
    )


def VirtualList(
    items: list[Any] | Signal[list[Any]] | Callable[[], list[Any]],
    *,
    render_item: Callable[[Any, int], Element],
    viewport_height: int,
    item_height: int | None = None,
    overscan: int = 3,
    on_scroll: Callable[[int], None] | None = None,
    key: Callable[[Any, int], str] | None = None,
    initial_scroll: int = 0,
    scroll_signal: Signal[int] | None = None,
    **box_props: Any,
) -> Element:
    """Create a virtualised / windowed list.

    Only the items that fall inside the visible window
    ``[scroll_offset - overscan, scroll_offset + viewport_height + overscan)``
    are rendered at any time; the rest of ``items`` is skipped entirely.
    This keeps the per-paint cost flat in ``len(items)`` — a 1000-row
    list renders as cheaply as a 20-row one.

    Parameters
    ----------
    items:
        The backing list. Accepts three shapes so callers can plug
        reactive sources directly:

        * ``list[T]`` — static snapshot captured at mount. Mutations to
          the *same* list object after mount are visible on the next
          paint (the slice is re-resolved through the callable child).
        * :class:`Signal[list[T]]`` — read at paint time via ``.value``,
          so writing the signal triggers a re-render with the new
          contents (append / replace / truncate all work).
        * ``Callable[[], list[T]]`` — invoked at paint time. Useful for
          bridging non-signal reactive sources or computing a derived
          view on the fly.
    render_item:
        Called as ``render_item(item, index)`` for each visible row.
        Must return an :class:`Element`. The returned element is
        rendered through :func:`pyink.render.render_to_string` with the
        current terminal width and the resulting text is placed on its
        own row in the viewport. The ``index`` is the item's absolute
        position in the backing list (not its position in the visible
        slice), so stable keys / row colours stay anchored to the item
        rather than the viewport.
    viewport_height:
        Number of rows the viewport shows at once. The wrapping
        :func:`Box`'s height is pinned to this value so the layout
        grants exactly ``viewport_height`` rows; the surrounding
        ``layout-hardening`` clip guarantees anything outside the
        viewport is never painted. Must be ``>= 1``.
    item_height:
        Per-row height in terminal rows. ``int`` selects the fixed-
        height fast path (the only path implemented in PR2). ``None``
        would select dynamic measurement mode, which is **not**
        implemented in PR2 — passing ``None`` raises
        :class:`NotImplementedError` from inside the component body so
        the caller discovers the limitation at mount time.
    overscan:
        Extra rows rendered above and below the viewport so small
        scrolls do not flash empty cells while the next paint catches
        up. Defaults to ``3``. ``0`` renders exactly the viewport; very
        large values degenerate to "render everything" and lose the
        virtualisation win.
    on_scroll:
        Called with the new top-of-viewport item index whenever the
        internal ``scroll_offset`` changes (including changes driven
        by the caller writing ``scroll_signal.value`` from outside).
        ``None`` (default) disables the callback.
    key:
        Stable-key function called as ``key(item, index)``. Accepted
        for forward-compatibility with a future reconciliation pass;
        PR2's reconciler does not consult it. ``None`` (default) keeps
        the default identity-based reconciliation.
    initial_scroll:
        Initial top-of-viewport index on mount. Clamped to the valid
        range ``[0, max(0, len(items) - viewport_height)]`` so
        out-of-range values silently settle on the nearest endpoint
        rather than raising. Defaults to ``0``.
    scroll_signal:
        External :class:`Signal[int]` the component should read and
        write to drive scrolling. ``None`` (default) means the
        component owns an internal signal — callers then steer
        scrolling through :attr:`on_scroll` or by re-mounting with a
        new ``initial_scroll``. Passing a signal is the canonical way
        to wire keyboard shortcuts (PageUp / PageDown / Home / End)
        from the surrounding focus manager: write
        ``scroll_signal.value`` from the handler and the viewport
        follows. The signal's value is clamped to the valid range on
        every change.
    **box_props:
        Forwarded to the wrapping :func:`Box` (``padding``,
        ``borderStyle``, ``width``, …). ``height`` is always pinned to
        ``viewport_height`` — passing ``height`` here is an error that
        raises :class:`ValueError` from the factory.

    Returns
    -------
    Element
        An element whose ``type`` is the :func:`_VirtualListImpl`
        function component. The factory itself never runs hooks — the
        reconciler mounts the function, which is what makes
        ``Box(VirtualList(...), Text(...))`` safe to call from outside
        a render context.

    Raises
    ------
    ValueError
        ``viewport_height`` is less than ``1``, ``overscan`` is
        negative, or ``box_props`` contains ``height`` (which would
        conflict with the viewport-pinned height).
    NotImplementedError
        ``item_height is None`` (dynamic-height mode is not
        implemented in PR2). Raised from inside the component body at
        mount time rather than from the factory so the error surfaces
        in the render context where the caller's surrounding
        error-handling lives.

    Usage
    -----
    ::

        items_sig = signal([f"Item {i}" for i in range(1000)])
        scroll_sig = signal(0)

        def App():
            return Box(
                VirtualList(
                    items_sig,
                    render_item=lambda item, i: Text(item),
                    viewport_height=10,
                    item_height=1,
                    scroll_signal=scroll_sig,
                ),
                padding=1,
                borderStyle="round",
            )

        # Drive scrolling from outside:
        #   scroll_sig.value = 50  # viewport jumps to item 50
    """
    if viewport_height < 1:
        raise ValueError(
            f"viewport_height must be >= 1, got {viewport_height}"
        )
    if overscan < 0:
        raise ValueError(f"overscan must be >= 0, got {overscan}")
    if "height" in box_props:
        raise ValueError(
            "VirtualList pins height=viewport_height internally; "
            "pass viewport_height=<int> instead of height=<int>."
        )

    return create_element(
        _VirtualListImpl,
        items=items,
        render_item=render_item,
        viewport_height=viewport_height,
        item_height=item_height,
        overscan=overscan,
        on_scroll=on_scroll,
        key=key,
        initial_scroll=initial_scroll,
        scroll_signal=scroll_signal,
        box_props=box_props,
    )
