"""services/health_alert — 运维告警服务（P3.5）。

设计：
- `AlertRule`       阈值规则：name / level / check(value) / describe
- `AlertChannel`    投递通道：webhook / log / noop（开发用）
- `AlertEvent`      一次触发的告警
- `HealthAlertService`  单例，周期性 evaluate + dispatch
  - 内置规则集：data_router 失败率、cache 命中率、LLM 失败率、HTTP 5xx
  - 评估输入：从 Prometheus REGISTRY 拉 metric samples
  - 触发后：通过 channels 投递；写入历史

触发模型：
- on_demand：`evaluate_now()` 立即检查（手动 / 路由）
- periodic：可选 `start_periodic(interval_s=60)` 用 thread 起后台循环

不依赖：APScheduler（避免与现有 scheduler 冲突，用 threading.Event 即可）
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from core.metrics import (
    ALERTS_DELIVERY,
    ALERTS_TRIGGERED,
)
from core.metrics import REGISTRY as APP_REGISTRY

logger = logging.getLogger(__name__)


# ── 告警事件 ───────────────────────────────────────


@dataclass
class AlertEvent:
    event_id: str
    rule: str
    level: str  # info / warn / error / critical
    message: str
    value: float
    threshold: float
    timestamp: str  # ISO8601 UTC
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── 规则 ────────────────────────────────────────


class AlertRule:
    """阈值规则。

    `check(samples) -> float | None`：取一个代表值；None = 跳过。
    `should_alert(value) -> bool`         ：是否触发。
    """

    def __init__(
        self,
        name: str,
        level: str,
        threshold: float,
        *,
        comparator: str = "gte",  # gte / lte / gt / lt / eq
        description: str = "",
    ) -> None:
        self.name = name
        self.level = level
        self.threshold = threshold
        self.comparator = comparator
        self.description = description

    def check(self, samples: dict[str, float]) -> float | None:
        """取本规则关心的指标值。子类或闭包覆盖。"""
        raise NotImplementedError

    def should_alert(self, value: float) -> bool:
        if self.comparator == "gte":
            return value >= self.threshold
        if self.comparator == "gt":
            return value > self.threshold
        if self.comparator == "lte":
            return value <= self.threshold
        if self.comparator == "lt":
            return value < self.threshold
        if self.comparator == "eq":
            return value == self.threshold
        return False

    def describe(self, value: float) -> str:
        op = {"gte": ">=", "gt": ">", "lte": "<=", "lt": "<", "eq": "=="}.get(
            self.comparator, ">=",
        )
        return f"{self.name}: {value:.4f} {op} {self.threshold:.4f}"


class _DataRouterFallbackRate(AlertRule):
    """DataRouter fallback 率（fallbacks / calls）超过阈值。"""

    def __init__(self, threshold: float = 0.20, level: str = "warn") -> None:
        super().__init__(
            name="data_router_fallback_rate",
            level=level,
            threshold=threshold,
            comparator="gte",
            description="DataRouter 降级比例（fallback_count / call_count）",
        )

    def check(self, samples: dict[str, float]) -> float | None:
        fb = samples.get("ashare_data_router_fallbacks_total")
        calls = samples.get("ashare_data_router_calls_total")
        if calls is None or calls <= 0:
            return None
        if fb is None:
            return 0.0
        return fb / calls


class _CacheHitRatio(AlertRule):
    """缓存命中率低于阈值。"""

    def __init__(self, threshold: float = 0.30, level: str = "warn") -> None:
        super().__init__(
            name="cache_hit_ratio",
            level=level,
            threshold=threshold,
            comparator="lt",
            description="缓存命中率（hits / (hits + misses)）",
        )

    def check(self, samples: dict[str, float]) -> float | None:
        hits = samples.get("ashare_cache_hits")
        misses = samples.get("ashare_cache_misses")
        if hits is None and misses is None:
            return None
        h = hits or 0.0
        m = misses or 0.0
        denom = h + m
        if denom <= 0:
            return None
        return h / denom


class _LLMFailureRate(AlertRule):
    """LLM 失败率（status=error 比例）超过阈值。"""

    def __init__(self, threshold: float = 0.10, level: str = "warn") -> None:
        super().__init__(
            name="llm_failure_rate",
            level=level,
            threshold=threshold,
            comparator="gte",
            description="LLM 调用失败率（status=error / total）",
        )

    def check(self, samples: dict[str, float]) -> float | None:
        """LLM 失败率 = status=error / total。

        通过 collect_metric_with_labels 拉带 label 的样本，分母用全部 status 之和，
        分子用 status=error 的 value。
        """
        rows = collect_metric_with_labels("ashare_llm_calls_total")
        if not rows:
            return None
        total = sum(r["value"] for r in rows)
        if total <= 0:
            return None
        err = sum(r["value"] for r in rows if r["labels"].get("status") == "error")
        return err / total


class _HTTP5xxRate(AlertRule):
    """HTTP 5xx 比例超过阈值。"""

    def __init__(self, threshold: float = 0.05, level: str = "error") -> None:
        super().__init__(
            name="http_5xx_rate",
            level=level,
            threshold=threshold,
            comparator="gte",
            description="HTTP 5xx 错误率（5xx / total）",
        )

    def check(self, samples: dict[str, float]) -> float | None:
        """HTTP 5xx 错误率 = status 5xx / total。

        用 collect_metric_with_labels 按 status label 拆分。
        """
        rows = collect_metric_with_labels("ashare_http_requests_total")
        if not rows:
            return None
        total = sum(r["value"] for r in rows)
        if total <= 0:
            return None
        err5xx = sum(
            r["value"] for r in rows
            if r["labels"].get("status", "").startswith("5")
        )
        return err5xx / total


# ── 通道 ────────────────────────────────────────


class AlertChannel:
    """投递通道基类。"""

    name: str = "base"

    def send(self, event: AlertEvent) -> bool:
        raise NotImplementedError


class LogChannel(AlertChannel):
    """日志通道（默认；always-on）。"""

    name = "log"

    def send(self, event: AlertEvent) -> bool:
        log_fn = {
            "info": logger.info, "warn": logger.warning,
            "error": logger.error, "critical": logger.critical,
        }.get(event.level, logger.warning)
        log_fn("[%s] %s — %s", event.level.upper(), event.rule, event.message)
        return True


class WebhookChannel(AlertChannel):
    """通用 webhook 通道（POST JSON）。"""

    def __init__(self, name: str, url: str, *, headers: dict[str, str] | None = None, timeout: float = 5.0) -> None:
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout

    def send(self, event: AlertEvent) -> bool:
        payload = event.to_dict()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(self.url, json=payload, headers=self.headers)
                ok = 200 <= r.status_code < 300
                ALERTS_DELIVERY.labels(channel=self.name, status="ok" if ok else "fail").inc()
                if not ok:
                    logger.warning("webhook %s 返回 %d", self.name, r.status_code)
                return ok
        except Exception as exc:
            logger.warning("webhook %s 投递失败：%s", self.name, exc)
            ALERTS_DELIVERY.labels(channel=self.name, status="fail").inc()
            return False


class NoopChannel(AlertChannel):
    """仅记录不投递（测试用）。"""

    name = "noop"

    def __init__(self) -> None:
        self.sent: list[AlertEvent] = []

    def send(self, event: AlertEvent) -> bool:
        self.sent.append(event)
        return True


# ── 服务 ────────────────────────────────────────


def collect_prometheus_samples() -> dict[str, float]:
    """从 APP_REGISTRY 拉所有 sample，key = metric name（去 label，累加 value）。

    适用：不在意 label 维度的规则（如"全部 cache 的命中率"）。
    """
    out: dict[str, float] = {}
    for fam in APP_REGISTRY.collect():
        for sample in fam.samples:
            out[sample.name] = out.get(sample.name, 0.0) + float(sample.value)
    return out


def collect_metric_with_labels(metric_name: str) -> list[dict[str, Any]]:
    """拉指定 metric 的所有 sample（含 labels）。

    适用：需要按 label 拆分的规则（如"LLM 失败率 = status=error / total"）。
    改原 collect_prometheus_samples 是按"metric 名聚合"的语义模糊，
    这里显式区分：只看一个 metric，按 labels 维度展开。

    Returns:
        list of {"name": str, "labels": dict[str, str], "value": float}
    """
    out: list[dict[str, Any]] = []
    for fam in APP_REGISTRY.collect():
        # fam.name 是原始名（Counter / Gauge），sample.name 是带后缀的（_total/_sum/_count/_bucket）
        for sample in fam.samples:
            if sample.name != metric_name:
                continue
            out.append({
                "name": sample.name,
                "labels": dict(sample.labels),
                "value": float(sample.value),
            })
    return out


class HealthAlertService:
    """告警服务单例。

    使用：
        svc = HealthAlertService.get()
        svc.add_channel(WebhookChannel("ops", "https://hooks.example/..."))
        ev = svc.evaluate_now()        # 手动评估，返回新触发的 events
        svc.start_periodic(interval_s=60)
    """

    _instance: HealthAlertService | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._rules: list[AlertRule] = []
        self._channels: list[AlertChannel] = [LogChannel(), NoopChannel()]
        self._history: list[AlertEvent] = []
        self._history_max = 1000
        self._last_trigger_at: dict[str, float] = {}  # rule -> ts（去抖）
        self._dedup_window_s = 60.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._init_default_rules()

    @classmethod
    def get(cls) -> HealthAlertService:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:  # 测试用
        with cls._lock:
            if cls._instance is not None and cls._instance._thread is not None:
                cls._instance._stop_event.set()
                cls._instance._thread.join(timeout=2.0)
            cls._instance = None

    def _init_default_rules(self) -> None:
        """内置规则集（全部真实可触发 — 改 fetch 的实现，不再 return None）。"""
        self._rules = [
            _DataRouterFallbackRate(threshold=0.20, level="warn"),
            _CacheHitRatio(threshold=0.30, level="warn"),
            _LLMFailureRate(threshold=0.10, level="warn"),
            _HTTP5xxRate(threshold=0.05, level="error"),
        ]
        self._inactive: set[str] = set()  # 软删的 rule name

    # ── 注册 ────────────────────────────────────

    def add_rule(self, rule: AlertRule) -> None:
        self._rules.append(rule)
        # 从 _inactive 移除（重新启用）
        self._inactive.discard(rule.name)

    def add_channel(self, channel: AlertChannel) -> None:
        # 重复名替换
        self._channels = [c for c in self._channels if c.name != channel.name]
        self._channels.append(channel)

    def list_rules(self) -> list[dict[str, Any]]:
        return [
            {
                "name": r.name,
                "level": r.level,
                "threshold": r.threshold,
                "comparator": r.comparator,
                "description": r.description,
                "is_active": r.name not in self._inactive,
            }
            for r in self._rules
        ]

    def list_channels(self) -> list[dict[str, Any]]:
        return [{"name": c.name, "type": type(c).__name__} for c in self._channels]

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._history[-limit:]]

    # ── P1-T5 在线编辑 ───────────────────────

    def update_rule(
        self,
        name: str,
        *,
        threshold: float | None = None,
        level: str | None = None,
        comparator: str | None = None,
    ) -> dict[str, Any] | None:
        """改 in-memory 规则的 threshold/level/comparator。

        Args:
            name: 规则名（必填）
            threshold: 新阈值（None=不动）
            level: 新级别（None=不动）
            comparator: 新比较器（None=不动）

        Returns:
            改完的 dict（list_rules 同 schema）；name 不存在 → None
        """
        valid_comparators = {"gte", "gt", "lte", "lt", "eq"}
        valid_levels = {"info", "warn", "error"}
        for r in self._rules:
            if r.name == name:
                if threshold is not None:
                    r.threshold = float(threshold)
                if level is not None:
                    if level not in valid_levels:
                        raise ValueError(f"非法 level：{level}（{valid_levels}）")
                    r.level = level
                if comparator is not None:
                    if comparator not in valid_comparators:
                        raise ValueError(f"非法 comparator：{comparator}（{valid_comparators}）")
                    r.comparator = comparator
                # 重新启用（如果之前被软删）
                self._inactive.discard(name)
                logger.info(
                    "[health_alert] update_rule: name=%s threshold=%s level=%s comparator=%s",
                    name, r.threshold, r.level, r.comparator,
                )
                return {
                    "name": r.name,
                    "level": r.level,
                    "threshold": r.threshold,
                    "comparator": r.comparator,
                    "description": r.description,
                    "is_active": r.name not in self._inactive,
                }
        return None

    def soft_delete_rule(self, name: str) -> bool:
        """软删：标记 _inactive，下次 evaluate_now 跳过。返回是否真有规则被改。"""
        for r in self._rules:
            if r.name == name:
                if name in self._inactive:
                    return False
                self._inactive.add(name)
                logger.info("[health_alert] soft_delete_rule: name=%s", name)
                return True
        return False

    def is_rule_active(self, name: str) -> bool:
        return name in self._rules and name not in self._inactive

    # ── 评估 / 触发 ───────────────────────────

    def evaluate_now(self) -> list[AlertEvent]:
        samples = collect_prometheus_samples()
        triggered: list[AlertEvent] = []
        now_ts = time.time()
        for rule in self._rules:
            # P1-T5: 软删的 rule 跳过
            if rule.name in self._inactive:
                continue
            try:
                value = rule.check(samples)
            except Exception as exc:
                logger.warning("rule %s 评估失败：%s", rule.name, exc)
                continue
            if value is None:
                continue
            if not rule.should_alert(value):
                continue
            # 去抖
            last = self._last_trigger_at.get(rule.name, 0)
            if now_ts - last < self._dedup_window_s:
                continue
            self._last_trigger_at[rule.name] = now_ts
            event = AlertEvent(
                event_id=uuid.uuid4().hex,
                rule=rule.name,
                level=rule.level,
                message=rule.describe(value),
                value=value,
                threshold=rule.threshold,
                timestamp=datetime.now(timezone.utc).isoformat(),
                metadata={"comparator": rule.comparator, "description": rule.description},
            )
            triggered.append(event)
            ALERTS_TRIGGERED.labels(level=rule.level, rule=rule.name).inc()
            self._dispatch(event)
        return triggered

    def _dispatch(self, event: AlertEvent) -> None:
        # 写历史
        self._history.append(event)
        if len(self._history) > self._history_max:
            self._history = self._history[-self._history_max:]
        # 投递
        for ch in self._channels:
            try:
                ch.send(event)
            except Exception as exc:
                logger.warning("channel %s send 异常：%s", ch.name, exc)
                ALERTS_DELIVERY.labels(channel=ch.name, status="fail").inc()

    # ── 周期性后台 ───────────────────────────

    def start_periodic(self, interval_s: float = 60.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                try:
                    self.evaluate_now()
                except Exception as exc:
                    logger.warning("alert evaluate 异常：%s", exc)
                self._stop_event.wait(interval_s)

        self._thread = threading.Thread(target=_loop, name="health-alert", daemon=True)
        self._thread.start()
        logger.info("HealthAlertService 周期评估已启动 interval_s=%.1f", interval_s)

    def stop_periodic(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None


# ── 工厂：创建默认 webhook（从 env 读配置）─────────


def channels_from_env() -> list[AlertChannel]:
    """从 env 读 ALERT_WEBHOOK_URL（多个用逗号分隔）创建 webhook channels。"""
    import os
    out: list[AlertChannel] = []
    raw = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if not raw:
        return out
    for i, url in enumerate(raw.split(",")):
        url = url.strip()
        if not url:
            continue
        out.append(WebhookChannel(name=f"webhook-{i + 1}", url=url))
    return out
