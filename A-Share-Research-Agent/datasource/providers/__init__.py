"""datasource.providers — 全部 Provider 自动注册入口。

import 本模块时，所有 @register_provider 装饰的类被注册到全局表。
facade.py 中 `import datasource.providers` 即完成全部注册。

24 个 Provider 全部原生实现于本目录，数据获取实现唯一收敛于此。
"""
from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

# 全部 provider 模块（import 即触发 @register_provider）
_PROVIDER_MODULES = [
    "akshare", "a_stock_data", "sina", "netease", "cninfo",
    "financial_report", "global_quote", "hk_southbound", "hk_full",
    "us_stock", "bse", "futures", "research_rag",
    "baostock", "tushare", "eastmoney", "news", "sentiment",
    "policy", "fundamentals_ext", "global_media", "geopolitics", "cninfo_news",
]

for _mod in _PROVIDER_MODULES:
    try:
        importlib.import_module(f"datasource.providers.{_mod}")
    except Exception as exc:  # 单 provider 加载失败不影响其他
        logger.warning("provider 模块加载失败: %s (%s)", _mod, exc)
