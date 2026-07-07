"""RedisCache — 分布式缓存实现（P3.3）。

设计目标：
- 多进程 / 多机部署时共享 cache，避免重复打外网。
- TTL 由 Redis 原生 EXPIRE 命令管理。
- value 序列化默认用 pickle（开发期），生产建议用 msgpack/orjson。
- 容错：连接失败 / 写入失败 → 透明降级到 InMemoryCache，不阻断调用方。

使用：
```python
from data_pipeline.cache import RedisCache
from data_pipeline.data_router import DataRouter

# url=None 时降级到 InMemoryCache
router = DataRouter(cache=RedisCache(url="redis://localhost:6379/0"))
```

健康检查：
```python
cache = RedisCache(url="...")
cache.health()  # {"backend": "redis", "connected": True, "dbsize": 1234, "url": "..."}
```
"""
from __future__ import annotations

import logging
import pickle
import threading
from typing import Any

from .base import CacheStats
from .in_memory import InMemoryCache

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis 缓存实现（同步 redis-py 8.x）。

    降级策略：
    - 构造时 url 为 None / 空白 → 直接走 InMemoryCache（开发 / 测试用）
    - 运行时 redis 抛 ConnectionError / TimeoutError → 记录一次 warning，
      后续操作走 fallback（in-memory），不阻断调用方
    - 每次操作前 ping() 检查连接（开销 ~ms），太频繁可关
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        key_prefix: str = "ashare:router:",
        default_ttl: float = 60.0,
        timeout: float = 2.0,
        health_check: bool = True,
    ) -> None:
        self._url = url
        self._key_prefix = key_prefix
        self._default_ttl = default_ttl
        self._timeout = timeout
        self._health_check = health_check
        self._stats = CacheStats()
        self._fallback = InMemoryCache()
        self._lock = threading.Lock()
        self._client: Any = None
        self._connected = False
        # url=None 时直接标记 degraded — 调用方一眼看出没有真 redis
        self._degraded = url is None or url.strip() == ""
        if url and url.strip():
            self._connect()

    # ── 连接管理 ─────────────────────────────────

    def _connect(self) -> None:
        try:
            import redis
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise ModuleNotFoundError(
                "未安装 redis 依赖，请先 `pip install redis>=4`",
            ) from exc
        try:
            self._client = redis.Redis.from_url(
                self._url,  # type: ignore[arg-type]
                socket_timeout=self._timeout,
                socket_connect_timeout=self._timeout,
                decode_responses=False,
            )
            # 启动 ping 一次
            self._client.ping()
            self._connected = True
            self._degraded = False
            logger.info("RedisCache 已连接: %s", self._url)
        except Exception as exc:
            logger.warning(
                "RedisCache 连接失败 (url=%s)：%s — 降级到 InMemoryCache",
                self._url, exc,
            )
            self._client = None
            self._connected = False
            self._degraded = True

    def _ensure_connected(self) -> bool:
        """每次操作前确保连接 OK。失败返 False（调用方走 fallback）。"""
        if not self._client:
            return False
        if not self._health_check:
            return self._connected
        try:
            self._client.ping()
            self._connected = True
            self._degraded = False
            return True
        except Exception as exc:
            if not self._degraded:
                logger.warning("RedisCache 运行时连接断开：%s — 降级到 InMemoryCache", exc)
            self._connected = False
            self._degraded = True
            return False

    # ── 协议方法（同步）────────────────────────

    def get(self, key: str) -> Any:
        if self._ensure_connected():
            try:
                raw = self._client.get(self._full_key(key))
                if raw is None:
                    self._stats.misses += 1
                    return None
                value = pickle.loads(raw)
                self._stats.hits += 1
                return value
            except Exception as exc:
                logger.warning("RedisCache.get 失败，降级: %s", exc)
                self._degraded = True
        # fallback
        value = self._fallback.get(key)
        if value is None:
            self._stats.misses += 1
        else:
            self._stats.hits += 1
        return value

    def set(self, key: str, value: Any, ttl: float = 0.0) -> None:
        effective_ttl = ttl if ttl > 0 else self._default_ttl
        payload = pickle.dumps(value)
        if self._ensure_connected():
            try:
                if effective_ttl > 0:
                    self._client.setex(self._full_key(key), int(effective_ttl), payload)
                else:
                    self._client.set(self._full_key(key), payload)
                self._stats.sets += 1
                # 同步写 fallback（避免下次连接失败读到 stale）
                self._fallback.set(key, value, ttl=ttl)
                return
            except Exception as exc:
                logger.warning("RedisCache.set 失败，降级: %s", exc)
                self._degraded = True
        # fallback
        self._fallback.set(key, value, ttl=ttl)
        self._stats.sets += 1

    def delete(self, key: str) -> None:
        if self._ensure_connected():
            try:
                self._client.delete(self._full_key(key))
            except Exception as exc:
                logger.warning("RedisCache.delete 失败，降级: %s", exc)
                self._degraded = True
        self._fallback.delete(key)
        self._stats.deletes += 1

    def clear(self) -> None:
        if self._ensure_connected():
            try:
                # 只删本 prefix 的 key，不动其他业务数据
                cursor = 0
                while True:
                    cursor, keys = self._client.scan(
                        cursor=cursor, match=f"{self._key_prefix}*", count=500,
                    )
                    if keys:
                        self._client.delete(*keys)
                    if cursor == 0:
                        break
            except Exception as exc:
                logger.warning("RedisCache.clear 失败: %s", exc)
                self._degraded = True
        self._fallback.clear()

    def stats(self) -> CacheStats:
        return self._stats

    # ── 健康检查 / 调试 ─────────────────────────

    def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "backend": "redis" if self._connected else "in_memory_fallback",
            "url": self._url or "(none)",
            "degraded": self._degraded,
            "key_prefix": self._key_prefix,
            "default_ttl_s": self._default_ttl,
            "stats": self._stats.to_dict(),
        }
        if self._client and self._ensure_connected():
            try:
                out["dbsize"] = self._client.dbsize()
                info = self._client.info("memory")
                out["used_memory_human"] = info.get("used_memory_human", "?")
            except Exception as exc:
                out["info_error"] = str(exc)
        else:
            out["dbsize"] = None
        return out

    def _full_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"
