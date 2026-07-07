"""test_redis_cache — P3.3 Redis 缓存单测。

覆盖：
- url=None 降级到 InMemoryCache
- 不可达 url（连接失败）降级 + 标记 degraded
- get/set/delete/clear/stats 基本协议
- pickle 序列化复杂对象
- TTL 路径（setex）
- health() 输出结构
- 运行时连接断开后 fallback
- key_prefix 拼接
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from data_pipeline.cache import CacheStats
from data_pipeline.cache.redis_cache import RedisCache

# ── url=None 降级 ─────────────────────────────────

def test_url_none_uses_fallback() -> None:
    """url=None 时直接用 InMemoryCache。"""
    c = RedisCache(url=None)
    assert c._client is None
    assert c._connected is False
    assert c._degraded is True
    c.set("k1", "v1", ttl=60)
    assert c.get("k1") == "v1"
    s = c.stats()
    assert s.hits == 1
    assert s.sets == 1


# ── 不可达 url 降级 ──────────────────────────────

def test_unreachable_url_degrades() -> None:
    """不可达 url 启动时连接失败 → 走 fallback。"""
    with patch("redis.Redis.from_url") as mock_from_url:
        mock_client = MagicMock()
        mock_client.ping.side_effect = ConnectionError("connection refused")
        mock_from_url.return_value = mock_client
        c = RedisCache(url="redis://localhost:6390/0", health_check=False)
        assert c._client is None
        assert c._connected is False
        assert c._degraded is True
        # fallback 仍可工作
        c.set("k1", "v1")
        assert c.get("k1") == "v1"


# ── mock redis 客户端基本协议 ─────────────────────

def test_get_miss_increments_misses() -> None:
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.get.return_value = None
    with patch("redis.Redis.from_url", return_value=mock_client):
        c = RedisCache(url="redis://localhost:6379/0", health_check=False)
        result = c.get("missing_key")
        assert result is None
        s = c.stats()
        assert s.misses == 1
        assert s.hits == 0


def test_set_with_ttl_calls_setex() -> None:
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    with patch("redis.Redis.from_url", return_value=mock_client):
        c = RedisCache(url="redis://localhost:6379/0", health_check=False, default_ttl=60)
        c.set("k1", "v1", ttl=120)
        mock_client.setex.assert_called_once()
        args = mock_client.setex.call_args
        # args[0] = (key, ttl, payload)
        assert args.args[0] == "ashare:router:k1"
        assert args.args[1] == 120


def test_set_zero_ttl_calls_set() -> None:
    """ttl=0 用 default_ttl（60s），但 effective_ttl=60 仍走 setex。
    测 ttl<0 路径不删：用 ttl=0 但 default_ttl=0 → ttl=0 走 set。
    """
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    with patch("redis.Redis.from_url", return_value=mock_client):
        c = RedisCache(url="redis://localhost:6379/0", health_check=False, default_ttl=0)
        c.set("k1", "v1", ttl=0)
        # default_ttl=0 → effective_ttl=0 → set() 路径
        mock_client.set.assert_called_once()
        mock_client.setex.assert_not_called()


def test_get_pickle_value() -> None:
    """真 redis 时：能 pickle 复杂对象。"""
    import pickle
    obj = {"a": [1, 2, 3], "b": {"nested": True}}
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.get.return_value = pickle.dumps(obj)
    with patch("redis.Redis.from_url", return_value=mock_client):
        c = RedisCache(url="redis://localhost:6379/0", health_check=False)
        result = c.get("k1")
        assert result == obj
        assert c.stats().hits == 1


def test_delete_calls_redis() -> None:
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    with patch("redis.Redis.from_url", return_value=mock_client):
        c = RedisCache(url="redis://localhost:6379/0", health_check=False)
        c.delete("k1")
        mock_client.delete.assert_called_once_with("ashare:router:k1")
        assert c.stats().deletes == 1


def test_clear_scans_with_prefix() -> None:
    """clear 用 SCAN 只删本 prefix 的 key。"""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    # scan 第一轮返一些 key + cursor=0（结束）
    mock_client.scan.return_value = (0, [b"ashare:router:k1", b"ashare:router:k2"])
    with patch("redis.Redis.from_url", return_value=mock_client):
        c = RedisCache(url="redis://localhost:6379/0", health_check=False)
        c.clear()
        mock_client.scan.assert_called()
        mock_client.delete.assert_called()
        # 删除的是本 prefix 的 key
        call_args = mock_client.delete.call_args
        assert b"ashare:router:" in call_args.args[0]


# ── 运行时断开 → 降级 ─────────────────────────

def test_runtime_disconnect_degrades() -> None:
    """get/set 时 ping 失败 → 降级到 fallback（不抛错）。"""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    with patch("redis.Redis.from_url", return_value=mock_client):
        c = RedisCache(url="redis://localhost:6379/0", health_check=True)
        assert c._connected is True
        # 模拟运行时断开
        mock_client.ping.side_effect = ConnectionError("lost")
        # 写不抛错
        c.set("k1", "v1")
        # 读不抛错
        result = c.get("k1")
        assert result == "v1"  # fallback 写过的
        assert c._degraded is True


# ── health() 输出结构 ─────────────────────────

def test_health_output_when_connected() -> None:
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.dbsize.return_value = 42
    mock_client.info.return_value = {"used_memory_human": "1.5M"}
    with patch("redis.Redis.from_url", return_value=mock_client):
        c = RedisCache(url="redis://localhost:6379/0", health_check=False)
        h = c.health()
        assert h["backend"] == "redis"
        assert h["url"] == "redis://localhost:6379/0"
        assert h["degraded"] is False
        assert h["dbsize"] == 42
        assert h["used_memory_human"] == "1.5M"
        assert "stats" in h
        assert h["key_prefix"] == "ashare:router:"


def test_health_output_when_degraded() -> None:
    """url=None 时 health 报告 degraded + 内存 fallback。"""
    c = RedisCache(url=None)
    h = c.health()
    assert h["backend"] == "in_memory_fallback"
    assert h["degraded"] is True
    assert h["dbsize"] is None


# ── key_prefix 自定义 ──────────────────────────

def test_custom_key_prefix() -> None:
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    with patch("redis.Redis.from_url", return_value=mock_client):
        c = RedisCache(url="redis://localhost:6379/0", key_prefix="myapp:v1:", health_check=False)
        c.set("k1", "v1")
        args = mock_client.setex.call_args
        assert args.args[0] == "myapp:v1:k1"


# ── fallback 计数（不重复计 set/sets）─────────────

def test_fallback_get_increments_stats() -> None:
    """fallback 路径走的 get/set 也计 stats。"""
    c = RedisCache(url=None)
    c.set("a", 1)
    c.set("b", 2)
    c.get("a")
    c.get("missing")
    s = c.stats()
    assert s.sets == 2
    assert s.hits == 1
    assert s.misses == 1
