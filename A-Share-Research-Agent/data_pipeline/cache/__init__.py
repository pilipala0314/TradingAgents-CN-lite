"""Cache 后端抽象 — DataRouter 的 TTL 缓存层。

设计：
- **CacheBackend Protocol**：定义 get/set/delete/clear 四个核心操作。
- **InMemoryCache**：默认实现，进程内 dict + 过期时间戳。
- **RedisCache**：分布式实现（接口预留，本期不实现）。

为什么独立成模块：
- DataRouter 只依赖 `CacheBackend` Protocol，不关心是内存还是 Redis。
- 后续接 Redis（多进程 / 多机）时只需把 DataRouter(cache=RedisCache(...)) 即可。
- 缓存策略（key 拼接、TTL 计算、序列化）由各 provider / DataRouter 负责，
  本模块只做"存什么 key 返什么 value"的薄壳。
"""
from __future__ import annotations

from .base import CacheBackend, CacheStats
from .in_memory import InMemoryCache

__all__ = ["CacheBackend", "CacheStats", "InMemoryCache"]
