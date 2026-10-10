"""datasource.config — 自包含配置（零项目依赖）。

设计目标：datasource 可整体复制到其他项目独立运行。

取值来源：仅标准环境变量（ROUTE_* / REDIS_URL / TUSHARE_TOKEN），
无 core 依赖、无 .env 加载器依赖。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class DatasourceConfig:
    """数据源模块运行时配置。"""

    cache_ttl_sec: float = 30.0
    default_timeout: float = 12.0
    default_strict: bool = False
    cache_persistent: bool = False
    default_resilience: bool = True
    quality_check: bool = True
    tushare_token: str = ""


def _from_env() -> DatasourceConfig:
    return DatasourceConfig(
        cache_ttl_sec=_env_float("ROUTE_CACHE_TTL_SEC", 30.0),
        default_timeout=_env_float("ROUTE_DEFAULT_TIMEOUT", 12.0),
        default_strict=_env_bool("ROUTE_DEFAULT_STRICT", False),
        cache_persistent=_env_bool("ROUTE_CACHE_PERSISTENT", False),
        default_resilience=_env_bool("ROUTE_DEFAULT_RESILIENCE", True),
        quality_check=_env_bool("ROUTE_CACHE_QUALITY_CHECK", True),
        tushare_token=os.getenv("TUSHARE_TOKEN", ""),
    )


_config: DatasourceConfig | None = None


def get_datasource_config() -> DatasourceConfig:
    """获取模块配置（仅环境变量，自包含无项目依赖）。"""
    global _config
    if _config is not None:
        return _config
    _config = _from_env()
    return _config
