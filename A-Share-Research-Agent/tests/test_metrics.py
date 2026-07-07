"""test_metrics — P3.4 Prometheus 指标 + tracer 单测。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.middleware import _normalize_path
from core.metrics import (
    ALERTS_TRIGGERED,
    APP_INFO,
    BACKTEST_RUNS,
    CACHE_HIT_RATIO,
    CACHE_OPS,
    DATA_ROUTER_CALLS,
    DATA_ROUTER_FALLBACKS,
    DECISION_FALLBACKS,
    HTTP_REQUEST_DURATION,
    HTTP_REQUEST_ERRORS,
    HTTP_REQUEST_TOTAL,
    LLM_CALLS,
    REGISTRY,
)
from core.telemetry import Span, Tracer, current_span, current_trace_id, trace

# ── _normalize_path ─────────────────────────────────


def test_normalize_path_strips_query() -> None:
    assert _normalize_path("/research/run?date=2025-01-01") == "/research/run"


def test_normalize_path_replaces_uuid() -> None:
    assert _normalize_path("/runs/12345678-1234-1234-1234-123456789012") == "/runs/{id}"


def test_normalize_path_replaces_long_hex() -> None:
    assert _normalize_path("/jobs/abcdef0123456789abcd") == "/jobs/{hex}"


def test_normalize_path_root() -> None:
    assert _normalize_path("/") == "/"


# ── Tracer ────────────────────────────────────────


def test_tracer_singleton() -> None:
    t1 = Tracer.get()
    t2 = Tracer.get()
    assert t1 is t2


def test_trace_root_span_has_no_parent() -> None:
    t = Tracer.get()
    t.clear()
    with trace("test.root") as s:
        assert isinstance(s, Span)
        assert s.parent_id is None
        assert s.trace_id  # 非空


def test_trace_nested_shares_trace_id() -> None:
    t = Tracer.get()
    t.clear()
    with trace("test.outer") as outer:
        with trace("test.inner") as inner:
            assert inner.parent_id == outer.span_id
            assert inner.trace_id == outer.trace_id


def test_trace_records_attributes() -> None:
    t = Tracer.get()
    t.clear()
    with trace("test.attrs", foo="bar", n=42) as s:
        s.set_attribute("dyn", "value")
    completed = t.completed()
    assert completed[0]["attributes"] == {"foo": "bar", "n": 42, "dyn": "value"}


def test_trace_marks_error_on_exception() -> None:
    t = Tracer.get()
    t.clear()
    try:
        with trace("test.boom"):
            raise ValueError("oops")
    except ValueError:
        pass
    spans = t.completed()
    assert spans[0]["status"] == "error"
    assert "oops" in spans[0]["error"]


def test_current_span_in_context() -> None:
    with trace("test.cs") as s:
        assert current_span() is s
        assert current_trace_id() == s.trace_id
    assert current_span() is None
    assert current_trace_id() is None


# ── Prometheus 指标 ─────────────────────────────


def test_metrics_registry_has_known_metrics() -> None:
    """检查所有指标都被注册（Counter 必须先 inc() 才会 collect 到）。"""
    # 先确保每个指标至少被初始化一次
    HTTP_REQUEST_TOTAL.labels(method="M", path="/P", status="200").inc(0)
    HTTP_REQUEST_ERRORS.labels(method="M", path="/P", status="500").inc(0)
    HTTP_REQUEST_DURATION.labels(method="M", path="/P", status="200").observe(0.001)
    DATA_ROUTER_CALLS.labels(endpoint="e", result="ok").inc(0)
    DATA_ROUTER_FALLBACKS.labels(endpoint="e", reason="r").inc(0)
    BACKTEST_RUNS.labels(status="completed").inc(0)
    CACHE_OPS.labels(backend="b", op="o", result="r").inc(0)
    CACHE_HIT_RATIO.labels(backend="b").set(0.5)
    APP_INFO.labels(version="v", env="e").set(1)
    ALERTS_TRIGGERED.labels(level="warn", rule="test").inc(0)
    DECISION_FALLBACKS.labels(layer="committee", reason="x").inc(0)
    LLM_CALLS.labels(layer="committee", status="ok").inc(0)

    # metric.name 是原始名（不含 _total / _count / _bucket 等 sample 后缀）
    # samples 是实际暴露的 Prometheus 指标
    family_names = set()
    sample_names = set()
    for m in REGISTRY.collect():
        family_names.add(m.name)
        for sample in m.samples:
            sample_names.add(sample.name)
    # Counter 校验（samples 带 _total）
    assert "ashare_http_requests_total" in sample_names
    assert "ashare_http_request_errors_total" in sample_names
    assert "ashare_data_router_calls_total" in sample_names
    assert "ashare_backtest_runs_total" in sample_names
    assert "ashare_alerts_triggered_total" in sample_names
    assert "ashare_decision_fallbacks_total" in sample_names
    assert "ashare_llm_calls_total" in sample_names
    # Histogram / Gauge 校验（用 family 名）
    assert "ashare_http_request_duration_seconds" in family_names
    assert "ashare_backtest_duration_seconds" in family_names
    assert "ashare_app_info" in family_names
    assert "ashare_cache_hit_ratio" in family_names


def test_metrics_basic_increment() -> None:
    HTTP_REQUEST_TOTAL.labels(method="GET", path="/x", status="200").inc()
    HTTP_REQUEST_ERRORS.labels(method="GET", path="/x", status="500").inc()
    DATA_ROUTER_CALLS.labels(endpoint="foo", result="ok").inc()
    CACHE_OPS.labels(backend="redis", op="get", result="hit").inc()
    BACKTEST_RUNS.labels(status="completed").inc()
    APP_INFO.labels(version="test", env="dev").set(1)


def test_prometheus_generate_latest() -> None:
    from prometheus_client import generate_latest
    HTTP_REQUEST_TOTAL.labels(method="GET", path="/generate", status="200").inc()
    text = generate_latest(REGISTRY).decode("utf-8")
    assert "ashare_http_requests_total" in text
    assert "/generate" in text


# ── FastAPI 集成 ───────────────────────────────


def test_metrics_endpoint_returns_prometheus_text() -> None:
    """直接用 app 测 /metrics 端点（不依赖 TestClient lifespan）。"""
    from apps.api.main import app
    # /metrics 自己不计入指标；用 TestClient 触发一些请求
    with TestClient(app) as client:
        # 触发若干请求
        for _ in range(3):
            client.get("/metrics/info")
        r = client.get("/metrics")
        assert r.status_code == 200
        text = r.text
        assert "ashare_http_request_duration_seconds" in text
        # /metrics/info 被命中过
        assert "metrics/info" in text


def test_metrics_traces_endpoint() -> None:
    from apps.api.main import app
    with TestClient(app) as client:
        r = client.get("/metrics/traces")
        assert r.status_code == 200
        data = r.json()
        assert "count" in data
        assert "spans" in data
