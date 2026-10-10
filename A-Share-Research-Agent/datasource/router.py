"""datasource.router — 精简路由引擎（~300 行）。

职责单一：
- 接收 method + args → 按 method_table 找 provider chain → 逐个尝试 → 返回结果
- 组合韧性中间件（retry + circuit breaker + rate limit + bulkhead）
- 组合缓存中间件（TTL cache + stampede 保护）
- 记录 EndpointStats

不包含：TOOL_CATEGORIES、method_table 声明、provider 初始化。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from datasource.cache import CacheBackend, CacheStats, InMemoryCache, TieredCache
from datasource.call_logger import enqueue_call_log
from datasource.config import get_datasource_config
from datasource.errors import DataFetchError
from datasource.health_tracker import get_tracker
from datasource.resilience import ResilienceError, ResiliencePolicy
from datasource.resilience.base import (
    BulkheadConfig,
    CircuitBreakerConfig,
    RateLimitConfig,
    RetryConfig,
)
from datasource.stats import EndpointStats, ProviderAttemptDetail
from datasource.types import MethodTable

logger = logging.getLogger(__name__)


async def _run_with_timeout(
    fn: Any, args: tuple, kwargs: dict, timeout: float,
) -> Any:
    """单端点超时包装（模块级函数，避免循环内重复建闭包）。"""
    return await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)


# 空结果=失败的方法（选择器类）：对指定范围应返回非空集合，
# 空=链路异常而非“确实无数据”，需继续 fallback 到下一 provider。
# 事件类方法（新闻/公告/龙虎榜等）空=合法响应，故意不在本集合内。
# get_individual_fund_flow：有效个股必有资金流历史，空 = WAF 剥空响应而非“无数据”。
_EMPTY_AS_FAILURE_METHODS: frozenset[str] = frozenset({
    "get_industry_ranking",
    "get_sector_fund_flow",
    "get_stock_list",
    "get_individual_fund_flow",
})


def _is_empty_result(result: Any) -> bool:
    """空结果判定（list/tuple/dict/str/DataFrame/Series）；无法判定时视为非空。"""
    if isinstance(result, (list, tuple, dict, str)):
        return len(result) == 0
    empty = getattr(result, "empty", None)  # pandas DataFrame / Series
    return empty if isinstance(empty, bool) else False


class DataSource:
    """多 provider 数据路由引擎。

    Args:
        providers:       {provider_name: provider_instance}
        method_table:    {method_name: {provider_name: attr_name}}
        cache_ttl:       默认缓存 TTL（秒）
        default_timeout: 单端点超时（秒）
        default_strict:  strict 模式（True=任一失败即抛）
        cache:           缓存后端实例
        default_resilience: 默认韧性策略
    """

    def __init__(
        self,
        providers: dict[str, Any],
        method_table: MethodTable,
        *,
        cache_ttl: float | None = None,
        default_timeout: float | None = None,
        default_strict: bool | None = None,
        cache: CacheBackend | None = None,
        default_resilience: ResiliencePolicy | None = None,
    ) -> None:
        cfg = get_datasource_config()
        self.providers = providers
        self.cache_ttl = cache_ttl if cache_ttl is not None else cfg.cache_ttl_sec
        self.default_timeout = default_timeout if default_timeout is not None else cfg.default_timeout
        self.default_strict = default_strict if default_strict is not None else cfg.default_strict
        self._quality_check = cfg.quality_check

        # 构建内部路由表：method → {provider_name: bound_callable}
        self._method_table: dict[str, dict[str, Any]] = {}
        for method, prov_map in method_table.items():
            self._method_table[method] = {}
            for prov_name, attr_name in prov_map.items():
                if prov_name not in providers:
                    continue
                fn = getattr(providers[prov_name], attr_name, None)
                if fn is None:
                    logger.warning("路由表注册失败: %s.%s 不存在", prov_name, attr_name)
                    continue
                self._method_table[method][prov_name] = fn

        # 缓存：进程内内存（默认）或 内存+本地文件 分层（ROUTE_CACHE_PERSISTENT=true）
        if cache is not None:
            self._cache: CacheBackend = cache
        elif cfg.cache_persistent:
            self._cache = TieredCache()
        else:
            self._cache = InMemoryCache()
        # Stampede 保护锁（带引用计数 + 安全淘汰防内存泄漏）
        self._inflight: dict[str, asyncio.Lock] = {}
        self._inflight_refs: dict[str, int] = {}
        self._inflight_max: int = 1024  # 最多保留 1024 个锁

        # 健康度统计
        self._stats: dict[str, EndpointStats] = {}
        for method, provs in self._method_table.items():
            for prov_name in provs:
                self._stats[f"{method}:{prov_name}"] = EndpointStats(method=method, provider=prov_name)

        # 韧性策略
        self._resilience: dict[str, ResiliencePolicy] = {}
        self._default_provider_policies: dict[str, ResiliencePolicy] = {}
        if default_resilience is not None:
            self._default_resilience: ResiliencePolicy | None = default_resilience
        elif cfg.default_resilience:
            self._default_resilience = self._build_default_policy()
        else:
            self._default_resilience = None

    @staticmethod
    def _build_default_policy() -> ResiliencePolicy:
        return ResiliencePolicy(
            retry=RetryConfig(max_attempts=3, initial_delay_sec=0.5, max_delay_sec=8.0, backoff_factor=2.0),
            rate_limit=RateLimitConfig(rps=5.0, burst=10),
            circuit=CircuitBreakerConfig(failure_threshold=5, recovery_timeout_sec=30.0, window_size=20),
            bulkhead=BulkheadConfig(max_concurrent=10),
        )

    # ── 核心路由 ──────────────────────────────────────────────────

    async def route(
        self,
        method: str,
        *args: Any,
        provider: str | None = None,
        strict: bool | None = None,
        timeout: float | None = None,
        use_cache: bool = True,
        **kwargs: Any,
    ) -> Any:
        """按 method 路由到第一个成功的 provider。"""
        if method not in self._method_table:
            raise ValueError(f"不支持的数据方法: {method}")

        effective_strict = strict if strict is not None else self.default_strict
        effective_timeout = timeout if timeout is not None else self.default_timeout

        # 缓存命中（内存 → 本地文件层，由所选 CacheBackend 决定）
        # 缓存键含 provider：强制指定源的调用与默认链调用相互隔离，
        # 避免单源结果污染默认链（不同源的复权口径/字段可能不同）
        cache_key: str | None = None
        if use_cache and self.cache_ttl > 0:
            cache_key = self._build_cache_key(method, args, kwargs, provider)
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._fire_log(method, args, kwargs, cached, 0.0, True, cache_key)
                return cached
            # Stampede 保护（只淘汰无引用的锁，避免删掉正在使用的锁）
            if len(self._inflight) >= self._inflight_max:
                for k in [k for k, refs in self._inflight_refs.items() if refs == 0][: self._inflight_max // 4]:
                    self._inflight.pop(k, None)
                    self._inflight_refs.pop(k, None)
            lock = self._inflight.setdefault(cache_key, asyncio.Lock())
            self._inflight_refs[cache_key] = self._inflight_refs.get(cache_key, 0) + 1
            try:
                async with lock:
                    cached = self._cache.get(cache_key)
                    if cached is not None:
                        self._fire_log(method, args, kwargs, cached, 0.0, True, cache_key)
                        return cached
                    return await self._dispatch(
                        method, args, kwargs, provider,
                        effective_strict, effective_timeout, cache_key,
                    )
            finally:
                refs = self._inflight_refs.get(cache_key)
                if refs is not None:
                    # 锁可能已被淘汰移除（此时不写回幽灵 0 引用条目）
                    self._inflight_refs[cache_key] = max(0, refs - 1)
        return await self._dispatch(
            method, args, kwargs, provider,
            effective_strict, effective_timeout, None,
        )

    # ── Provider chain dispatch ────────────────────────────────────

    async def _dispatch(
        self,
        method: str,
        args: tuple,
        kwargs: dict,
        provider: str | None,
        strict: bool,
        timeout: float,
        cache_key: str | None,
    ) -> Any:
        """遍历 provider chain，按顺序尝试直到成功。"""
        chain = self._get_chain(method, provider)
        attempted: list[str] = []
        details: list[ProviderAttemptDetail] = []
        last_error: BaseException | None = None

        for idx, candidate in enumerate(chain):
            fn = self._method_table[method][candidate]
            stat_key = f"{method}:{candidate}"
            stat = self._stats.setdefault(stat_key, EndpointStats(method=method, provider=candidate))
            is_fallback = idx > 0
            policy = self._get_resilience(method, candidate)
            attempted.append(candidate)
            t0 = time.monotonic()

            try:
                result = (
                    await policy.execute(_run_with_timeout, fn, args, kwargs, timeout)
                    if policy
                    else await _run_with_timeout(fn, args, kwargs, timeout)
                )
                latency = (time.monotonic() - t0) * 1000.0

                # 质量校验
                ok, reason = self._check_quality(method, result)
                if not ok:
                    stat.record(success=False, latency_ms=latency, error=reason, error_type="QualityFail", is_fallback=is_fallback)
                    # 健康追踪：质量失败也计入（否则成功率统计漏记该类失败）
                    try:
                        get_tracker().record(
                            source=candidate, method=method, status="failed",
                            latency_ms=latency, error_type="QualityFail",
                        )
                    except Exception:
                        pass
                    last_error = RuntimeError(f"quality: {reason}")
                    details.append(ProviderAttemptDetail.from_exception(
                        method=method, provider=candidate, exc=last_error,
                        latency_ms=latency, started_at=time.time(),
                    ))
                    continue

                stat.record(success=True, latency_ms=latency, is_fallback=is_fallback)
                if cache_key:
                    self._cache.set(cache_key, result, ttl=self.cache_ttl)
                # 异步写调用记录
                self._fire_log(method, args, kwargs, result, latency, False, cache_key, provider_used=candidate)
                # 健康追踪（连续运行验收用）
                try:
                    get_tracker().record(source=candidate, method=method, status="ok", latency_ms=latency)
                except Exception:
                    pass
                return result

            except Exception as exc:
                latency = (time.monotonic() - t0) * 1000.0
                strategy = getattr(exc, "strategy", None) if isinstance(exc, ResilienceError) else None
                attempts = int(getattr(exc, "attempts", 0) or 0) if isinstance(exc, ResilienceError) else 0
                # 解包韧性异常，让统计/日志/错误链拿到真实根因
                root = getattr(exc, "last_error", None) if isinstance(exc, ResilienceError) else None
                root = root or exc
                stat.record(success=False, latency_ms=latency, error=str(root), error_type=type(root).__name__, is_fallback=is_fallback)
                last_error = exc
                details.append(ProviderAttemptDetail.from_exception(
                    method=method, provider=candidate, exc=root,
                    latency_ms=latency, started_at=time.time(),
                    resilience_strategy=strategy, resilience_attempts=attempts,
                ))
                logger.warning("路由失败: method=%s provider=%s fallback=%s err=%s", method, candidate, is_fallback, root)
                # 健康追踪（连续运行验收用）
                try:
                    get_tracker().record(
                        source=candidate, method=method, status="failed",
                        latency_ms=latency, error_type=type(root).__name__,
                    )
                except Exception:
                    pass
                if strict:
                    raise DataFetchError(method, attempted, last_error, attempted_details=details) from exc

        raise DataFetchError(method, attempted, last_error, attempted_details=details) from last_error

    # ── 查询 API ──────────────────────────────────────────────────

    def available_methods(self) -> list[str]:
        return sorted(self._method_table.keys())

    def register_method(self, method: str, provider: str, attr: str | None = None) -> None:
        """运行时动态注册新 method。"""
        if provider not in self.providers:
            raise KeyError(f"未知 provider: {provider}")
        attr_name = attr or method
        fn = getattr(self.providers[provider], attr_name, None)
        if fn is None:
            raise AttributeError(f"provider '{provider}' 无属性 '{attr_name}'")
        self._method_table.setdefault(method, {})[provider] = fn
        key = f"{method}:{provider}"
        if key not in self._stats:
            self._stats[key] = EndpointStats(method=method, provider=provider)

    def health(self) -> dict[str, Any]:
        """返回所有端点健康度。"""
        endpoints = [s.to_dict() for s in self._stats.values()]
        total = sum(s["total"] for s in endpoints)
        success = sum(s["success"] for s in endpoints)
        return {
            "router": "DataSource",
            "providers": list(self.providers.keys()),
            "endpoints_total": len(endpoints),
            "calls_total": total,
            "calls_success": success,
            "calls_success_rate": round(success / total, 3) if total else 0.0,
            "cache": self._cache.stats().to_dict(),
            "endpoints": endpoints,
        }

    def reset_stats(self) -> None:
        for stat in self._stats.values():
            stat.total = 0
            stat.success = 0
            stat.failure = 0
            stat.fallback_count = 0
            stat.last_error = ""
            stat.last_status = "idle"
            stat.last_called_at = 0.0
            stat.latencies_ms.clear()
            stat.error_tracker.reset()

    def clear_cache(self) -> None:
        self._cache.clear()

    def cache_stats(self) -> CacheStats:
        return self._cache.stats()

    # ── 韧性 ──────────────────────────────────────────────────────

    def set_resilience(self, method: str, provider: str, policy: ResiliencePolicy | None) -> None:
        key = f"{method}:{provider}"
        if policy is None:
            self._resilience.pop(key, None)
        else:
            self._resilience[key] = policy

    # ── 内部 ──────────────────────────────────────────────────────

    def _get_chain(self, method: str, provider: str | None) -> list[str]:
        providers = self._method_table[method]
        if provider:
            if provider not in providers:
                raise ValueError(f"method {method} 不支持 provider {provider}")
            return [provider]
        candidates = list(providers.keys())
        if len(candidates) <= 1:
            return candidates
        # 自适应排序：有健康记录的 provider 按（成功率降序、均延迟升序）前置，
        # 无记录的保持声明顺序置后 → 大幅减少 fallback 等待时间
        def _key(name: str) -> tuple[int, float, float, int]:
            st = self._stats.get(f"{method}:{name}")
            if st is None or st.total == 0:
                return (1, 0.0, 0.0, order[name])  # 无记录：置后但保持原相对顺序
            return (0, -st.success_rate, st.avg_latency_ms, order[name])
        order = {name: i for i, name in enumerate(candidates)}
        return sorted(candidates, key=_key)

    def _get_resilience(self, method: str, provider: str) -> ResiliencePolicy | None:
        key = f"{method}:{provider}"
        explicit = self._resilience.get(key)
        if explicit is not None:
            return explicit
        if self._default_resilience is not None:
            # 每 provider 独立策略：限流/熔断互不串扰，避免单 provider 故障拖死全局配额
            policy = self._default_provider_policies.get(provider)
            if policy is None:
                policy = self._build_default_policy()
                self._default_provider_policies[provider] = policy
            return policy
        return None

    def _build_cache_key(self, method: str, args: tuple, kwargs: dict, provider: str | None = None) -> str:
        normalized = {"method": method, "provider": provider or "", "args": list(args), "kwargs": sorted(kwargs.items())}
        payload = json.dumps(normalized, default=str, sort_keys=True, ensure_ascii=False)
        return f"{method}|{hashlib.sha256(payload.encode()).hexdigest()[:16]}"

    def _check_quality(self, method: str, result: Any) -> tuple[bool, str]:
        """结果质量校验（两级语义）。

        - 通用：拒绝 None（provider 内部异常但未抛错的典型表现）。
        - 选择器类方法（_EMPTY_AS_FAILURE_METHODS）：空结果=失败，
          继续 fallback 到下一 provider——避免“空即成功”短路多源链。
          事件类方法空 list/dict 仍是合法响应（如“今日无新闻”）。
        """
        if not self._quality_check:
            return True, ""
        if result is None:
            return False, "result is None"
        if method in _EMPTY_AS_FAILURE_METHODS and _is_empty_result(result):
            return False, f"empty result for selector method {method!r}"
        return True, ""

    def _fire_log(
        self,
        method: str,
        args: tuple,
        kwargs: dict,
        result: Any,
        latency_ms: float,
        cache_hit: bool,
        cache_key: str | None,
        provider_used: str | None = None,
    ) -> None:
        """异步写调用记录到 PG（不阻塞主流程）。"""
        try:
            # 确定 source_key：用 provider_used 或 method_table 的第一个 provider
            source_key = provider_used or ""
            if not source_key and method in self._method_table:
                provs = list(self._method_table[method].keys())
                source_key = provs[0] if provs else "unknown"

            params = {"args": [str(a)[:100] for a in args[:5]], "kwargs": {k: str(v)[:100] for k, v in list(kwargs.items())[:5]}}
            enqueue_call_log(
                source_key=source_key,
                method_name=method,
                caller="system",
                params=params,
                response_preview=result,
                status_code=200,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
                cache_key=cache_key,
            )
        except Exception:
            pass  # 记录失败不影响主流程
