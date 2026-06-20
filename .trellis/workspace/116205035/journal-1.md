# Journal - 116205035 (Part 1)

> AI development session journal
> Started: 2026-06-19

---



## Session 1: PyInk MVP: 8 PRs from empty repo to 516-test TUI framework

**Date**: 2026-06-20
**Task**: PyInk MVP: 8 PRs from empty repo to 516-test TUI framework
**Branch**: `main`

### Summary

Built complete PyInk MVP from scratch in one session: 8 PRs covering signals core, reconciler (signals model — components mount once, no React-style rerun), pure-Python flex engine (ink oracle-aligned), Box/Text/Newline/Spacer/Static/Transform components with ANSI rendering, render pipeline (inline frame diff + alternate screen + FPS throttle), hooks (use_input/use_app/use_window_size with Unix termios + Windows VT input raw mode), 6 examples (counter/select-input/borders/static/use-input/use-focus), README + LICENSE + py.typed. Two post-MVP bug fixes: Windows arrow/Tab/F-key capture (missing ENABLE_VIRTUAL_TERMINAL_INPUT + msvcrt chunk drain) and reactive style props (callable support for Box/Text decoration + border visibility props so signal changes update border/color without remount). Final state: 516 passed + 22 xfailed, mypy strict + ruff green across 70 files. 13 ADR-lite decisions captured in PRD.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `101a04a` | (see git log) |
| `4315595` | (see git log) |
| `c6aa70c` | (see git log) |
| `98d9826` | (see git log) |
| `b657951` | (see git log) |
| `00357db` | (see git log) |
| `d7c3bbe` | (see git log) |
| `3c42a4d` | (see git log) |
| `cefbdde` | (see git log) |
| `e7c0869` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 2: PyInk MVP-fits examples + 3 bug fixes

**Date**: 2026-06-20
**Task**: PyInk MVP-fits examples + 3 bug fixes
**Branch**: `main`

### Summary

Added 6 examples (alternate-screen, transform, computed-batch, nested-layout, ansi-colors, use-window-size) covering MVP features that lacked demos. Examples then surfaced 3 real bugs in MVP core, all fixed with regression tests: (1) long text overflowed bordered Box in nested-layout — flex.py text-leaf wrap guard suppressed re-wrapping on subsequent layout passes, fixed via FlexNode.original_text snapshot + monotonic-tighten re-wrap; (2) alternate-screen exit wiped user scrollback — Instance.unmount cleared frame after exit_alternate_screen restored primary buffer + bare \x1b[?1049h/l didn't save cursor on some terminals, fixed by skipping clear-frame in alt mode and bracketing 1049 with explicit DECSC/DECRC; (3) text style leak across wrapped lines (user-reported 'background overflow') — _paint_text wrapped entire multi-line string with one style pair then split, only first line had opener and last had reset, fixed by per-line styling. Final state: 534 passed + 22 xfailed, mypy strict + ruff green. 13 examples total now demonstrate every MVP capability.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `06c04b7` | (see git log) |
| `c606633` | (see git log) |
| `838f01d` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 3: PyInk Phase 2: externals + Context + new hooks + measure

**Date**: 2026-06-20
**Task**: PyInk Phase 2: externals + Context + new hooks + measure
**Branch**: `main`

### Summary

Phase 2 complete: 8 PRs adding 3 externals (Spinner/Link/Divider), Context system (create_context/Provider/use_context), 5 new hooks (use_interval/use_focus/use_focus_manager/use_context/use_box_metrics), measure_element API + Box ref prop. Key design decisions: Provider pops at end of own subtree mount (not on unmount) because PyInk components run once at mount — prevents sibling-subtree leakage; Spinner wraps a function component via create_element so hooks run in reconciler context; Link extends measure.py ANSI regex to strip OSC 8 (was CSI-only, caused layout width over-allocation); layout_epoch Signal drives use_box_metrics reactivity (bumped after every layout pass, use_box_metrics subscribes via Computed). 5 new examples (spinner/link/divider/use-focus-real/measure-element) bring total to 18. State-management spec updated to document Context + ref/measure patterns. Final state: 693 passed + 22 xfailed, mypy strict + ruff green across 99 source files.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `4e95086` | (see git log) |
| `357ecfa` | (see git log) |
| `7c73b7e` | (see git log) |
| `faadde0` | (see git log) |
| `a177b15` | (see git log) |
| `d83c985` | (see git log) |
| `b18dbdb` | (see git log) |
| `eb38e58` | (see git log) |
| `c1cdcb5` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete
