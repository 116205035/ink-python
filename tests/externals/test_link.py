"""Tests for :func:`ink.externals.Link` (Phase 2 PR3).

OSC 8 hyperlinks wrap the rendered text of ``Link``'s children in a
terminal hyperlink sequence:

    \\x1b]8;;URL\\x1b\\\\TEXT\\x1b]8;;\\x1b\\\\

These tests cover:

* Basic link: ``Link("hello", url="https://x")`` produces the OSC 8
  sequence with the right URL and label.
* Sequence format exactly matches the spec (open, text, close).
* Text style props (color / bold / underline) are forwarded to the
  emitted text leaf and wrap the OSC payload.
* Nested Text children (``Link(Text("click", color="blue"), url=...)``).
* URLs with special characters (query strings, fragments, ``file://``).
* Empty children (Link with no label — OSC sequence still emitted).
* Integration: Link inside a Box with sibling Text.
* Element shape: ``Link`` is a function component, not a host tag.
"""

from __future__ import annotations

from typing import Any

from ink import Box, Text, create_element, render_to_string
from ink.core.element import Element
from ink.externals import Link
from ink.externals.link import _LinkImpl

#: Expected OSC 8 open/close sequences. Kept as constants so a failure
#: points at the wrapping logic, not at a typo in the test data.
OSC_OPEN_PREFIX = "\x1b]8;;"
OSC_OPEN_SUFFIX = "\x1b\\"
OSC_CLOSE = "\x1b]8;;\x1b\\"


def _osc_open(url: str) -> str:
    """Build the OSC 8 open sequence for ``url`` (mirrors ``Link``)."""
    return f"{OSC_OPEN_PREFIX}{url}{OSC_OPEN_SUFFIX}"


def _wrap(text: str, url: str) -> str:
    """Build the full expected wrapped sequence for ``text``/``url``."""
    return f"{_osc_open(url)}{text}{OSC_CLOSE}"


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_link_returns_function_component_element() -> None:
    el = Link("hi", url="https://x")
    assert isinstance(el, Element)
    # Function component, not a host tag.
    assert callable(el.type)
    assert el.type is _LinkImpl
    # url is captured in props.
    assert el.props["url"] == "https://x"
    # Children are stashed for the function component to consume.
    assert el.props["__link_children__"] == ("hi",)
    # No positional children on the element itself.
    assert el.children == ()


def test_link_url_is_required_keyword() -> None:
    """Link requires ``url`` as a keyword argument."""
    # We can't easily test the missing-keyword case because Python
    # enforces it at the signature level; instead verify that passing
    # a non-str url raises a clear TypeError from our guard.
    try:
        Link("hi", url=123)  # type: ignore[arg-type]
    except TypeError as exc:
        assert "url" in str(exc)
    else:
        raise AssertionError("Link should reject non-str url")


def test_link_extra_props_captured_for_text_leaf() -> None:
    el = Link("hi", url="https://x", color="blue", bold=True)
    assert el.props["color"] == "blue"
    assert el.props["bold"] is True
    # url is still captured.
    assert el.props["url"] == "https://x"


# ---------------------------------------------------------------------------
# Basic rendering — OSC 8 sequence format
# ---------------------------------------------------------------------------


def test_basic_link_wraps_text_in_osc8() -> None:
    tree: Any = Link("hello", url="https://x")
    out = render_to_string(tree, columns=40)
    assert out == _wrap("hello", "https://x")


def test_link_sequence_format_is_correct() -> None:
    """Exact byte-level check of the OSC 8 wrapping."""
    tree: Any = Link("click", url="https://example.com")
    out = render_to_string(tree, columns=40)
    expected = "\x1b]8;;https://example.com\x1b\\click\x1b]8;;\x1b\\"
    assert out == expected


def test_link_url_with_query_string() -> None:
    url = "https://example.com/search?q=hello%20world&lang=en"
    tree: Any = Link("search", url=url)
    out = render_to_string(tree, columns=80)
    assert out == _wrap("search", url)


def test_link_url_with_fragment() -> None:
    url = "https://example.com/docs#section-1"
    tree: Any = Link("docs", url=url)
    out = render_to_string(tree, columns=80)
    assert out == _wrap("docs", url)


def test_link_file_url() -> None:
    url = "file:///home/user/file.py"
    tree: Any = Link("file.py", url=url)
    out = render_to_string(tree, columns=80)
    assert out == _wrap("file.py", url)


def test_link_mailto_url() -> None:
    url = "mailto:user@example.com"
    tree: Any = Link("email", url=url)
    out = render_to_string(tree, columns=80)
    assert out == _wrap("email", url)


# ---------------------------------------------------------------------------
# Empty children
# ---------------------------------------------------------------------------


def test_empty_link_renders_to_empty_string() -> None:
    """Link with no children renders to nothing.

    The inner layout produces a zero-width text leaf; the renderer's grid
    right-trim drops it. Callers who want an empty-but-present link
    boundary should pass a literal space (``Link(" ", url=...)``).
    """
    tree: Any = Link(url="https://x")
    out = render_to_string(tree, columns=40)
    assert out == ""


