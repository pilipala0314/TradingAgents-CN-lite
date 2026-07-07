"""DataRouter 子包 — P3.6 拆分。

模块：
- registry：路由表 / 分类（不持状态）
- health  ：轻量级健康分工具

注：`DataFetchError`（带 method/attempted/last_error 详细上下文）和
`EndpointStats`（dataclass 详细版，含 p95/fallback_rate 等指标）均仍位于
`data_pipeline.data_router`，本包不重复定义，避免双源导致签名漂移。
"""
from __future__ import annotations

from data_pipeline.router.health import health_score
from data_pipeline.router.registry import (
    CORE_METHOD_TABLE,
    EXTENDED_METHOD_TABLE,
    TOOL_CATEGORIES,
    find_category,
    list_categories,
    list_methods,
    merged_method_table,
)

__all__ = [
    "CORE_METHOD_TABLE",
    "EXTENDED_METHOD_TABLE",
    "TOOL_CATEGORIES",
    "find_category",
    "health_score",
    "list_categories",
    "list_methods",
    "merged_method_table",
]
