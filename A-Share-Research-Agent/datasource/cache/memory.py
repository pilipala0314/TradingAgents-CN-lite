"""InMemoryCache — 进程内 LRU + TTL 缓存。"""
from __future__ import annotations

import copy
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
                del self._store[key]
                self._stats.misses += 1
                return None
            self._store.move_to_end(key)
            self._stats.hits += 1
            return value

    def set(self, key: str, value: Any, ttl: float = 0.0) -> None:
        with self._lock:
            if ttl < 0:
                self._store.pop(key, None)
                self._stats.deletes += 1
                return
            expires_at = (time.time() + ttl) if ttl > 0 else 0.0
            self._store[key] = (expires_at, value)
            self._store.move_to_end(key)
            self._stats.sets += 1
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
        with self._lock:
            return copy.copy(self._stats)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
