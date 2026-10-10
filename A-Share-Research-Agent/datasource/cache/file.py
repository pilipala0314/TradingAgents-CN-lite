"""FileCache — 文件持久化缓存（pickle 序列化 + TTL 惰性检查）。"""
from __future__ import annotations

import logging
import os
import pickle
import threading
import time
from pathlib import Path
from typing import Any

from .base import CacheStats

logger = logging.getLogger(__name__)


class FileCache:
    """文件系统持久化缓存。

    安全说明：缓存值用 pickle 序列化（支持 DataFrame 等任意对象），
    反序列化等价于代码执行——缓存目录必须仅当前用户可写，
    不可与其他不可信进程共享（POSIX 下自动收敛为 0700）。
    """

    def __init__(self, cache_dir: str | None = None, *, max_file_size_kb: int = 2048) -> None:
        if cache_dir is None:
            cache_dir = str(Path(__file__).parent / ".file_cache")
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "posix":
                os.chmod(self._cache_dir, 0o700)  # 仅当前用户可读写（防 pickle 被投毒）
        except OSError:
            pass
        self._max_file_size = max_file_size_kb * 1024
        self._lock = threading.Lock()
        self._stats = CacheStats()

    def get(self, key: str) -> Any:
        entry = self.get_with_expiry(key)
        return entry[1] if entry is not None else None

    def get_with_expiry(self, key: str) -> tuple[float, Any] | None:
        """读取缓存条目并返回 (expires_at, value)；未命中/已过期返回 None。

        expires_at 为 0.0 表示永不过期。供上层（如 TieredCache）
        回填时透传剩余 TTL，避免回填层丢失过期语义。
        """
        with self._lock:
            path = self._key_to_path(key)
            if not path.exists():
                self._stats.misses += 1
                return None
            try:
                with open(path, "rb") as f:
                    expires_at, value = pickle.load(f)
            except Exception:
                self._safe_delete(path)
                self._stats.misses += 1
                return None
            if expires_at and expires_at <= time.time():
                self._safe_delete(path)
                self._stats.misses += 1
                return None
            self._stats.hits += 1
            return expires_at, value

    def set(self, key: str, value: Any, ttl: float = 0.0) -> None:
        if ttl < 0:
            self.delete(key)
            return
        with self._lock:
            try:
                payload = pickle.dumps((time.time() + ttl if ttl > 0 else 0.0, value))
            except Exception:
                return
            if len(payload) > self._max_file_size:
                return
            path = self._key_to_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                tmp_path = path.with_suffix(".tmp")
                with open(tmp_path, "wb") as f:
                    f.write(payload)
                os.replace(str(tmp_path), str(path))
                self._stats.sets += 1
            except Exception as exc:
                logger.warning("FileCache.set 写入失败: key=%s err=%s", key, exc)

    def delete(self, key: str) -> None:
        with self._lock:
            path = self._key_to_path(key)
            if self._safe_delete(path):
                self._stats.deletes += 1

    def clear(self) -> None:
        with self._lock:
            import shutil
            try:
                shutil.rmtree(self._cache_dir, ignore_errors=True)
                self._cache_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            self._stats = CacheStats()

    def stats(self) -> CacheStats:
        return self._stats

    def __len__(self) -> int:
        count = 0
        for _, _, filenames in os.walk(self._cache_dir):
            count += sum(1 for f in filenames if f.endswith(".pkl"))
        return count

    def _key_to_path(self, key: str) -> Path:
        shard = key[:2] if len(key) >= 2 else "__"
        safe_key = key.replace("|", "_").replace("/", "_").replace("\\", "_")
        return self._cache_dir / shard / f"{safe_key}.pkl"

    @staticmethod
    def _safe_delete(path: Path) -> bool:
        try:
            if path.exists():
                path.unlink()
                return True
        except OSError:
            pass
        return False
