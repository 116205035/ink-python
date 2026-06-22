# PyInk Phase 5 — VirtualList + scroll API cleanup

## Goal

为长列表场景（千条消息、日志流）提供虚拟滚动，只渲染可见窗口的 items。同时清理 Bug 5（`_pyink_scroll` 私有约定暴露在公共 Text 上）。

## Background

- PyInk 已交付 7 个 task（MVP / examples / Phase 2-4 / cursor-fixes / layout-hardening）
- 1156 测试通过，layout 引擎已加固（min-content + clip-to-content-box）
- Bug 5（`_pyink_scroll` 私有 prop 暴露在公共 Text 上）推迟到 Phase 5 一起设计——VirtualList 的滚动需求会驱动 scroll API 的正确形状

Phase 5 不做增量渲染（subtree cache + fiber reconciliation）。理由：
- signals 模型已细粒度订阅（组件函数只跑一次）
- Markdown/Code 有 LRU cache
- 真瓶颈不明确（1000 行 layout ~5-15ms，30 FPS 预算 33ms）
- 风险高（3-4 周架构改造）

VirtualList 是"应用层优化"（只渲染可见），增量渲染是"引擎层优化"（缓存所有子树）。前者够用。

## Decisions (ADR-lite)

### Decision 1: Text 加公开 `scroll_offset` prop

**Context**: 当前 `_pyink_scroll` 是私有 prop（下划线前缀），用共享可变 dict 侧信道传递光标行信息。无文档，API 表面有内部约定泄漏。

**Decision**: Text 加公开 `scroll_offset: int | Signal[int] | Callable[[], int] | None = None`：
- None = 默认 top-keeping（当前行）
- int = 固定偏移
- Signal/Callable = 动态（光标跟随滚动）
- 文本行数 > 容器高度时，从 scroll_offset 行开始显示
- render_layout 读公开 `scroll_offset`，不再读私有 `_pyink_scroll`

**Consequences**:
- ✅ 公开 API，有 docstring
- ✅ VirtualList 可以用 signal 驱动滚动
- ✅ TextInput 的 rows viewport 重构成用 scroll_offset
- ❌ Text props 表面 +1（可接受）

### Decision 2: VirtualList 支持固定 + 动态高度

**Context**: 长列表的 items 高度可能统一（日志行）或变化（消息内容不同）。

**Decision**:
```python
def VirtualList(
    items: list[T] | Signal[list[T]] | Callable[[], list[T]],
    *,
    render_item: Callable[[T, int], Element],
    viewport_height: int,                    # 可见窗口行数
    item_height: int | None = None,         # 固定高度（快路径）；None = 动态测量
    on_scroll: Callable[[int], None] | None = None,
    overscan: int = 3,                       # 上下额外渲染的 item 数（减少滚动闪烁）
    key: Callable[[T, int], str] | None = None,  # 稳定 key（优化 diff）
) -> Element:
    """虚拟滚动列表。
    - 只渲染 [scroll_offset, scroll_offset + viewport_height + 2*overscan) 范围的 items
    - item_height=None 时动态测量（measure_element）
    - 内部维护 scroll_offset: Signal[int]
    - 暴露 on_scroll 回调让外部控制（如键盘 PageUp/Down）
    """
```

**Consequences**:
- ✅ 固定高度快路径（O(1) 计算 visible range）
- ✅ 动态高度支持（measureElement 测量后缓存）
- ❌ 动态高度复杂（要维护 measured heights 累积索引）

### Decision 3: 滚动控制用键盘 hook（可选）

**Context**: VirtualList 默认不绑定键盘（跟 SelectInput 一样，焦点由外部 use_focus_manager 控制）。

**Decision**: VirtualList 不直接处理按键。提供 `use_scroll_controls(list_handle)` hook 让外部 wire 键盘：
```python
def use_scroll_controls(handle) -> ScrollControls:
    """返回 focus_next/focus_previous/page_up/page_down/scroll_to_top/scroll_to_bottom。"""
```

或者更简单：VirtualList 接受 `keybindings: dict[str, Callable] | None`。

MVP 先不实现键盘绑定，只暴露 scroll API（`handle.scroll_to(index)`），让 demo 手动 wire。

### Decision 4: 不实现增量渲染

**Context**: 增量渲染（subtree cache + reconciliation）工作量大（3-4 周）、风险高。

