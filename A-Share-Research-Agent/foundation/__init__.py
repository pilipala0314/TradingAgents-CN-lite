"""foundation — 横切基座。

被 L1-L5 所有层依赖，但不反向依赖任何层。

当前内容：
- `schemas/` — 层间唯一共享物：指标包 / 结论包 / 溯源契约（pydantic）

设计约束（见 docs/designs/2026-08-27-research-agent-system-design.md §2.5）：
- 层间只通过 schema + 落盘产物通信，不传内存对象、不互相 import 内部实现
- schema 版本化：新增字段向后兼容，破坏性变更升版本号并保留旧版解析器
"""
