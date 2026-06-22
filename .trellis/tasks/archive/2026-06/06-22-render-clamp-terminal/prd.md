# render() clamps frame to terminal size

## Goal

修复 PyInk inline 模式的根本性 cursor-up 数学错乱 bug：当用户传的 rows/columns 超过实际终端尺寸时，frame 溢出屏幕，frame diff 的 cursor-up 算法失效，导致渲染错乱。

## Background

**Bug**：`render(tree, rows=30)` 在 20 行终端上跑，frame 是 30 行，终端只显示底部 20 行（上面 10 行进 scrollback）。下次 rerender 时 PyInk 发 `\x1b[30A`（光标上移 30），但光标已经在屏幕底，上移后跑到 scrollback 里，新 frame 写到不可见区域 → 渲染错乱。

**Claude Code 的机制**（参考 `D:\Projects\github\claude-code\src\ink\ink.tsx:191`）：
```ts
this.terminalRows = options.stdout.rows || 24;
// 强制读终端真实高度，用户不能传任意 rows
```

PyInk 让用户能传任意 rows 是 bug 的根源。

## Decision

**`render()` 强制裁剪 rows/columns 到实际终端尺寸**：

```python
def render(tree, *, rows=None, columns=None, ...):
    actual = shutil.get_terminal_size()
    if columns is None or columns > actual.columns:
        columns = actual.columns
    if rows is None or rows > actual.rows:
        rows = actual.rows
    # layout 用裁剪后的尺寸
```

用户传 `rows=30` 在 20 行终端 → 实际用 20 行 layout。内容超出由 layout-hardening 的 min-content + clip-to-content-box 处理（不溢出屏幕）。

## Requirements

### 1. 修改 `src/pyink/render/pipeline.py` 的 `render()`

加 terminal size clamp 逻辑：
- `shutil.get_terminal_size()` 读真实尺寸（fallback 80x24）
- 如果用户传的 rows/columns 超过实际，裁剪
- 用裁剪后的尺寸做 layout

### 2. 修改 `src/pyink/render/terminal.py`（如需）

确保 `Terminal.columns` / `Terminal.rows` 反映裁剪后的值。

### 3. 加测试

`tests/render/test_pipeline.py` 或新文件：
- `test_render_clamps_rows_to_terminal_size`：mock 终端 20 行，传 rows=30，验证 layout 用 20
- `test_render_clamps_columns_to_terminal_size`：类似
- `test_render_default_uses_terminal_size`：不传 rows/columns，用终端尺寸
- `test_render_rows_below_terminal_unchanged`：传 rows=10，终端 20，用 10（用户显式要小）

### 4. 验证 demo

`examples/text-input/text_input_demo.py`：
- 改回固定 `render(TextInputDemo(), columns=72, rows=30)`（之前的版本）
- 在小终端（高度 < 30）跑，bug 应消失（frame 被裁剪到终端高度）
- 边框/input 不再错乱

## Acceptance Criteria

- [ ] `render()` 强制 rows/columns <= 实际终端尺寸
- [ ] 测试覆盖 clamp 逻辑
- [ ] demo 改回固定 rows=30，在小终端跑无 bug
- [ ] 全部现有测试通过（1165+22）
- [ ] mypy strict + ruff 全绿

## Out of Scope

- Static 组件验证（可能需要单独 task）
- 双缓冲（frontFrame/backFrame）—— 大重构
- Scroll capture（captureScrolledRows）—— 大重构

## Implementation Plan

单 PR：
1. render() 加 clamp
2. 加测试
3. demo 改回固定 rows=30 验证

预计 1-2 小时。

## Research References

- Claude Code ink: `D:\Projects\github\claude-code\src\ink\ink.tsx:191`
