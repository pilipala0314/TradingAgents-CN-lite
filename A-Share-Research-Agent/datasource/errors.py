"""datasource.errors — 统一异常体系（自包含，无模块内循环依赖）。

异常层次：
    DataSourceError (base)
    ├── ProviderError       — 单 provider 内部错误（网络/解析/接口缺失）
    ├── DataFetchError      — 路由层：所有 provider 均失败后抛出
    └── ResilienceError     — 韧性层：熔断/限流/隔离拒绝
      
ResilienceError 定义在本模块（而非 resilience.base），
保证 datasource.errors 可被任意模块安全导入而不产生循环。
"""
from __future__ import annotations

from typing import Any


class DataSourceError(RuntimeError):
    """datasource 包的基础异常。"""


class ProviderError(DataSourceError):
    """单 Provider 内部错误。

    Provider 方法内部捕获原始异常后，包装为此类型抛出。
    路由层根据此异常决定是否 fallback 到下一个 provider。

    Attributes:
        provider:  provider 名称（如 'akshare'）
        method:    调用的方法名（如 'get_daily_bars'）
        cause:     原始异常（可选）
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        method: str = "",
        cause: BaseException | None = None,
    ) -> None:
        self.provider = provider
        self.method = method
        self.cause = cause
        super().__init__(message)


class NonRetryableProviderError(ProviderError):
    """Provider 参数/逻辑类错误 — 重试与降级均无意义（如参数校验失败）。

    由 provider 内部（如 akshare 的 `_run_with_retry`）抛出；
    路由层与 provider 内部降级逻辑据此快速失败，不做无谓重试/换源。
    """


class ResilienceError(DataSourceError):
    """韧性策略用尽后的统一异常。

    Attributes:
        strategy:   触发异常的策略名（"retry" / "rate_limit" / "circuit" / "bulkhead"）
        attempts:   已经尝试的次数
        last_error: 最后一个底层异常
        context:    诊断上下文
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
        msg = f"[{strategy}] {message}（attempts={attempts}）"
        if last_error is not None:
            # 透出根因，避免底层报错被韧性包装后丢失
            root = str(last_error)[:160]
            msg += f" | 根因: {type(last_error).__name__}: {root}"
        super().__init__(msg)


class DataFetchError(DataSourceError):
    """路由层：所有 provider 均失败后抛出。

    Attributes:
        method:              数据方法名
        attempted_providers: 已尝试的 provider 列表（按调用顺序）
        last_error:          最后一个 provider 的原始异常
        attempted_details:   每个 provider 的结构化诊断列表
    """

    def __init__(
        self,
        method: str,
        attempted_providers: list[str],
        last_error: BaseException | None,
        *,
        attempted_details: list[Any] | None = None,
    ) -> None:
        self.method = method
        self.attempted_providers = attempted_providers
        self.last_error = last_error
        self.attempted_details: list[Any] = attempted_details or []
        super().__init__(
            f"数据路由全部失败: method={method} "
            f"attempted={attempted_providers} last_error={last_error}"
        )

    def summary(self) -> dict[str, Any]:
        """压缩为可序列化 dict（给前端/告警消费）。

        last_error 自动解包韧性包装（retry/circuit/...），透出真实根因；
        韧性策略名与尝试次数保留在 attempted_details 中供诊断。
        """
        root = self.last_error
        while isinstance(root, ResilienceError) and root.last_error is not None:
            root = root.last_error
        return {
            "method": self.method,
            "attempted_providers": list(self.attempted_providers),
            "last_error": str(root) if root else None,
            "last_error_type": type(root).__name__ if root else None,
            "attempted_details": [
                d.to_dict() if hasattr(d, "to_dict") else str(d)
                for d in self.attempted_details
            ],
        }
