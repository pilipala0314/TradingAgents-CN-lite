"""datasource.cache — 轻量缓存层（零外部依赖，内存 + 本地文件）。

- CacheBackend Protocol：定义 get/set/delete/clear 四个核心操作
- InMemoryCache：默认实现，进程内 LRU + TTL
- FileCache：文件持久化，进程重启不丢失
- TieredCache：L1 (内存) + L2 (文件) 二级缓存
"""
from __future__ import annotations

from .base import CacheBackend, CacheStats
from .file import FileCache
from .memory import InMemoryCache
from .tiered import TieredCache, TieredCacheStats

__all__ = [
    "CacheBackend",
    "CacheStats",
    "FileCache",
    "InMemoryCache",
    "TieredCache",
    "TieredCacheStats",
]
