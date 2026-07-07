"""Dashboard router 单测。

覆盖：
- /api/dashboard/overview 返 health + global_quotes（best-effort，global_quotes 可空但 health 必有）
- /api/dashboard/stock/{code} 返 quote / bars / announcements / errors
- / 返 200 + HTML
- /static/index.html /style.css /dashboard.js 返 200
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_root_returns_dashboard_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "A 股投研 Dashboard" in r.text


def test_static_index_html(client: TestClient) -> None:
    r = client.get("/static/index.html")
    assert r.status_code == 200
    assert "A 股投研 Dashboard" in r.text


def test_static_style_css(client: TestClient) -> None:
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert "--bg" in r.text


def test_static_dashboard_js(client: TestClient) -> None:
    r = client.get("/static/dashboard.js")
    assert r.status_code == 200
    assert "loadOverview" in r.text


def test_overview_returns_health_and_global_quotes(client: TestClient) -> None:
    r = client.get("/api/dashboard/overview")
    assert r.status_code == 200
    data = r.json()
    assert "health" in data
    h = data["health"]
    # health 必有 providers / endpoints_total / cache
    assert "providers" in h
    assert "endpoints_total" in h
    assert "cache" in h
    # global_quotes 允许空 dict（网络断时不阻断 overview）
    assert "global_quotes" in data
    assert isinstance(data["global_quotes"], dict)


def test_stock_detail_structure(client: TestClient) -> None:
    r = client.get("/api/dashboard/stock/000001")
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == "000001"
    assert "quote" in data
    assert "bars" in data
    assert "announcements" in data
    assert "errors" in data


def test_stock_detail_handles_invalid_code(client: TestClient) -> None:
    """非法 code → 不抛 500，errors 字段承载错误信息。"""
    r = client.get("/api/dashboard/stock/000000")
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == "000000"
    # 至少 errors 或 quote / bars 有一个有内容
    assert "errors" in data


def test_health_detail_returns_endpoint_rows_with_alerts(client: TestClient) -> None:
    """/api/dashboard/health 返 endpoints + alerts 字段。"""
    r = client.get("/api/dashboard/health")
    assert r.status_code == 200
    data = r.json()
    assert "endpoints" in data
    assert "alerts" in data
    assert "providers" in data
    assert "endpoints_total" in data
    # alerts 字段必有 3 个键
    assert set(data["alerts"].keys()) >= {"low_success_rate", "last_error", "high_latency"}
    # endpoint 行字段
    if data["endpoints"]:
        row = data["endpoints"][0]
        assert "method" in row
        assert "provider" in row
        assert "success" in row
        assert "failure" in row
        assert "success_rate" in row
        assert "p95_latency_ms" in row
        assert "fallback_count" in row
        assert "last_error" in row
        assert "last_status" in row
        assert "last_called_at" in row
        assert "request_sample" in row
        assert "response_sample" in row
        assert "error_sample" in row
        assert "alerts" in row


def test_health_detail_alert_classification(client: TestClient) -> None:
    """告警规则：success_rate < 0.5 → low_success_rate；last_error 非空 → last_error。"""
    r = client.get("/api/dashboard/health")
    assert r.status_code == 200
    data = r.json()
    for row in data["endpoints"]:
        if row["total"] > 0 and row["success_rate"] < 0.5:
            assert "low_success_rate" in row["alerts"]
        if row["last_error"]:
            assert "last_error" in row["alerts"]
        if row["p95_latency_ms"] > 3000:
            assert "high_latency" in row["alerts"]


def test_compare_returns_series_and_errors(client: TestClient) -> None:
    """compare 端点返 codes / series / errors 字段。"""
    r = client.get("/api/dashboard/compare", params={"codes": "600519,000001"})
    assert r.status_code == 200
    data = r.json()
    assert data["codes"] == ["600519", "000001"]
    assert "series" in data
    assert "errors" in data
    # series 必含 2 个 key
    assert set(data["series"].keys()) == {"600519", "000001"}


def test_compare_too_many_codes(client: TestClient) -> None:
    """超过 5 个 code → 截断到 5 个 + errors 提示。"""
    r = client.get(
        "/api/dashboard/compare",
        params={"codes": "600519,000001,600036,000636,000002,999999"},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["codes"]) == 5
    assert "最多 5 个" in data["errors"].get("_", "")


def test_compare_invalid_code_format(client: TestClient) -> None:
    """非 6 位数字 code → errors 提示，不抛 500。"""
    r = client.get("/api/dashboard/compare", params={"codes": "abc,12345"})
    assert r.status_code == 200
    data = r.json()
    assert "code 必须 6 位数字" in data["errors"].get("_", "")


def test_compare_empty_codes(client: TestClient) -> None:
    """空 codes → errors 提示。"""
    r = client.get("/api/dashboard/compare", params={"codes": "  ,  "})
    assert r.status_code == 200
    data = r.json()
    assert data["codes"] == []
    assert "codes 必填" in data["errors"].get("_", "")


# ── P6: 今日信号 tab 渲染 ────────────────────────────────────────
def test_signals_tab_present_in_index(client: TestClient) -> None:
    """index.html 必含 '今日信号' tab 按钮 + 信号/审计区关键 section id。"""
    r = client.get("/")
    assert r.status_code == 200
    text = r.text
    # tab 按钮
    assert "今日信号" in text
    assert 'data-tab="signals"' in text
    # 关键 section id
    for sec_id in (
        "signals-market",
        "signals-sector",
        "signals-approved",
        "signals-blocked",
        "signals-audit-summary",
        "signals-audit-meta",
        "signals-global-trace-groupby",
        "signals-global-run-sort",
        "signals-global-anomaly-only",
        "signals-toggle-all",
        "signals-workbench-summary",
        "signals-workbench-alerts",
        "signals-trace-list",
        "signals-trace-detail",
        "signals-trace-groupby",
        "signals-trace-group-summary",
        "signals-trace-group-chart",
        "signals-consistency-summary",
        "signals-consistency-issues",
        "signals-linked-backtests",
        "signals-audit-bars",
        "signals-audit-matrix",
        "signals-audit-timeline",
        "signals-fallback-summary",
        "signals-trace-diff-matrix",
        "signals-drilldown-chain",
        "signals-approved-chart",
        "signals-blocked-chart",
        "signals-drilldown-notes",
        "signals-evidence-map",
        "signals-run-recommendation",
        "signals-stock-review",
        "signals-segment-review",
        "signals-trace-compare",
        "signals-stock-diff",
        "signals-run-rank-diff",
        "signals-segment-compare",
        "signals-trace-attribution-bridge",
        "signals-backtest-attribution",
        "signals-attribution-chart",
        "signals-runs-compare-chart",
        "signals-run-filter-completed",
        "signals-run-filter-attribution",
        "signals-run-sort",
        "signals-runs-compare-summary",
        "signals-runs-peer-summary",
        "signals-backtest-modules",
        "signals-module-rebalance-chart",
        "signals-module-scoreic-chart",
        "signals-module-holdings-chart",
        "signals-attribution-module-details",
        "signals-backtest-attribution-raw",
    ):
        assert sec_id in text


def test_dashboard_js_includes_signals_handlers(client: TestClient) -> None:
    """dashboard.js 必含 signals + decision trace 审计处理函数。"""
    r = client.get("/static/dashboard.js")
    assert r.status_code == 200
    text = r.text
    assert "loadSignals" in text
    assert "loadDecisionTraces" in text
    assert "loadResearchAudit" in text
    assert "_renderSignalsAuditSummary" in text
    assert "_renderDecisionTraceDetail" in text
    assert "_renderResearchAudit" in text
    assert "_renderAuditBars" in text
    assert "_renderAuditMatrix" in text
    assert "_renderAuditTimeline" in text
    assert "_renderFallbackSummary" in text
    assert "_renderTraceDiffMatrix" in text
    assert "_renderTraceGroupCompare" in text
    assert "_renderTraceAttributionBridge" in text
    assert "_renderEvidenceMap" in text
    assert "_renderRunRecommendation" in text
    assert "_renderStockReview" in text
    assert "_renderSegmentReview" in text
    assert "_renderTraceCompare" in text
    assert "_renderStockDiff" in text
    assert "_renderRunRankDiff" in text
    assert "_renderSegmentCompare" in text
    assert "_renderAttributionModuleDetails" in text
    assert "_renderWorkbenchSummary" in text
    assert "_applySectionVisibility" in text
    assert "_toggleAllSignalsSections" in text
    assert "_syncWorkbenchControls" in text
    assert "_renderSignalsDrilldown" in text
    assert "_renderSignalsBacktestAttribution" in text
    assert "_renderSignalsAttributionChart" in text
    assert "_renderSignalsRunsCompareChart" in text
    assert "_renderSignalsRunsCompareSummary" in text
    assert "_renderSignalsRunsPeerSummary" in text
    assert "_renderSignalsBacktestModules" in text
    assert "_renderBacktestModuleCharts" in text
    assert "_hydrateSignalsBacktestCompare" in text
    assert "_refreshSignalsRunCompareByFilters" in text
    assert "_loadSignalsBacktestAttribution" in text
    assert "_renderSignalsMarket" in text
    assert "signals/latest" in text
    assert "decision-traces/latest" in text
    assert "audit/latest" in text


def test_dashboard_js_handles_signals_tab_in_switch(client: TestClient) -> None:
    """switchTab 必含 signals 分支。"""
    r = client.get("/static/dashboard.js")
    assert r.status_code == 200
    assert 'tabName === "signals"' in r.text


# ── 回测 tab 渲染 ────────────────────────────────────────
def test_backtest_tab_present_in_index(client: TestClient) -> None:
    """index.html 必含 '回测' tab 按钮 + 配置 + 结果 section id。"""
    r = client.get("/")
    assert r.status_code == 200
    text = r.text
    # tab 按钮
    assert "回测" in text
    assert 'data-tab="backtest"' in text
    # 关键 section id
    for sec_id in ("bt-strategy", "bt-run", "bt-metrics", "bt-nav-chart", "bt-trades", "bt-skipped"):
        assert sec_id in text


def test_dashboard_js_includes_backtest_handlers(client: TestClient) -> None:
    """dashboard.js 必含 backtest handler 函数引用。"""
    r = client.get("/static/dashboard.js")
    assert r.status_code == 200
    text = r.text
    for fn in ("initBacktest", "runBacktest", "_renderBtMetrics", "_renderBtNavChart", "_renderBtTrades"):
        assert fn in text, f"missing {fn} in dashboard.js"


def test_dashboard_js_handles_backtest_tab_in_switch(client: TestClient) -> None:
    """switchTab 必含 backtest 分支。"""
    r = client.get("/static/dashboard.js")
    assert r.status_code == 200
    assert 'tabName === "backtest"' in r.text
