"""兼容 shim — `AkShareClient` 现在是 `AkshareProvider` 的别名。

历史：本类方法实现已下沉到 `data_pipeline/providers/akshare_provider.py`，
作为 DataRouter 路由表里的 'akshare' provider。所有 etl 任务继续
`from data_pipeline.akshare_client import AkShareClient` 不需要改动。

设计目的：
- `data_pipeline/etl/*.py` 的 `client = AkShareClient()` 全部继续工作；
- 实际 provider 实现集中在 `providers/akshare_provider.py`，便于 DataRouter 注入；
- `AkShareClient` 和 `AkshareProvider` 指向同一个类，零行为差异。
"""
from data_pipeline.providers.akshare_provider import AkshareProvider

# 旧类名 = 新类（同一对象）
AkShareClient = AkshareProvider

__all__ = ["AkShareClient"]
