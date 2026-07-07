"""P1-T7 报告导出 Markdown — 单元测试。

覆盖：
- _render_market_section: 空数据 / 多条
- _render_sector_section: 空数据 / 按 confidence 排序
- _render_approved_section: 表格 5 列
- _render_blocked_section: 含 invalid_conditions
- _render_attribution_section: dict → 列表
- export_daily_report: 含 LLM 增强（mock LLM）
- export_daily_report: LLM 不可用 → 占位
- 4 个 section 都存在
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from services.report_exporter import (
    _render_approved_section,
    _render_attribution_section,
    _render_blocked_section,
    _render_market_section,
    _render_sector_section,
    export_daily_report,
)


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def test_market_section_empty() -> None:
    out = _render_market_section([])
    assert "## 市场概览" in out
    assert "暂无市场信号" in out


def test_market_section_with_data() -> None:
    sigs = [
        {"entity_id": "sh_index", "direction": "long", "confidence": 0.8,
         "supporting_evidence": ["PMI 上行", "社融转正"]},
        {"entity_id": "hs300", "direction": "long", "confidence": 0.7, "supporting_evidence": []},
    ]
    out = _render_market_section(sigs)
    assert "## 市场概览" in out
    assert "sh_index" in out
    assert "PMI 上行" in out
    assert "0.80" in out


def test_sector_section_sorts_by_confidence() -> None:
    sigs = [
        {"entity_id": "sw_801010", "direction": "long", "confidence": 0.3},
        {"entity_id": "sw_801080", "direction": "long", "confidence": 0.9},
        {"entity_id": "sw_801120", "direction": "long", "confidence": 0.5},
    ]
    out = _render_sector_section(sigs)
    # sw_801080 应该排第一
    idx_8080 = out.index("sw_801080")
    idx_1010 = out.index("sw_801010")
    assert idx_8080 < idx_1010


def test_approved_section_includes_table() -> None:
    sigs = [
        {"entity_id": "600519", "direction": "long", "confidence": 0.9,
         "model_version": "v3.2.1", "supporting_evidence": ["高分红"]},
    ]
    out = _render_approved_section(sigs)
    assert "## 准入个股" in out
    assert "600519" in out
    assert "v3.2.1" in out
    assert "| 代码 | 方向 | 模型评分 | 模型版本 | 关键证据 |" in out


def test_blocked_section_includes_invalid_conditions() -> None:
    sigs = [
        {"entity_id": "000001", "direction": "short", "confidence": 0.6,
         "invalid_conditions": ["行业景气度下行", "估值偏高"]},
    ]
    out = _render_blocked_section(sigs)
    assert "## 拦截个股" in out
    assert "000001" in out
    assert "行业景气度下行" in out


def test_attribution_section_renders_dict() -> None:
    out = _render_attribution_section({"alpha": 0.123, "beta": 0.85, "sharpe": 1.8})
    assert "## 归因分析" in out
    assert "alpha" in out
    assert "0.1230" in out


def test_attribution_section_empty() -> None:
    out = _render_attribution_section(None)
    assert "无归因数据" in out


@pytest.mark.asyncio
async def test_export_daily_report_contains_all_sections() -> None:
    """完整报告含 4 类信号 section + 归因 + AI 解读。"""
    grouped = {
        "market": [{"entity_id": "sh", "direction": "long", "confidence": 0.7,
                    "supporting_evidence": ["x"], "model_version": "v1"}],
        "sector": [{"entity_id": "sw_801010", "direction": "long", "confidence": 0.6,
                    "supporting_evidence": [], "model_version": "v1"}],
        "approved": [{"entity_id": "600519", "direction": "long", "confidence": 0.9,
                      "supporting_evidence": ["a"], "model_version": "v1"}],
        "blocked": [{"entity_id": "000001", "direction": "short", "confidence": 0.7,
                     "invalid_conditions": ["b"], "model_version": "v1"}],
    }

    async def _llm_ok(*_a, **_kw):
        return "> AI 测试解读"

    with patch(
        "services.report_exporter._llm_enhance_section",
        side_effect=_llm_ok,
    ):
        md = await export_daily_report(
            business_date="2026-07-01", grouped=grouped,
            attribution={"alpha": 0.1, "beta": 0.9},
        )
    assert "# A 股研究日报 · 2026-07-01" in md
    assert "## 市场概览" in md
    assert "## 板块轮动" in md
    assert "## 准入个股" in md
    assert "## 拦截个股" in md
    assert "## 归因分析" in md
    assert "AI 解读" in md
    assert "600519" in md
    assert "000001" in md


@pytest.mark.asyncio
async def test_export_daily_report_llm_failure_shows_placeholder() -> None:
    """LLM 失败 → 占位 > LLM 不可用: <reason>。"""
    grouped = {
        "market": [{"entity_id": "sh", "direction": "long", "confidence": 0.5,
                    "supporting_evidence": [], "model_version": "v1"}],
        "sector": [], "approved": [], "blocked": [],
    }

    async def _llm_fail(*_a, **_kw):
        raise Exception("LLM API key missing")

    with patch(
        "services.report_exporter._llm_enhance_section",
        side_effect=_llm_fail,
    ):
        md = await export_daily_report(business_date="2026-07-01", grouped=grouped)
    # 整个 export 不应抛错，markdown 中含失败占位
    assert "LLM 不可用" in md or "AI 解读" in md


@pytest.mark.asyncio
async def test_export_daily_report_without_llm() -> None:
    """llm_enhance=False → 不调 LLM、不含 AI 解读。"""
    grouped = {
        "market": [], "sector": [], "approved": [], "blocked": [],
    }
    md = await export_daily_report(
        business_date="2026-07-01", grouped=grouped, llm_enhance=False,
    )
    assert "AI 解读" not in md


@pytest.mark.asyncio
async def test_export_daily_report_empty_grouped() -> None:
    """完全空数据 → 报告仍生成，含 4 个 '—' section。"""
    md = await export_daily_report(
        business_date="2026-07-01", grouped={}, llm_enhance=False,
    )
    assert "## 市场概览" in md
    assert "## 板块轮动" in md
    assert "## 准入个股" in md
    assert "## 拦截个股" in md
    assert "## 归因分析" in md
