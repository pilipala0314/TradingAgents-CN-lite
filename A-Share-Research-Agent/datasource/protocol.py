"""datasource.protocol — Provider 统一接口契约。

所有数据源 Provider 必须满足此 Protocol：
1. name 属性：唯一标识
2. health_check()：轻量健康检查
3. supported_methods()：声明支持的方法列表

具体数据方法（get_daily_bars 等）仍用 duck-typing，但必须：
- 是 async def
- 返回 pd.DataFrame | list[dict] | dict
- 失败抛 ProviderError（不静默返回 None）
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProviderProtocol(Protocol):
    """数据源 Provider 统一接口契约。

    用法::

        @register_provider("my_source")
        class MySourceProvider:
            @property
            def name(self) -> str:
                return "my_source"

            async def health_check(self) -> bool:
                return True

            def supported_methods(self) -> list[str]:
                return ["get_daily_bars", "get_stock_list"]

            async def get_daily_bars(self, symbol: str, ...) -> pd.DataFrame:
                ...
    """

    @property
    def name(self) -> str:
        """Provider 唯一标识（如 'akshare', 'sina'）。"""
        ...

    async def health_check(self) -> bool:
        """轻量健康检查（不发真实请求，仅验证依赖可用）。

        Returns:
            True = 可用；False = 不可用（依赖缺失/配置错误）
        """
        ...

    def supported_methods(self) -> list[str]:
        """声明本 provider 支持的所有 method 名。

        用于：
        - 自动注册时填充 method_table
        - /health 端点展示 provider 能力
        """
        ...
