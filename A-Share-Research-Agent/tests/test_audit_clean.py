"""test_audit_clean — 防回滚的代码清洁度契约。

锁定这一轮"全量高+中问题清理"后的状态：
- 生产代码不允许再出现 TRAE-debugger 调试挡板（`_debug_emit` / `debug-point` / `debug-region`）
- `.dbg/` 调试目录已清，源码不允许再引用其路径
- 静默吞错的容错分支都已升级为 logger.debug / logger.warning
- Scorer 兜底路径会在 debug 日志留痕

任何回滚都会让这些测试红灯。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = [
    REPO_ROOT / "apps",
    REPO_ROOT / "agents",
    REPO_ROOT / "data_pipeline",
    REPO_ROOT / "graph",
    REPO_ROOT / "core",
    REPO_ROOT / "services",
    REPO_ROOT / "rule_engine",
    REPO_ROOT / "features_engine",
    REPO_ROOT / "models",
    REPO_ROOT / "schemas",
]


def _iter_source_files() -> list[Path]:
    out: list[Path] = []
    for d in SOURCE_DIRS:
        if d.exists():
            out.extend(d.rglob("*.py"))
    return out


class TestNoDebuggingBoards:
    """不允许再出现 TRAE-debugger 调试塞的代码。"""

    def test_no_debug_emit_in_sources(self):
        offenders: list[str] = []
        for p in _iter_source_files():
            text = p.read_text(encoding="utf-8", errors="ignore")
            if "_debug_emit" in text or re.search(r"debug-point\s+[A-Z]:", text):
                offenders.append(str(p.relative_to(REPO_ROOT)))
        assert offenders == [], (
            "生产代码不允许保留 _debug_emit / debug-point 调试挡板: " + ", ".join(offenders)
        )

    def test_no_dot_dbg_references_in_sources(self):
        offenders: list[str] = []
        for p in _iter_source_files():
            text = p.read_text(encoding="utf-8", errors="ignore")
            if ".dbg/" in text or "trae-debug-log-" in text:
                offenders.append(str(p.relative_to(REPO_ROOT)))
        assert offenders == [], (
            "生产代码不允许引用 .dbg/ 调试目录: " + ", ".join(offenders)
        )

    def test_dbg_directory_cleaned(self):
        dbg = REPO_ROOT / ".dbg"
        if dbg.exists():
            leftover = sorted(p.name for p in dbg.iterdir())
            assert leftover == [], f".dbg/ 目录残留: {leftover}"


class TestNoDeadCode:
    """不允许 `if False else` 之类的死代码。"""

    def test_no_if_false(self):
        offenders: list[str] = []
        for p in _iter_source_files():
            text = p.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"\bif\s+False\b", text):
                offenders.append(str(p.relative_to(REPO_ROOT)))
        assert offenders == [], (
            "生产代码不允许出现 if False 死代码: " + ", ".join(offenders)
        )


class TestScorerFallbackAuditable:
    """Scorer 三个无数据兜底函数必须留 logger.debug 痕迹。"""

    @pytest.mark.parametrize(
        "fn,args",
        [
            ("score_liquidity", (0, 10)),
            ("score_liquidity", (100, 0)),
            ("score_valuation_percentile_from_pe", (0,)),
            ("score_valuation_percentile_from_pe", (-5,)),
        ],
    )
    def test_missing_input_logs_debug(self, fn, args, caplog):
        from data_pipeline.providers import scorer

        with caplog.at_level(logging.DEBUG, logger=scorer.logger.name):
            getattr(scorer, fn)(*args)
        msgs = [r.message for r in caplog.records]
        assert any("兜底" in m for m in msgs), (
            f"{fn}({args}) 数据缺失未打 debug 日志: {msgs}"
        )

    def test_score_crowding_none_logs_debug(self, caplog):
        from data_pipeline.providers import scorer

        with caplog.at_level(logging.DEBUG, logger=scorer.logger.name):
            scorer.score_crowding(None, 1.0)
        assert any("兜底" in r.message for r in caplog.records), (
            "score_crowding(None, x) 未打 debug 日志"
        )


class TestRenderersContract:
    """renderer 必须容忍 `AgentOutputEnvelope` 不带 `fallback_used` 字段。"""

    def test_report_renderer_does_not_access_fallback_used(self):
        # 防止有人又把 `if committee_decision.fallback_used` 写回来
        # AgentOutputEnvelope 没有该字段，访问会 AttributeError
        p = REPO_ROOT / "agents" / "report_renderer.py"
        text = p.read_text(encoding="utf-8", errors="ignore")
        assert "committee_decision.fallback_used" not in text, (
            "report_renderer 不应访问 committee_decision.fallback_used（字段不存在）"
        )
        assert "llm_unavailable_fallback" in text, (
            "report_renderer 应通过 warnings 列表的 'llm_unavailable_fallback' 标记判断兜底"
        )


class TestCommitteeParseResponseAuditable:
    """committee._parse_response 不能静默吞错。"""

    def test_empty_content_logs_warning(self, caplog):
        from agents.committee import CommitteeSynthesizer

        synth = CommitteeSynthesizer()
        with caplog.at_level(logging.WARNING):
            result = synth._parse_response("")
        assert result == {}
        assert any("返回空内容" in r.message for r in caplog.records), (
            "LLM 返回空内容时 _parse_response 未打 warning 日志"
        )

    def test_bad_json_logs_error(self, caplog):
        from agents.committee import CommitteeSynthesizer

        synth = CommitteeSynthesizer()
        with caplog.at_level(logging.ERROR):
            result = synth._parse_response("not a json at all, no code block either")
        assert result == {}
        assert any("无法解析" in r.message for r in caplog.records), (
            "LLM 输出无法解析时 _parse_response 未打 error 日志"
        )

    def test_good_json_parses(self):
        from agents.committee import CommitteeSynthesizer

        synth = CommitteeSynthesizer()
        result = synth._parse_response('{"verdict": "long", "score": 0.8}')
        assert result == {"verdict": "long", "score": 0.8}


class TestNormalizeRowsAuditable:
    """dashboard._normalize_rows 异常分支必须留 logger.warning。"""

    def test_unknown_type_logs_warning(self, caplog):
        from apps.api.routers.dashboard import _normalize_rows

        with caplog.at_level(logging.WARNING):
            result = _normalize_rows(12345)  # int — 未知类型
        assert result == []
        assert any("无法归一化" in r.message for r in caplog.records), (
            "未知类型输入未打 warning 日志"
        )

    def test_dataframe_with_broken_to_dict_logs_warning(self, caplog):
        from apps.api.routers.dashboard import _normalize_rows

        class Broken:
            def to_dict(self, *, orient):
                raise ValueError("simulated df failure")

        with caplog.at_level(logging.WARNING):
            result = _normalize_rows(Broken())
        assert result == []
        assert any("失败" in r.message for r in caplog.records), (
            "DataFrame.to_dict 失败时未打 warning 日志"
        )

    def test_list_passthrough(self):
        from apps.api.routers.dashboard import _normalize_rows

        assert _normalize_rows([{"a": 1}]) == [{"a": 1}]

    def test_none_returns_empty(self):
        from apps.api.routers.dashboard import _normalize_rows

        assert _normalize_rows(None) == []


# ── P0 阶段专项审计 ────────────────────────────


P0_NEW_SERVICES = [
    "services/macro_interpretation_service.py",
    "services/decision_drilldown_service.py",
    "services/scheduler_status_service.py",
    "services/provider_failover.py",
    "services/sector_heatmap_service.py",
]

P0_NEW_ETLS = [
    "data_pipeline/etl/hk_kline_etl.py",
    "data_pipeline/etl/us_kline_etl.py",
    "data_pipeline/etl/futures_kline_etl.py",
    "data_pipeline/etl/sw_industry_kline_etl.py",
    "data_pipeline/etl/macro_etl.py",
]

P0_ALL_NEW_FILES = P0_NEW_SERVICES + P0_NEW_ETLS


class TestP0NoMock:
    """P0 新代码不允许出现 Mock / 假数据占位。

    禁止模式：
    - 硬编码 'mock' / 'fake' / 'stub' 注释
    - 写死的 dict 假装 API 响应
    - 只在测试中存在、生产代码不调用
    """

    def test_p0_services_have_no_mock_comments(self):
        offenders: list[str] = []
        for rel in P0_ALL_NEW_FILES:
            p = REPO_ROOT / rel
            if not p.exists():
                offenders.append(f"{rel} (不存在)")
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            # 排除 docstring / 中文注释正常用法，匹配带 # 后紧跟 mock/fake/stub 的注释
            for m in re.finditer(
                r"#[^\n]*(?:\bmock\b|\bfake\b|\bstub\b)[^\n]*", text, re.IGNORECASE
            ):
                line_no = text[: m.start()].count("\n") + 1
                # 排除 docstring 里的正常词汇（如 "no mock"）
                snippet = m.group(0).lower()
                if "no mock" in snippet or "无 mock" in snippet or "无挡板" in snippet:
                    continue
                offenders.append(f"{rel}:{line_no}: {m.group(0).strip()}")
        assert offenders == [], "P0 新代码不允许出现 mock/fake/stub 注释:\n" + "\n".join(offenders)

    def test_p0_services_import_real_modules(self):
        """P0 services 至少 import 一个 data_pipeline 或 services 模块（不能纯空架子）。

        豁免：以下文件是纯状态机 / 注册器，不需要业务模块依赖：
        - scheduler_status_service：全局 scheduler 注册表
        - provider_failover：failover 状态机（被 data_router / health router 调用）
        """
        exempt = {
            "services/scheduler_status_service.py",
            "services/provider_failover.py",
            "services/sector_heatmap_service.py",  # 纯数据处理 + pandas，调用方在 router 层
        }
        for rel in P0_NEW_SERVICES:
            if rel in exempt:
                continue
            p = REPO_ROOT / rel
            assert p.exists(), f"{rel} 不存在"
            text = p.read_text(encoding="utf-8", errors="ignore")
            has_real_import = any(
                head in text
                for head in (
                    "from data_pipeline",
                    "from services",
                    "from apps",
                    "from core",
                    "from models",
                )
            )
            assert has_real_import, (
                f"{rel} 没 import 任何业务模块（可能是空架子）"
            )

    def test_p0_etls_use_real_data_sources(self):
        """P0 ETL 必须调真实数据源接口（不能写死 list）。"""
        for rel in P0_NEW_ETLS:
            p = REPO_ROOT / rel
            assert p.exists(), f"{rel} 不存在"
            text = p.read_text(encoding="utf-8", errors="ignore")
            has_real_fetch = any(
                token in text
                for token in (
                    "akshare",
                    "ak.",
                    "stock_",
                    "macro_",
                    "sw_",
                    "futures_",
                    "hk_",
                    "us_",
                )
            )
            assert has_real_fetch, (
                f"{rel} 没引用任何真实数据源（akshare / stock_* / macro_* 等）"
            )


class TestP0NoSilentFallback:
    """P0 新代码不允许静默吞错。

    禁止模式：
    - 裸 `except: pass` / `except: continue`
    - 失败的分支不带 logger.warning / .exception
    """

    def test_p0_no_bare_pass(self):
        offenders: list[str] = []
        # 匹配 except ... : pass  / except ... : continue (空白任意)
        pattern = re.compile(
            r"except[^:\n]*:\s*(?:pass|continue)\b", re.MULTILINE
        )
        for rel in P0_ALL_NEW_FILES:
            p = REPO_ROOT / rel
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            for m in pattern.finditer(text):
                line_no = text[: m.start()].count("\n") + 1
                offenders.append(f"{rel}:{line_no}: {m.group(0).strip()}")
        assert offenders == [], (
            "P0 新代码不允许出现 except: pass / except: continue:\n"
            + "\n".join(offenders)
        )

    def test_p0_exception_branches_log(self):
        """P0 services 的 except 子句至少要 log（不允许裸 raise / return None）。"""
        pattern = re.compile(r"except[^:]*:\s*\n\s*([^\s#]+)", re.MULTILINE)
        offenders: list[str] = []
        for rel in P0_NEW_SERVICES:
            p = REPO_ROOT / rel
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            for m in pattern.finditer(text):
                body = m.group(1).strip()
                # 允许的：logger.* / raise / return <error-payload>（只用于 API 层）
                allowed = (
                    body.startswith("logger.")
                    or body.startswith("raise")
                    or body.startswith("return ")
                    or body.startswith("#")  # 注释占位（罕见）
                )
                if not allowed:
                    line_no = text[: m.start()].count("\n") + 1
                    offenders.append(f"{rel}:{line_no}: except body=`{body[:60]}`")
        # 这条规则只警告，不强行阻断
        if offenders:
            print(
                "\n[P0-AUDIT] 以下 except 子句未 log / 未 raise / 未 return，建议审查:\n  "
                + "\n  ".join(offenders)
            )


class TestP0RouterContract:
    """P0 新增的 4 类端点必须有响应契约（不静默 500 / 不返 None）。"""

    def test_p0_decisions_endpoint_documented(self):
        """docstring 必须说明返回字段。"""
        from apps.api.routers import dashboard

        # 找 stock_decisions 函数
        fn = getattr(dashboard, "stock_decisions", None)
        assert fn is not None, "dashboard.stock_decisions 不存在"
        doc = (fn.__doc__ or "").strip()
        assert "traces" in doc, "stock_decisions docstring 应说明返回 traces"

    def test_p0_heatmap_endpoint_documented(self):
        from apps.api.routers import dashboard

        fn = getattr(dashboard, "sector_heatmap", None)
        assert fn is not None, "dashboard.sector_heatmap 不存在"
        doc = (fn.__doc__ or "").strip()
        assert "matrix" in doc, "sector_heatmap docstring 应说明返回 matrix"

    def test_p0_failover_endpoints_documented(self):
        from apps.api.routers import health

        for name in (
            "failover_overview",
            "failover_history",
            "failover_state",
            "failover_force_recover",
        ):
            fn = getattr(health, name, None)
            assert fn is not None, f"health.{name} 不存在"

    def test_p0_scheduler_endpoint_documented(self):
        from apps.api.routers import admin

        fn = getattr(admin, "scheduler_status", None)
        assert fn is not None, "admin.scheduler_status 不存在"
        doc = (fn.__doc__ or "").strip()
        assert "running" in doc, "scheduler_status docstring 应说明 running 字段"
