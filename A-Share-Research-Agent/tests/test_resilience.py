"""韧性中间件行为测试（无网络，asyncio 直接驱动）。"""
import asyncio

import pytest

from datasource.errors import ResilienceError
from datasource.resilience.base import (
    CircuitBreakerConfig,
    RateLimitConfig,
    RetryConfig,
)
from datasource.resilience.circuit_breaker import CircuitBreaker, CircuitState
from datasource.resilience.policy import ResiliencePolicy
from datasource.resilience.rate_limit import RateLimiter
from datasource.resilience.retry import RetryPolicy


@pytest.mark.unit
class TestRetryPolicy:
    def test_success_first_try(self):
        calls = []

        async def fn():
            calls.append(1)
            return "ok"

        async def main():
            policy = RetryPolicy(RetryConfig(max_attempts=3))
            return await policy.execute(fn)

        assert asyncio.run(main()) == "ok"
        assert len(calls) == 1

    def test_retry_then_success(self):
        calls = []

        async def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("boom")
            return "ok"

        async def main():
            policy = RetryPolicy(RetryConfig(max_attempts=3, initial_delay_sec=0.01))
            return await policy.execute(fn)

        assert asyncio.run(main()) == "ok"
        assert len(calls) == 3

    def test_exhausted_raises_resilience_error(self):
        async def fn():
            raise ValueError("always")

        async def main():
            policy = RetryPolicy(RetryConfig(max_attempts=2, initial_delay_sec=0.01))
            await policy.execute(fn)

        with pytest.raises(ResilienceError):
            asyncio.run(main())

    def test_retry_on_filter(self):
        calls = []

        async def fn():
            calls.append(1)
            raise ValueError("not retryable per filter")

        async def main():
            policy = RetryPolicy(
                RetryConfig(max_attempts=3, retry_on=(ConnectionError,))
            )
            await policy.execute(fn)

        with pytest.raises(ValueError):
            asyncio.run(main())
        assert len(calls) == 1  # 过滤不命中，不重试


class _StatusError(Exception):
    """模拟 httpx.HTTPStatusError：带 response.status_code。"""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.response = type("_Resp", (), {"status_code": status})()


@pytest.mark.unit
class TestRetryCostGuard:
    """成本守护：确定性失败不得重试（每次重试 = 一次付费请求）。"""

    def test_non_retryable_provider_error_not_retried(self):
        from datasource.errors import NonRetryableProviderError

        calls = []

        async def fn():
            calls.append(1)
            raise NonRetryableProviderError("bad param")

        async def main():
            policy = RetryPolicy(RetryConfig(max_attempts=3, initial_delay_sec=0.01))
            await policy.execute(fn)

        with pytest.raises(NonRetryableProviderError):
            asyncio.run(main())
        assert len(calls) == 1  # 不重试 → 不放大付费请求

    def test_http_403_not_retried(self):
        calls = []

        async def fn():
            calls.append(1)
            raise _StatusError(403)

        async def main():
            policy = RetryPolicy(RetryConfig(max_attempts=3, initial_delay_sec=0.01))
            await policy.execute(fn)

        with pytest.raises(_StatusError):
            asyncio.run(main())
        assert len(calls) == 1

    def test_http_429_is_retried(self):
        calls = []

        async def fn():
            calls.append(1)
            if len(calls) < 2:
                raise _StatusError(429)
            return "ok"

        async def main():
            policy = RetryPolicy(RetryConfig(max_attempts=3, initial_delay_sec=0.01))
            return await policy.execute(fn)

        assert asyncio.run(main()) == "ok"
        assert len(calls) == 2  # 429 限流值得换 IP 重试

    def test_cause_chain_detected(self):
        """根因被 ProviderError 包裹时仍需识别（provider 普遍包装异常）。"""
        from datasource.errors import NonRetryableProviderError

        calls = []

        async def fn():
            calls.append(1)
            try:
                raise NonRetryableProviderError("inner")
            except NonRetryableProviderError as inner:
                raise ValueError("wrapped") from inner

        async def main():
            policy = RetryPolicy(RetryConfig(max_attempts=3, initial_delay_sec=0.01))
            await policy.execute(fn)

        with pytest.raises(ValueError):
            asyncio.run(main())
        assert len(calls) == 1

    def test_is_retryable_helper(self):
        from datasource.errors import NonRetryableProviderError
        from datasource.resilience.retry import is_retryable

        assert is_retryable(ConnectionError("transient")) is True
        assert is_retryable(TimeoutError("slow")) is True
        assert is_retryable(_StatusError(500)) is True
        assert is_retryable(_StatusError(429)) is True
        assert is_retryable(_StatusError(408)) is True
        assert is_retryable(_StatusError(403)) is False
        assert is_retryable(_StatusError(404)) is False
        assert is_retryable(NonRetryableProviderError("x")) is False


