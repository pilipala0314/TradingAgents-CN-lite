"""DataRouter 端点健康度统计 — P3.6 拆分。

每个 method + provider 组合维护一份 stats：
- total / success / failure / fallback_count
- 最后错误信息
- 延迟采样（最近 50 次）
"""
from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Any


class EndpointStats:
    """单端点的运行统计（线程安全）。"""

    def __init__(self, max_latency_samples: int = 50) -> None:
        self.total: int = 0
        self.success: int = 0
        self.failure: int = 0
        self.fallback_count: int = 0
        self.last_error: str = ""
        self.latencies_ms: deque[float] = deque(maxlen=max_latency_samples)
        self._lock = Lock()

    def record_success(self, latency_ms: float) -> None:
        with self._lock:
            self.total += 1
            self.success += 1
            self.latencies_ms.append(latency_ms)

    def record_failure(self, latency_ms: float, error: str) -> None:
        with self._lock:
            self.total += 1
            self.failure += 1
            self.latencies_ms.append(latency_ms)
            self.last_error = error

    def record_fallback(self) -> None:
        with self._lock:
            self.fallback_count += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            lats = list(self.latencies_ms)
            avg = (sum(lats) / len(lats)) if lats else 0.0
            success_rate = (self.success / self.total) if self.total else 0.0
            return {
                "total": self.total,
                "success": self.success,
                "failure": self.failure,
                "fallback_count": self.fallback_count,
                "success_rate": round(success_rate, 4),
                "avg_latency_ms": round(avg, 2),
                "last_error": self.last_error,
            }

    def reset(self) -> None:
        with self._lock:
            self.total = 0
            self.success = 0
            self.failure = 0
            self.fallback_count = 0
            self.last_error = ""
            self.latencies_ms.clear()


def health_score(stats: EndpointStats) -> float:
    """根据 stats 算 0~1 健康分（成功率 × 延迟惩罚）。"""
    snap = stats.snapshot()
    if snap["total"] == 0:
        return 1.0
    rate = snap["success_rate"]
    lat = snap["avg_latency_ms"]
    # 延迟 > 5s 视为不可用
    lat_penalty = max(0.0, 1.0 - max(0.0, lat - 5000.0) / 10000.0)
    return round(rate * lat_penalty, 4)
