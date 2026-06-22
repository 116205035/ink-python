# PyInk layout hardening — min-content + CJK cursor + dead param

## Goal

修复 layout 引擎 3 个真实问题，让 PyInk 在小终端 / 长内容 / 嵌套约束下不再乱码式重叠。来源：LAYOUT_ANALYSIS.md 报告（用户用 Claude 分析 flex.py + render_layout.py 得出）。

## Background

Phase 4 + cursor-fixes task 后，layout 引擎有 3 类 bug：
- **Bug 1（严重）**：垂直空间不足时行重叠，而非干净裁剪
- **Bug 4（中）**：CJK 宽字符下光标列与截断点错位 1 格
- **Bug 6（低）**：`_distribute_main` 的 `fixed` 形参是死参数（caller 传了但函数体从不读）

LAYOUT_ANALYSIS.md 的其他 bug（2/3/5/7）属于已知 MVP 取舍（off-by-one、单趟近似、性能 polish），不在本 task 范围。

## Decisions (ADR-lite)

### Decision 1: 文本 leaf 加 min-content 概念

**Context**: `_distribute_main` shrink 时 `out[i] = max(0, ...)` 可把子节点压到 0 高度；`_paint_text` 在 `node.height == 0` 时不 clip，照样画 → 多个 0 高度子节点同 y → 行重叠。

**Decision**: 给 FlexNode 加 `min_content_main: int` 字段（文本 leaf = 最长段落高度，通常 1）。shrink 时 clamp 到 `max(min_content_main, sizes[i] - shrink_amount)`，不下穿 1（文本至少 1 行）。

box 容器的 min-content = sum(children min-content) + padding + border + gaps。

**Consequences**:
- ✅ shrink 不会把文本压到 0 → 不重叠
- ✅ 跟 CSS flex 的 `min-height: auto`（默认 min-content）行为一致
- ❌ 超预算时整体溢出（仍然不完美），但至少不重叠——配合 Bug 1 的 clip-to-box 解决"溢出到兄弟节点"

### Decision 2: render_layout clip 子节点到父 content box

**Context**: 即使 min-content 防止 0 高度，超预算时 content sum > container 还是会让多余行画到兄弟节点的 y 位置。

**Decision**: `_paint_node` 在画每个 child 前，clip 到父的 content box（`x ∈ [content_left, content_right)`，`y ∈ [content_top, content_bottom)`）。Grid 加 `clip(x1, y1, x2, y2)` / `unclip()` API，painter 在范围内丢弃越界写入。

**Consequences**:
- ✅ 即使布局超预算，每个 box 内部画的内容不泄漏到外面
- ✅ 跟 ink / Textual 的"clip to viewport"行为一致
- ❌ 多一层 grid boundary 检查，paint 性能略降（可接受）

### Decision 3: CJK cursor 列按显示宽度对齐

**Context**: text_input.py 把 cursor SGR 按**字符偏移**插入 `_build_displayed_line`，但 `_cursor_visible_column` 用**显示宽度**。两者在宽字符行混排时差 1 格 → 横向截断点可能偏。

**Decision**: 把 cursor SGR 插入改成"按显示宽度定位"——遍历到累积显示宽度 == cursor 可见列时插入 SGR。统一用显示宽度，不用字符偏移。

**Consequences**:
- ✅ CJK + ASCII 混排时光标列精确
- ✅ 截断点也精确（_truncate_line_around_cursor 共用同一个宽度概念）
- ❌ 实现要小心 ANSI escape 的宽度（不计）——已有 helper

### Decision 4: 删 `_distribute_main` 的死参数 `fixed`

**Context**: `_layout_row` / `_layout_column` 构造 `main_is_fixed: list[bool]` 传给 `_distribute_main(fixed=...)`，但函数体从不读 `fixed`。

**Decision**: 删 `_distribute_main` 的 `fixed` 形参 + 两个 caller 的 `main_is_fixed` 构造。纯清理，不改行为。

