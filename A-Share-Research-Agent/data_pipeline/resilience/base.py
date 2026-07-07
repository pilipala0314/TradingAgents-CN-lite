"""韧性层基类 — 配置 schema + 异常 + 统计。

设计目标：
- 配置：每个策略一个 dataclass，字段显式、必有默认值
- 异常：ResilienceError 带完整诊断信息（attempts / last_error / strategy_name）
- 统计：ResilienceStats 统一暴露给 /health
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 异常 ──────────────────────────────────────────────────────────────

class ResilienceError(RuntimeError):
    """韧性策略用尽后的统一异常。

    Attributes:
        strategy:  触发异常的策略名（"retry" / "rate_limit" / "circuit" / "bulkhead" / "ip_pool"）
        attempts:  已经尝试的次数
        last_error: 最后一个底层异常
        context:    诊断上下文（IP 列表 / 熔断状态 / 等待时长...）
    """

    def __init__(
        self,
        strategy: str,
        message: str,
        *,
        attempts: int = 0,
        last_error: BaseException | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.strategy = strategy
        self.attempts = attempts
        self.last_error = last_error
        self.context = context or {}
        super().__init__(f"[{strategy}] {message}（attempts={attempts}）")


# ── 配置 schema ─────────────────────────────────────────────────────

@dataclass
class RetryConfig:
    """重试配置。"""

    max_attempts: int = 3        # 最大尝试次数（含首次）
    initial_delay_sec: float = 0.5  # 首次重试延迟
    max_delay_sec: float = 10.0     # 单次重试延迟上限
    backoff_factor: float = 2.0     # 退避倍数
    jitter: bool = True             # 加 ±25% 抖动
    # 哪些异常触发重试（None = 全部异常）
    retry_on: tuple[type[BaseException], ...] | None = None


@dataclass
class RateLimitConfig:
    """限流配置（令牌桶）。"""

    rps: float = 5.0     # 每秒允许的请求数
    burst: int = 10      # 桶容量


@dataclass
class CircuitBreakerConfig:
    """熔断器配置（滑动窗口 + 3 状态）。"""

    failure_threshold: int = 5      # 连续失败 N 次触发熔断
    recovery_timeout_sec: float = 30.0  # 熔断后多少秒进入半开
    window_size: int = 20           # 滑动窗口大小（最近 N 次）


@dataclass
class IpPoolConfig:
    """IP 池配置。"""

    enabled: bool = True
    # "round_robin" / "random" / "weighted"
    strategy: str = "round_robin"
    # 切换 IP 的触发条件：失败 N 次后换 IP
    switch_after_failures: int = 1
    # 同一 IP 最多连续用多少次（防单 IP 过度使用）
    max_per_ip: int = 100


@dataclass
class BulkheadConfig:
    """并发隔离配置。"""

    max_concurrent: int = 10  # 同时最多 N 个请求


# ── 统计 ─────────────────────────────────────────────────────────────

@dataclass
class ResilienceStats:
    """单一策略实例的统计。"""

    total_calls: int = 0
    success: int = 0
    failed: int = 0
    retries: int = 0
    rate_limited_waits: int = 0
    circuit_rejections: int = 0
    ip_switches: int = 0
    bulkhead_rejections: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total_calls": self.total_calls,
            "success": self.success,
            "failed": self.failed,
            "retries": self.retries,
            "rate_limited_waits": self.rate_limited_waits,
            "circuit_rejections": self.circuit_rejections,
            "ip_switches": self.ip_switches,
            "bulkhead_rejections": self.bulkhead_rejections,
        }


@dataclass
class ResilientEndpointStats:
    """一个 (method:provider) 端点的韧性统计聚合。"""

    method: str
    provider: str
    stats: dict[str, ResilienceStats] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "provider": self.provider,
            "by_strategy": {k: v.to_dict() for k, v in self.stats.items()},
        }
