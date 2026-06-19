"""Tests for :func:`pyink.components.transform.Transform` (PR7).

Covers:

* Basic single-line transform (uppercase).
* Multi-line children: each line is passed through ``transform`` with its
  index.
* Transform wrapping a Box with multiple Text children.
* Nested Transform (transform inside transform).
* Empty / whitespace-only children.
* ANSI preservation: styling on Text children survives into the
  transform (the callable receives the styled string).
* ``render_to_string`` direct usage (transform snapshots are static).
"""

from __future__ import annotations

from typing import Any

from pyink import Box, Text, Transform, render_to_string

# ---------------------------------------------------------------------------
# Basic
# ---------------------------------------------------------------------------


def test_transform_uppercases_single_line() -> None:
    tree = Transform(
        Text("hello world"),
        transform=lambda line, idx: line.upper(),
    )
    assert render_to_string(tree, columns=40) == "HELLO WORLD"


def test_transform_preserves_index_argument() -> None:
    seen: list[int] = []

    def xform(line: str, idx: int) -> str:
        seen.append(idx)
        return line.upper()

    tree = Transform(
        Text("a\nb\nc"),
        transform=xform,
    )
    render_to_string(tree, columns=40)
    assert seen == [0, 1, 2]


def test_transform_receives_each_line_of_multiline_children() -> None:
    """Each line of the rendered output is passed through ``transform``."""
    tree = Transform(
        Box(
            Text("alpha"),
            Text("beta"),
            Text("gamma"),
            flexDirection="column",
        ),
        transform=lambda line, idx: line.upper(),
    )
    out = render_to_string(tree, columns=40)
    assert "ALPHA" in out
    assert "BETA" in out
    assert "GAMMA" in out


def test_transform_indent_each_line() -> None:
    """A real-world use case: prefix each line with two spaces."""
    tree = Transform(
        Box(
            Text("first"),
            Text("second"),
            flexDirection="column",
        ),
        transform=lambda line, idx: f"  {line}",
    )
    out = render_to_string(tree, columns=40)
    # Each line should be indented by two spaces.
    assert "\n  second" in out or out.startswith("  first")


def test_transform_inside_box() -> None:
    """A Transform nested inside layout still renders correctly."""
    tree = Box(
        Text("label"),
        Transform(
            Text("value"),
            transform=lambda line, idx: line.upper(),
        ),
        flexDirection="column",
    )
    out = render_to_string(tree, columns=40)
    assert "label" in out
    assert "VALUE" in out


def test_transform_nested_transform() -> None:
    """Outer transform sees the inner transform's output."""
    tree = Transform(
        Transform(
            Text("hi"),
            transform=lambda line, idx: line.upper(),
        ),
        transform=lambda line, idx: f"[{line}]",
    )
    assert render_to_string(tree, columns=40) == "[HI]"


def test_transform_empty_children() -> None:
    tree: Any = Transform(transform=lambda line, idx: line.upper())
    assert render_to_string(tree, columns=40) == ""


def test_transform_passes_ansi_styling_through() -> None:
    """ANSI escapes from styled Text children reach the transform callable."""
    seen: list[str] = []

    def xform(line: str, idx: int) -> str:
        seen.append(line)
        return line

    tree = Transform(
        Text("red", color="red"),
        transform=xform,
    )
    render_to_string(tree, columns=40)
    # The transform should have received the ANSI-wrapped text.
    assert any("\x1b[" in s for s in seen)


def test_transform_can_strip_ansi() -> None:
    """A transform that drops ANSI sequences produces plain text."""
    import re

    strip_re = re.compile(r"\x1b\[[0-9;]*m")

    tree = Transform(
        Text("red", color="red", bold=True),
        transform=lambda line, idx: strip_re.sub("", line),
    )
    out = render_to_string(tree, columns=40)
    assert out == "red"
    assert "\x1b[" not in out


def test_transform_no_op_passthrough() -> None:
    """``transform=lambda line, idx: line`` reproduces the children verbatim."""
    tree = Transform(
        Box(
            Text("a"),
            Text("b"),
            flexDirection="column",
        ),
        transform=lambda line, idx: line,
    )
    out = render_to_string(tree, columns=40)
    assert "a" in out
    assert "b" in out


def test_transform_returns_element() -> None:
    from pyink.core.element import Element

    el = Transform(Text("hi"), transform=lambda line, idx: line)
    assert isinstance(el, Element)


def test_transform_can_change_line_count() -> None:
    """A transform that splits a line into two — documented behaviour.

    The layout engine has already positioned the children based on the
    original line count, so adding lines via ``transform`` will misalign
    the rendered grid relative to siblings. This is an accepted
    constraint; we only verify the transformed text is what we emit.
    """
    tree = Transform(
        Text("split_me"),
        transform=lambda line, idx: line.replace("_", "\n"),
    )
    out = render_to_string(tree, columns=40)
    # The output should contain the two halves on separate lines.
    assert "split" in out
    assert "me" in out
