"""InMemoryCache — 默认 cache 实现，进程内 dict + 过期时间戳。

特性：
- O(1) get / set / delete
- 过期时间由 (timestamp, value) 元组保存，get 时惰性检查
- 容量上限（max_items）防 OOM，LRU 简单实现（按插入顺序淘汰）
- 命中率统计内置（hits / misses / sets / deletes）
- 线程安全：加锁，跨 asyncio 协程安全

注意：
- **非多进程共享**：每进程一份 cache。分布式场景用 RedisCache（接口预留）。
- **不序列化**：value 直接存原对象引用；DataFrame 之类走引用计数。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

from .base import CacheStats


class InMemoryCache:
    """进程内 LRU + TTL 缓存实现。"""

    def __init__(self, max_items: int = 10_000) -> None:
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_items = max_items
        self._stats = CacheStats()

    def get(self, key: str) -> Any:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._stats.misses += 1
                return None
            expires_at, value = entry
            if expires_at and expires_at <= time.time():
                # 过期 → 惰性删除
                del self._store[key]
                self._stats.misses += 1
                return None
            # LRU touch
            self._store.move_to_end(key)
            self._stats.hits += 1
            return value

    def set(self, key: str, value: Any, ttl: float = 0.0) -> None:
        with self._lock:
            # ttl<0 表示删除
            if ttl < 0:
                self._store.pop(key, None)
                self._stats.deletes += 1
                return
            expires_at = (time.time() + ttl) if ttl > 0 else 0.0
            self._store[key] = (expires_at, value)
            self._store.move_to_end(key)
            self._stats.sets += 1
            # LRU 淘汰
            while len(self._store) > self._max_items:
                self._store.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            if self._store.pop(key, None) is not None:
                self._stats.deletes += 1

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._stats = CacheStats()

    def stats(self) -> CacheStats:
        """返回当前统计快照（不复制，调用方只读）。"""
        return self._stats

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
