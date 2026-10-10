"""datasource.registry — Provider 自动注册表。

用法::

    from datasource.registry import register_provider

    @register_provider("akshare")
    class AkshareProvider:
        ...

注册后，`build_providers()` 会自动实例化所有已注册 provider。
路由引擎通过 `get_provider_class(name)` 按需获取。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 全局注册表：provider_name → provider class
_REGISTRY: dict[str, type] = {}


def register_provider(name: str):
    """类装饰器：注册 provider 到全局表。

    Args:
        name: provider 唯一标识（如 'akshare', 'sina'）

    用法::

        @register_provider("my_source")
        class MySourceProvider:
            @property
            def name(self) -> str:
                return "my_source"
            ...
    """
    def wrapper(cls: type) -> type:
        if name in _REGISTRY:
            logger.warning(
                "Provider '%s' 重复注册，覆盖旧类 %s → %s",
                name, _REGISTRY[name].__name__, cls.__name__,
            )
        cls._provider_name = name  # type: ignore[attr-defined]
        _REGISTRY[name] = cls
        logger.debug("注册 provider: %s → %s", name, cls.__name__)
        return cls
    return wrapper


def build_providers() -> dict[str, Any]:
    """实例化所有已注册 provider，返回 {name: instance}。

    实例化失败的 provider 会被跳过（warning 日志），不影响其他 provider。
    """
    instances: dict[str, Any] = {}
    for name, cls in _REGISTRY.items():
        try:
            instances[name] = cls()
        except Exception as exc:
            logger.warning("Provider '%s' 实例化失败，跳过: %s", name, exc)
    return instances


def get_provider_class(name: str) -> type | None:
    """按名称获取已注册的 provider class（未注册返回 None）。"""
    return _REGISTRY.get(name)


def list_providers() -> list[str]:
    """返回所有已注册 provider 名称（排序）。"""
    return sorted(_REGISTRY.keys())
