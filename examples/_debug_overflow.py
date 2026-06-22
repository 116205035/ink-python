"""Render TextInputDemo at a too-short terminal height and dump the frame.

Writes UTF-8 output to a file so box-drawing chars survive the Windows
console code page. We render the real demo tree at decreasing ``rows``
and record each painted row with its visible width so any overlap /
border breakage / right-edge overflow is obvious.
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pyink import render  # noqa: E402

_demo_path = ROOT / "examples" / "text-input" / "text_input_demo.py"
_spec = importlib.util.spec_from_file_location("_text_input_demo", _demo_path)
assert _spec and _spec.loader
_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_demo)
TextInputDemo = _demo.TextInputDemo

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

buf = io.StringIO()


def dump(cols: int, rows: int) -> None:
    out = io.StringIO()
    inst = render(TextInputDemo(), stdout=out, columns=cols, rows=rows)
    time.sleep(0.4)
    buf.write(f"\n===== columns={cols}, rows={rows} =====\n")
    visible = ANSI.sub("", inst.current_frame)
    n = len(visible.split("\n"))
    over_h = "  <<< TALLER THAN TERMINAL" if n > rows else ""
    buf.write(f"(frame has {n} lines for a {rows}-row terminal){over_h}\n")
    for i, ln in enumerate(visible.split("\n")):
        w = len(ln)
        mark = "  <<< WIDE" if w > cols else ""
        buf.write(f"{i:2d}|{ln}|w={w}{mark}\n")
    inst.unmount()


for cols, rows in ((40, 12), (50, 20), (30, 10)):
    dump(cols, rows)

Path(ROOT / "examples" / "_debug_overflow_out.txt").write_text(
    buf.getvalue(), encoding="utf-8"
)
print("written")
