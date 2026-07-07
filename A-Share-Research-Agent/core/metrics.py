"""core/metrics — Prometheus 指标定义（P3.4）。

集中式指标注册表：
- 业务域指标：HTTP / Cache / DataRouter / Backtest / 告警
- 全部用 `prometheus_client`（轻量级标准库）
- 通过 /metrics 端点暴露

设计要点：
- 指标类名直接当变量名：`HTTP_REQUEST_DURATION`（避免"先 import 再 label"漏改）
- label 用有限枚举（method / path / status），避免高基数（path 含 id 时裁剪）
- 所有 Counter/Histogram 必须用统一前缀 `ashare_`（运维 grep 方便）
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# 独立 registry — 不污染默认 REGISTRY（多进程 / 多 app 隔离）
REGISTRY = CollectorRegistry()

# ── HTTP 层 ─────────────────────────────────────────
HTTP_REQUEST_DURATION = Histogram(
    "ashare_http_request_duration_seconds",
    "HTTP 请求耗时（按 method / path / status）",
    labelnames=("method", "path", "status"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=REGISTRY,
)
HTTP_REQUEST_TOTAL = Counter(
    "ashare_http_requests_total",
    "HTTP 请求总数",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_ERRORS = Counter(
    "ashare_http_request_errors_total",
    "HTTP 4xx/5xx 总数",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)

# ── 缓存层 ─────────────────────────────────────────
CACHE_OPS = Counter(
    "ashare_cache_ops_total",
    "缓存操作总数（按 backend / op / result）",
    labelnames=("backend", "op", "result"),
    registry=REGISTRY,
)
CACHE_HIT_RATIO = Gauge(
    "ashare_cache_hit_ratio",
    "缓存命中率（backend 维度）",
    labelnames=("backend",),
    registry=REGISTRY,
)

# ── DataRouter ─────────────────────────────────────
DATA_ROUTER_CALLS = Counter(
    "ashare_data_router_calls_total",
    "DataRouter 调用次数（按 endpoint / result）",
    labelnames=("endpoint", "result"),
    registry=REGISTRY,
)
DATA_ROUTER_FALLBACKS = Counter(
    "ashare_data_router_fallbacks_total",
    "DataRouter 降级到 fallback 次数",
    labelnames=("endpoint", "reason"),
    registry=REGISTRY,
)
DATA_ROUTER_LATENCY = Histogram(
    "ashare_data_router_latency_seconds",
    "DataRouter 调用耗时",
    labelnames=("endpoint",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    registry=REGISTRY,
)
DATA_ROUTER_HEALTH_SCORE = Gauge(
    "ashare_data_router_health_score",
    "DataRouter 端点健康分（0~1）",
    labelnames=("endpoint",),
    registry=REGISTRY,
)

# ── 回测 ───────────────────────────────────────────
BACKTEST_RUNS = Counter(
    "ashare_backtest_runs_total",
    "回测执行次数（按 status）",
    labelnames=("status",),
    registry=REGISTRY,
)
BACKTEST_LATENCY = Histogram(
    "ashare_backtest_duration_seconds",
    "回测执行耗时（按 strategy）",
    labelnames=("strategy",),
    buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 300),
    registry=REGISTRY,
)
BACKTEST_IN_FLIGHT = Gauge(
    "ashare_backtest_in_flight",
    "正在运行的回测数（async 模式）",
    registry=REGISTRY,
)

# ── 告警 ───────────────────────────────────────────
ALERTS_TRIGGERED = Counter(
    "ashare_alerts_triggered_total",
    "触发的告警次数（按 level / rule）",
    labelnames=("level", "rule"),
    registry=REGISTRY,
)
ALERTS_DELIVERY = Counter(
    "ashare_alerts_delivery_total",
    "告警投递结果（按 channel / status）",
    labelnames=("channel", "status"),
    registry=REGISTRY,
)

# ── 决策 / Agent ───────────────────────────────────
DECISION_FALLBACKS = Counter(
    "ashare_decision_fallbacks_total",
    "决策回退到规则 baseline 次数（按 layer / reason）",
    labelnames=("layer", "reason"),
    registry=REGISTRY,
)
LLM_CALLS = Counter(
    "ashare_llm_calls_total",
    "LLM 调用次数（按 layer / status）",
    labelnames=("layer", "status"),
    registry=REGISTRY,
)
LLM_LATENCY = Histogram(
    "ashare_llm_latency_seconds",
    "LLM 调用耗时",
    labelnames=("layer",),
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60),
    registry=REGISTRY,
)

# ── 健康 / Build 信息 ──────────────────────────────
APP_INFO = Gauge(
    "ashare_app_info",
    "App 元信息（恒为 1，label 携带版本/构建）",
    labelnames=("version", "env"),
    registry=REGISTRY,
)
