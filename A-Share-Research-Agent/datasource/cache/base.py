"""CacheBackend Protocol — 缓存后端抽象接口。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class CacheStats:
    """缓存命中率统计。"""
    hits: int = 0
    misses: int = 0
    sets: int = 0
    deletes: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "deletes": self.deletes,
            "hit_rate": round(self.hit_rate, 3),
        }


@runtime_checkable
class CacheBackend(Protocol):
    """缓存后端 Protocol。

    - get(key) 不存在/过期 → 返回 None
    - set(key, value, ttl) ttl=0 不过期；ttl<0 删除；ttl>0 秒数过期
    - delete(key) 幂等
    - clear() 清空所有
    """
    def get(self, key: str) -> Any: ...
    def set(self, key: str, value: Any, ttl: float = 0.0) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...
    def stats(self) -> CacheStats: ...