**Decision**: Phase 5 不做。VirtualList 通过"只渲染可见 items"已经解决长列表性能。等真有性能瓶颈（profile 出 layout 全树 pass 是热点）再立项。

## Requirements

### 必交付（3 PRs）

#### PR1: Text.scroll_offset + Bug 5 重构

`src/pyink/components/text.py`：
- 加 `scroll_offset: int | Signal[int] | Callable[[], int] | None = None` prop
- docstring 文档化

`src/pyink/layout/render_layout.py`：
- 读 `scroll_offset` 而不是 `_pyink_scroll`
- 求值逻辑：Signal/Callable 在 layout 时求值，拿到当前 offset
- offset 决定文本从第几行开始显示（`text.split("\n")[offset:offset+height]`）

`src/pyink/externals/text_input.py`：
- 移除 `_pyink_scroll` 私有 prop
- 改用 `scroll_offset` 公开 prop（传 signal 或 callable）

测试：
- `test_text_scroll_offset_none_shows_from_top`
- `test_text_scroll_offset_int_shows_from_offset`
- `test_text_scroll_offset_signal_dynamic`
- `test_text_scroll_offset_clamps_at_max`
- 回归：TextInput 多行 + rows viewport 仍然光标跟随

#### PR2: VirtualList

`src/pyink/externals/virtual_list.py`：
```python
def VirtualList(
    items,
    *,
    render_item,
    viewport_height,
    item_height=None,
    overscan=3,
    key=None,
    on_scroll=None,
    **box_props,
) -> Element: ...
```

实现：
- 内部 `scroll_offset: Signal[int]`
- 固定高度（item_height=int）：visible range = [scroll_offset, scroll_offset + viewport_height + overscan*2]
- 动态高度（item_height=None）：维护 measured heights 累积索引，二分查找 scroll_offset 对应的 item index
- 渲染 visible items 到 column 容器
- 用 Box(height=viewport_height) + 内部 Text(scroll_offset=...) 实现窗口
- 暴露 `use_virtual_list_controls()` hook 或类似

测试：
- 固定高度：1000 items，viewport=10，只渲染 ~16 items（10 + 2*3 overscan）
- 动态高度：items 高度不同，scroll 准确
- scroll_offset 越界 clamp
- overscan 减少闪烁
- items 变化（追加）时滚动跟随
- items 减少（删除）时正确收缩

#### PR3: Examples + README

3 个新 examples：
- `examples/virtual-list/virtual_list_demo.py` —— 1000 个 items 固定高度，↑↓ 滚动
- `examples/virtual-list-dynamic/dynamic_demo.py` —— 消息列表（不同长度），动态高度
- `examples/scroll-text/scroll_text_demo.py` —— 长 Text + scroll_offset 手动控制

更新 README + integration tests。

## Acceptance Criteria

- [ ] 3 PRs 全部交付
- [ ] Bug 5 修复（`_pyink_scroll` 私有约定消失，`Text.scroll_offset` 公开）
- [ ] VirtualList 支持 1000+ items 不卡（FPS >= 30）
- [ ] mypy strict + ruff 全绿
- [ ] 全部测试通过（应 1156 + ~30 新 = 1186+）
- [ ] 3 个新 examples 跑通

## Out of Scope

- 增量渲染（subtree cache + reconciliation）—— 未来性能优化 task
- VirtualList 双向滚动锚定（scroll anchor on resize）—— polish
- 虚拟化的嵌套（VirtualList 内含 VirtualList）—— 极端场景

## Technical Notes

### 参考实现

- ink 没有官方 VirtualList（Claude Code 自己实现了 `VirtualMessageList.tsx`）
- React 的 `react-window` / `react-virtualized`：固定 + 动态高度
- Vue VirtualScroller：动态高度 + 测量缓存
- Textual `VirtualListView`：类似

### 关键约束

- Python 3.11+，mypy strict + ruff
- 不破坏 1156 测试
- VirtualList 不应破坏 layout-hardening 的 min-content + clip-to-box

## Implementation Plan

3 PRs（每 PR implement → check → commit）：

1. **PR1**: Text.scroll_offset + Bug 5 重构
2. **PR2**: VirtualList（固定 + 动态高度）
3. **PR3**: Examples + README

预计 1 周。

## Research References

（暂无）