# ---------------------------------------------------------------------------
# Style prop forwarding
# ---------------------------------------------------------------------------


def test_link_forwards_color_to_text_leaf() -> None:
    """color wraps the OSC payload — link is rendered in the requested colour."""
    tree: Any = Link("hi", url="https://x", color="red")
    out = render_to_string(tree, columns=40)
    # SGR red open (\\x1b[31m) wraps the OSC-wrapped text and reset closes it.
    assert out.startswith("\x1b[31m")
    assert out.endswith("\x1b[0m")
    # The OSC sequence sits inside the SGR colour run.
    assert _wrap("hi", "https://x") in out


def test_link_forwards_bold_and_underline() -> None:
    tree: Any = Link("hi", url="https://x", bold=True, underline=True)
    out = render_to_string(tree, columns=40)
    # apply_style emits separate SGR runs for each toggle (bold=1, underline=4),
    # so both openers prefix the OSC-wrapped text and a single reset closes it.
    assert "\x1b[1m" in out
    assert "\x1b[4m" in out
    assert _wrap("hi", "https://x") in out
    assert out.endswith("\x1b[0m")


def test_link_forwards_color_cyan() -> None:
    tree: Any = Link("docs", url="https://x", color="cyan")
    out = render_to_string(tree, columns=40)
    assert "\x1b[36m" in out
    assert _wrap("docs", "https://x") in out


# ---------------------------------------------------------------------------
# Nested children
# ---------------------------------------------------------------------------


def test_link_with_nested_text_child() -> None:
    """Link wrapping a styled Text child preserves the child's style."""
    tree: Any = Link(Text("click", color="blue"), url="https://x")
    out = render_to_string(tree, columns=40)
    # Inner Text blue styling is inside the OSC sequence.
    expected_inner = "\x1b[34mclick\x1b[0m"
    assert out == _wrap(expected_inner, "https://x")


def test_link_with_multiple_string_children() -> None:
    """Multiple positional children concatenate into the link label."""
    tree: Any = Link("My ", "Website", url="https://x")
    out = render_to_string(tree, columns=40)
    assert out == _wrap("My Website", "https://x")


def test_link_with_box_child() -> None:
    """A Box inside a Link renders to a string before wrapping."""
    tree: Any = Link(
        Box(Text("nested")),
        url="https://x",
    )
    out = render_to_string(tree, columns=40)
    assert out == _wrap("nested", "https://x")


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_link_inside_box_with_sibling_text() -> None:
    """Link composes with siblings inside Box."""
    tree: Any = Box(
        Text("See "),
        Link("docs", url="https://x"),
        Text(" for more"),
    )
    out = render_to_string(tree, columns=60)
    assert "See " in out
    assert " for more" in out
    # The link label and URL are both present in the OSC sequence.
    assert _osc_open("https://x") in out
    assert "docs" in out
    assert out.endswith(" for more")


def test_link_as_child_of_create_element_box() -> None:
    """Link works as a child of an Element built via create_element."""
    tree: Any = create_element(
        "box",
        Link("home", url="https://x"),
        Text("!"),
    )
    out = render_to_string(tree, columns=40)
    assert _wrap("home", "https://x") in out
    assert out.endswith("!")


def test_link_wraps_multiline_children() -> None:
    """Multi-line children are wrapped as a single link region.

    The OSC sequence wraps the whole rendered string (which may itself
    contain newlines); each line shares the same URL, matching ink-link.
    """
    tree: Any = Link(
        Box(Text("line1"), Text("line2"), flexDirection="column"),
        url="https://x",
    )
    out = render_to_string(tree, columns=40)
    # The OSC open sequence prefixes the first line, close suffixes the
    # last line, and the newline sits inside the link region.
    assert out.startswith(_osc_open("https://x"))
    assert out.endswith(OSC_CLOSE)
    assert "line1" in out
    assert "line2" in out
    assert "\n" in out


# ---------------------------------------------------------------------------
# Cross-layer: OSC sequences don't inflate measured width
# ---------------------------------------------------------------------------


def test_link_does_not_over_allocate_layout_width() -> None:
    """OSC bytes must measure as zero-width so layout doesn't pad.

    Regression for the cross-layer concern documented in
    :mod:`ink.externals.link`: prior to extending the measure layer's
    ANSI regex to cover OSC sequences, the OSC payload counted as visible
    width and the rendered output gained trailing padding. This test
    pins the fix.
    """
    tree: Any = Link("hi", url="https://x")
    out = render_to_string(tree, columns=40)
    # No trailing whitespace beyond the OSC close sequence.
    assert out == out.rstrip()
    # Visible width (ignoring escapes) is exactly the label width.
    import re

    visible = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", out)
    assert visible == "hi"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_link() -> None:
    from ink.externals import Link as InitLink

    assert InitLink is Link


def test_link_not_in_ink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in; top-level import must fail."""
    import ink

    assert not hasattr(ink, "Link"), "Link must NOT be top-level"
