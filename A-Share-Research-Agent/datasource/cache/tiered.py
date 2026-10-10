"""TieredCache — L1 (内存) + L2 (文件) 二级缓存。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .base import CacheStats
from .file import FileCache
from .memory import InMemoryCache

logger = logging.getLogger(__name__)


@dataclass
class TieredCacheStats:
    """二级缓存统计。"""
    l1: CacheStats
    l2: CacheStats

    @property
    def total_hits(self) -> int:
        return self.l1.hits + self.l2.hits

    @property
    def total_misses(self) -> int:
        return self.l2.misses

    @property
    def hit_rate(self) -> float:
        total = self.total_hits + self.total_misses
        return self.total_hits / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": "tiered",
            "l1_memory": self.l1.to_dict(),
            "l2_file": self.l2.to_dict(),
            "total_hits": self.total_hits,
            "total_misses": self.total_misses,
            "hit_rate": round(self.hit_rate, 3),
        }


class TieredCache:
    """L1 (InMemoryCache) + L2 (FileCache) 二级缓存。"""

    def __init__(
        self,
        *,
        cache_dir: str | None = None,
        l1_max_items: int = 10_000,
        l2_max_file_size_kb: int = 2048,
    ) -> None:
        self._l1 = InMemoryCache(max_items=l1_max_items)
        self._l2 = FileCache(cache_dir=cache_dir, max_file_size_kb=l2_max_file_size_kb)

    def get(self, key: str) -> Any:
        value = self._l1.get(key)
        if value is not None:
            return value
        # 从 L2 命中时回填 L1，并透传剩余 TTL
        # （不能用 ttl=0 永久缓存，否则 L2 过期/删除后 L1 仍返回陈旧数据）
        entry = self._l2.get_with_expiry(key)
        if entry is not None:
            expires_at, value = entry
            ttl = max(0.0, expires_at - time.time()) if expires_at else 0.0
            self._l1.set(key, value, ttl=ttl)
            return value
        return None

    def set(self, key: str, value: Any, ttl: float = 0.0) -> None:
        self._l1.set(key, value, ttl=ttl)
        self._l2.set(key, value, ttl=ttl)

    def delete(self, key: str) -> None:
        self._l1.delete(key)
        self._l2.delete(key)

    def clear(self) -> None:
        self._l1.clear()
        self._l2.clear()

    def stats(self) -> TieredCacheStats:
        return TieredCacheStats(l1=self._l1.stats(), l2=self._l2.stats())
