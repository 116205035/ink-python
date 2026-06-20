"""``Link`` — clickable OSC 8 terminal hyperlink (Phase 2 PR3).

Mirrors :mod:`ink-link`, which delegates to :mod:`terminal-link` to wrap
the rendered text of its children in an OSC 8 hyperlink sequence.

OSC 8 hyperlink format (per the `Egmont Kobé spec
<https://gist.github.com/egmontkob/eb114294efbcd5adb1944c9f3cb5feda>`_):

.. code-block:: text

    \\x1b]8;;URL\\x1b\\\\TEXT\\x1b]8;;\\x1b\\\\

where:

* ``\\x1b]8;;URL\\x1b\\\\`` opens the hyperlink (the empty field between
  the two ``;`` is a custom ``id`` parameter ink-link leaves blank).
* ``TEXT`` is the displayed, possibly styled, content.
* ``\\x1b]8;;\\x1b\\\\`` closes the hyperlink so subsequent output is not
  itself turned into a link.

Terminals that don't understand OSC 8 just strip the escape sequences and
show ``TEXT`` plain — we don't ship a separate "print the URL after the
text" fallback because every modern terminal PyInk targets supports OSC 8.

Design (per PRD PR3 scope):

* ``Link`` is a function component. On mount it builds a throwaway host
  tree containing the children, lays it out at the active Instance's
  current width (or 80 columns when used via the synchronous test
  renderer), renders it to a string, and wraps that string in the OSC 8
  sequence. The result is emitted as a single ``text`` host leaf carrying
  any forwarded style props (``color`` / ``bold`` / ``underline`` / …).
* This is the same pattern :func:`pyink.components.Transform` uses — the
  function component body runs exactly once on mount, so the wrapped text
  is captured at mount time. Wrap the whole ``Link`` call site in a
  parent that re-renders if you need reactive content.
* Style props are forwarded to the emitted ``text`` leaf so callers can
  write ``Link("file.py", url="...", color="blue", bold=True)`` and have
  the SGR sequence wrap the OSC payload — terminals render the link in
  the requested style.

Cross-layer note: OSC 8 sequences contain bytes that are not visible
cells. :mod:`pyink.layout.measure` was extended to strip OSC sequences
(alongside the pre-existing CSI handling) so the measure pass reports the
true display width of a Link's text — otherwise the layout would
over-allocate cells and the link would render with stray padding.

PR3 scope: ships ``Link`` only. ``Divider`` is PR4.
"""

from __future__ import annotations

from typing import Any

from pyink.core.element import Element, create_element
from pyink.core.reconciler import Reconciler
from pyink.hooks._runtime import _get_current_instance
from pyink.layout import layout, render_layout_to_string

__all__ = ["Link"]

#: OSC 8 hyperlink template. ``{url}`` is interpolated verbatim; the
#: ``\x1b\\`` (ST) terminator is the more widely supported form (vs. the
#: 7-bit BEL terminator). Two pairs of ``;;`` separate the (empty) link
#: ``id`` from the URI.
_OSC_OPEN = "\x1b]8;;{url}\x1b\\"
_OSC_CLOSE = "\x1b]8;;\x1b\\"

#: Default width used by the inner layout pass when no live Instance is
#: bound (e.g. when ``Link`` is rendered via :func:`render_to_string`).
#: Matches :func:`pyink.components.Transform`'s fallback.
_DEFAULT_COLUMNS: int = 80


def _wrap_osc8(text: str, url: str) -> str:
    """Wrap ``text`` in an OSC 8 hyperlink sequence pointing at ``url``.

    The OSC open/close sequences are emitted even when ``text`` is empty —
    that keeps the call a pure string transform and lets terminals that
    demarcate link regions (rather than link text) still see the
    boundaries. Multi-line ``text`` is wrapped as a whole: each line
    shares the same URL, matching ink-link's behaviour (it does not split
    a link across lines).
    """
    return f"{_OSC_OPEN.format(url=url)}{text}{_OSC_CLOSE}"


def _LinkImpl(**props: Any) -> Element:
    """Function component body — runs inside the reconciler render context.

    Reads ``url`` and forwarded ``Text`` style props from ``props`` (passed
    through by :func:`Link`).
    """
    url: str = props["url"]
    # All remaining props (other than ``url``) are forwarded to the
    # emitted text leaf. We don't whitelist — any unknown prop just gets
    # ignored by the renderer (matching Text's lenient handling).
    text_props: dict[str, Any] = {
        k: v for k, v in props.items() if k != "url"
    }
    captured_children: tuple[Any, ...] = props["__link_children__"]

    # Build a throwaway host tree containing the children and render it,
    # exactly like Transform. A fresh Reconciler scopes any effects the
    # children might establish to this snapshot and tears them down
    # immediately after — Link captures a static string at mount time.
    inner = create_element("box", *captured_children)
    reconciler = Reconciler()
    mounted = reconciler.mount(inner)
    try:
        inst = _get_current_instance()
        columns = _DEFAULT_COLUMNS
        if inst is not None:
            cols_attr = getattr(inst, "columns", 0)
            if isinstance(cols_attr, int) and cols_attr > 0:
                columns = cols_attr
        tree = layout(mounted, columns=columns)
        rendered = render_layout_to_string(tree)
    finally:
        reconciler.unmount(mounted)

    wrapped = _wrap_osc8(rendered, url)
    return create_element("text", wrapped, **text_props)


def Link(
    *children: Any,
    url: str,
    **props: Any,
) -> Element:
    """Create a clickable OSC 8 terminal hyperlink.

    Parameters
    ----------
    *children:
        Link label content. May contain ``str``, lazy ``Callable[[], str]``
        text leaves, nested :class:`Element` instances (e.g.
        :func:`Text`), or tuples/lists thereof. Children are rendered to
        a string at mount time and wrapped in the OSC 8 sequence —
        reactive children captured by reference will not re-render the
        link on change (wrap the ``Link`` call site in a parent that
        re-renders instead, mirroring :func:`Transform`).
    url:
        Target URL. Emitted verbatim inside the OSC 8 sequence — callers
        are responsible for using a parseable URI
        (``https://…``, ``file://…``, ``mailto:…``).
    **props:
        Forwarded to the emitted ``text`` leaf. Supports every style prop
        :func:`Text` accepts: ``color``, ``backgroundColor``, ``bold``,
        ``italic``, ``underline``, ``strikethrough``, ``inverse``,
        ``dimColor``, ``wrap``. The SGR sequence wraps the OSC payload so
        terminals render the link in the requested style.

    Returns
    -------
    Element
        An element whose ``type`` is a function component
        (:func:`_LinkImpl`). The factory itself never runs the inner
        layout — the wrapped function is invoked by the reconciler on
        mount, which is what makes ``Box(Link(...), Text(...))`` safe to
        call from outside a render context.

    Usage
    -----
    ::

        Link("Click here", url="https://example.com")
        Link(Text("file.py", color="blue"), url="file:///path/to/file.py")
        Link("docs", url="https://example.com", color="cyan", underline=True)
    """
    if not isinstance(url, str):
        raise TypeError(
            f"Link 'url' must be a str, got {type(url).__name__!r}"
        )
    # Stash children on the props dict so the function component can
    # retrieve them without us having to thread them through the element's
    # own children slot (function components receive only ``props``).
    link_props: dict[str, Any] = dict(props)
    link_props["url"] = url
    link_props["__link_children__"] = children
    return create_element(_LinkImpl, **link_props)