**Consequences**:
- ✅ 代码更清晰，没 misleading 参数
- ✅ 未来如真需要 min/max content 约束（Decision 1），重新设计参数

## Requirements

### Bug 1: min-content + clip to content box

#### 1.1 FlexNode 加 min_content_main

`src/pyink/layout/flex.py`：
- FlexNode 加 `min_content_main: int = 1` 字段
- text leaf：min_content_main = 1（至少 1 行）
- box 容器：min_content_main = sum(children min_content_main) + padding*2 + border*2 + gaps

#### 1.2 `_distribute_main` shrink 时 clamp

```python
if free < 0:
    ...
    for i in range(n):
        if weights[i] > 0:
            shrink_amount = overflow * (weights[i] / total_weight)
            target = sizes[i] - int(round(shrink_amount))
            out[i] = max(children[i].min_content_main, target)  # 不下穿 min-content
```

#### 1.3 render_layout 加 clip-to-content-box

`src/pyink/layout/render_layout.py`：
- `_Grid` 加 `clip(x1, y1, x2, y2)` + `unclip()` 方法
- `_paint_node` 在 paint 每个 box 的 children 前 push content box clip，paint 完 pop
- 越界写入被丢弃

### Bug 4: CJK cursor 列对齐

`src/pyink/externals/text_input.py`：
- `_build_displayed_line` 改：遍历字符时累积显示宽度，到达 cursor 可见列时插入 SGR
- `_truncate_line_around_cursor` 同步用显示宽度定位
- 复用现有 `_visible_width` / `wcswidth` helper

### Bug 6: 删死参数

`src/pyink/layout/flex.py`：
- `_distribute_main` 删 `fixed` 形参
- `_layout_row` 删 `main_is_fixed` 构造
- `_layout_column` 同上

## Acceptance Criteria

- [ ] Bug 1：rows=10 容纳 4 个 input（multi-line 长内容），缩小到 rows=4 时不重叠（要么 clip 要么 min-content clamp）
- [ ] Bug 4：CJK + ASCII 混排文本，cursor 列跟实际显示位置一致（1 格不差）
- [ ] Bug 6：`_distribute_main` 签名不含 `fixed`，两个 caller 不构造 `main_is_fixed`
- [ ] 全部现有测试通过（1134+22）
- [ ] 新增回归测试覆盖每个 bug
- [ ] mypy strict + ruff 全绿

## Definition of Done

- 3 个 bug 都修 + 回归测试
- LAYOUT_ANALYSIS.md 报告里的 Bug 1/4/6 标记为"已修"
- 不破坏 Phase 1-4 现有测试

## Out of Scope

- Bug 2（off-by-one 取整）：留 MVP 取舍
- Bug 3（单趟近似 vs 迭代收敛）：留 MVP 取舍
- Bug 5（`_pyink_scroll` 耦合重构）：留 polish
- Bug 7（重复布局性能）：留性能优化

## Technical Notes

### 参考实现

- CSS flex `min-height: auto`：默认 min-content
- Yoga：有 min-width/min-height 节点属性
- ink：无显式 min-content，但 text leaf 永远至少 1 行（隐式 min-content=1）
- Textual：有 scrollable container + clip

### 关键约束

- Python 3.11+，mypy strict + ruff
- 不破坏 1134 测试
- min-content 不应破坏现有 flex oracle 测试（ink 对齐）
- clip-to-box 不应破坏边框渲染（Bug 8 已修过的边框完整性）

## Implementation Plan

3 PRs：
1. **PR1**: Bug 6（删死参数，5 分钟）+ Bug 4（CJK cursor 对齐，1-2 小时）
2. **PR2**: Bug 1 part 1（FlexNode.min_content_main + _distribute_main clamp）
3. **PR3**: Bug 1 part 2（_Grid.clip + _paint_node clip-to-content-box）

预计 1-2 天。

## Research References

- LAYOUT_ANALYSIS.md（用户报告，已 commit 在 repo 根）
