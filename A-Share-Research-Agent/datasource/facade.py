"""datasource.facade — 公共入口（单例）。

用法::

    from datasource import get_source

    source = get_source()
    df = await source.route("get_daily_bars", "600519", "20250101", "20250701")
"""
from __future__ import annotations

import logging
import threading

from datasource.router import DataSource

logger = logging.getLogger(__name__)

_source: DataSource | None = None
_source_lock = threading.Lock()


def get_source() -> DataSource:
    """获取 DataSource 单例（懒加载，线程安全双检）。

    首次调用时：
    1. 触发 datasource.providers 模块加载（所有 @register_provider 生效）
    2. 实例化所有已注册 provider
    3. 用 FULL_METHOD_TABLE 构造 DataSource
    """
    global _source
    if _source is None:
        with _source_lock:
            if _source is None:
                # 触发 provider 自动注册
                import datasource.providers  # noqa: F401
                from datasource.method_table import FULL_METHOD_TABLE
                from datasource.registry import build_providers

                providers = build_providers()
                _source = DataSource(
                    providers=providers,
                    method_table=FULL_METHOD_TABLE,
                )
                logger.info(
                    "DataSource 初始化完成: %d providers, %d methods",
                    len(providers), len(_source.available_methods()),
                )
    return _source


def set_source(source: DataSource | None) -> None:
    """覆盖默认 DataSource（测试/注入用）。"""
    global _source
    _source = source
