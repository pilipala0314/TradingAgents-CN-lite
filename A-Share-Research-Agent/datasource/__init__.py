"""datasource — 独立数据源模块。

公共 API::

    from datasource import get_source, DataSource, register_provider

    # 获取路由引擎单例
    source = get_source()

    # 路由调用
    df = await source.route("get_daily_bars", "600519", "20250101", "20250701")

    # 注册新 provider
    @register_provider("my_source")
    class MySourceProvider:
        ...

设计原则：
- 零上层依赖：仅依赖标准库/三方库（项目内可选复用 core.config 配置）
- async-first：所有 provider 方法必须是 async def
- 失败透明：provider 错误包装为 ProviderError，路由层决定 fallback
- 自动注册：@register_provider 装饰器 + providers/__init__.py 触发加载
"""
from __future__ import annotations

from datasource.errors import (
    DataFetchError,
    DataSourceError,
    NonRetryableProviderError,
    ProviderError,
    ResilienceError,
)
from datasource.health_tracker import SourceHealthTracker, get_tracker
from datasource.protocol import ProviderProtocol
from datasource.registry import register_provider
from datasource.types import DataResult, MethodTable

__all__ = [
    # 核心入口（延迟导入，避免循环）
    "get_source",
    "DataSource",
    # 类型
    "DataResult",
    "MethodTable",
    # 协议
    "ProviderProtocol",
    # 注册
    "register_provider",
    # 异常
    "DataFetchError",
    "DataSourceError",
    "NonRetryableProviderError",
    "ProviderError",
    "ResilienceError",
    # 健康追踪
    "SourceHealthTracker",
    "get_tracker",
]


def __getattr__(name: str):
    """延迟导入重量级对象，避免 import datasource 时加载全部 provider。"""
    if name == "get_source":
        from datasource.facade import get_source
        return get_source
    if name == "DataSource":
        from datasource.router import DataSource
        return DataSource
    raise AttributeError(f"module 'datasource' has no attribute {name!r}")
