"""PyInk built-in host components (PR4 + PR7).

These factories wrap :func:`ink.core.element.create_element` for the
host tags the layout engine understands:

* :func:`Box` — ``"box"`` flex container.
* :func:`Text` — ``"text"`` leaf.
* :func:`Newline` — convenience ``text`` leaf with ``"\\n"`` body.
* :func:`Spacer` — ``box`` with ``flexGrow=1`` (or fixed ``width`` when
  ``size=`` is given).

PR7 adds the two function-component counterparts that map 1:1 to ink's
``Static`` and ``Transform``:

* :func:`Static` — permanently renders a list above the live frame.
* :func:`Transform` — applies a per-line transform to its children's
  rendered string.
"""

from ink.components.box import Box
from ink.components.newline import Newline
from ink.components.spacer import Spacer
from ink.components.static import Static
from ink.components.text import Text
from ink.components.transform import Transform

__all__ = ["Box", "Newline", "Spacer", "Static", "Text", "Transform"]
