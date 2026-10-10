"""缓存后端行为测试。"""
import time

import pytest

from datasource.cache.memory import InMemoryCache
from datasource.cache.tiered import TieredCache


@pytest.mark.unit
class TestInMemoryCache:
    def test_set_get(self):
        c = InMemoryCache()
        c.set("k", {"a": 1})
        assert c.get("k") == {"a": 1}

    def test_miss(self):
        c = InMemoryCache()
        assert c.get("nope") is None

    def test_ttl_expiry(self):
        c = InMemoryCache()
        c.set("k", 1, ttl=0.05)
        assert c.get("k") == 1
        time.sleep(0.1)
        assert c.get("k") is None

    def test_ttl_negative_deletes(self):
        c = InMemoryCache()
        c.set("k", 1)
        c.set("k", 1, ttl=-1)
        assert c.get("k") is None

    def test_lru_eviction(self):
        c = InMemoryCache(max_items=2)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        assert c.get("a") is None
        assert c.get("b") == 2
        assert c.get("c") == 3

    def test_delete(self):
        c = InMemoryCache()
        c.set("k", 1)
        c.delete("k")
        assert c.get("k") is None

    def test_clear(self):
        c = InMemoryCache()
        c.set("k", 1)
        c.clear()
        assert c.get("k") is None


@pytest.mark.unit
class TestTieredCache:
    def test_set_get_across_layers(self, tmp_path):
        c = TieredCache(cache_dir=str(tmp_path))
        c.set("k", [1, 2, 3], ttl=30)
        assert c.get("k") == [1, 2, 3]

    def test_delete_both_layers(self, tmp_path):
        c = TieredCache(cache_dir=str(tmp_path))
        c.set("k", 1, ttl=30)
        c.delete("k")
        assert c.get("k") is None

    def test_l2_backfill(self, tmp_path):
        c = TieredCache(cache_dir=str(tmp_path))
        c._l1.clear()  # 模拟 L1 被清空
        c._l2.set("k", "v", ttl=60)
        assert c.get("k") == "v"  # 应从 L2 回填