@pytest.mark.unit
class TestCircuitBreaker:
    def test_opens_after_threshold(self):
        config = CircuitBreakerConfig(failure_threshold=3, window_size=10, recovery_timeout_sec=1)
        cb = CircuitBreaker(config)

        async def fn():
            raise ConnectionError("fail")

        async def main():
            for _ in range(3):
                try:
                    await cb.execute(fn)
                except ConnectionError:
                    pass
            assert cb.state is CircuitState.OPEN
            # OPEN 状态直接拒绝
            with pytest.raises(ResilienceError):
                await cb.execute(fn)

        asyncio.run(main())

    def test_success_keeps_closed(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5))

        async def fn():
            return 1

        async def main():
            for _ in range(10):
                await cb.execute(fn)
            assert cb.state is CircuitState.CLOSED

        asyncio.run(main())

    def test_half_open_single_probe(self):
        """HALF_OPEN 仅放行单个探测请求，其余拒绝；探测成功回到 CLOSED。"""
        # recovery_timeout 设大，避免访问 .state 时立即 OPEN→HALF_OPEN
        config = CircuitBreakerConfig(failure_threshold=2, recovery_timeout_sec=3600, window_size=10)
        cb = CircuitBreaker(config)

        async def fail():
            raise ConnectionError("fail")

        async def succeed():
            return "ok"

        async def main():
            for _ in range(2):
                try:
                    await cb.execute(fail)
                except ConnectionError:
                    pass
            assert cb.state is CircuitState.OPEN
            # 模拟恢复期已到：手工切到 HALF_OPEN
            cb._state = CircuitState.HALF_OPEN
            cb._half_open_probing = False
            # 探测请求成功 → 回到 CLOSED
            await cb.execute(succeed)
            assert cb.state is CircuitState.CLOSED

        asyncio.run(main())


@pytest.mark.unit
class TestRateLimiter:
    def test_execute(self):
        async def fn():
            return 42

        async def main():
            rl = RateLimiter(RateLimitConfig(rps=100, burst=10))
            return await rl.execute(fn)

        assert asyncio.run(main()) == 42

    def test_rps_zero_disables(self):
        async def fn():
            return 1

        async def main():
            rl = RateLimiter(RateLimitConfig(rps=0, burst=0))
            return await rl.execute(fn)

        assert asyncio.run(main()) == 1


@pytest.mark.unit
class TestResiliencePolicy:
    def test_onion_order_executes(self):
        """circuit → retry → rate_limit → bulkhead → fn 全链路。"""
        calls = []

        async def fn():
            calls.append(1)
            return "ok"

        async def main():
            policy = ResiliencePolicy(
                retry=RetryConfig(max_attempts=3, initial_delay_sec=0.01),
                rate_limit=RateLimitConfig(rps=100, burst=10),
                circuit=CircuitBreakerConfig(),
            )
            return await policy.execute(fn)

        assert asyncio.run(main()) == "ok"
        assert calls == [1]
