"""PyInk extension components.

Externals are opt-in: users do ``from ink.externals import Spinner``
rather than importing them from the top-level package (PRD Decision 5 —
externals carry heavier dependencies / non-essential surface area and
stay out of the default namespace).
"""

from ink.externals.big_text import BigText
from ink.externals.confirm_input import ConfirmInput
from ink.externals.diff import StructuredDiff
from ink.externals.divider import Divider
from ink.externals.gradient import Gradient
from ink.externals.highlighted_code import DEFAULT_THEME, HighlightedCode
from ink.externals.link import Link
from ink.externals.markdown import DEFAULT_MARKDOWN_THEME, Markdown
from ink.externals.progress_bar import ProgressBar
from ink.externals.select_input import SelectInput
from ink.externals.spinner import SPINNERS, Spinner
from ink.externals.streaming_text import StreamingText
from ink.externals.table import Table
from ink.externals.task_list import TaskItem, TaskList
from ink.externals.text_input import TextInput

__all__ = [
    "BigText",
    "ConfirmInput",
    "DEFAULT_MARKDOWN_THEME",
    "DEFAULT_THEME",
    "Divider",
    "Gradient",
    "HighlightedCode",
    "Link",
    "Markdown",
    "ProgressBar",
    "SPINNERS",
    "SelectInput",
    "Spinner",
    "StreamingText",
    "StructuredDiff",
    "Table",
    "TaskItem",
    "TaskList",
    "TextInput",
]
