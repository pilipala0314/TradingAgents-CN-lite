"""健康追踪器单测 — 连续运行验收的核心工具。"""
import pytest

from datasource.health_tracker import SourceHealthTracker


@pytest.mark.unit
class TestHealthTracker:
    def test_record_and_rates(self):
        t = SourceHealthTracker()
        t.record(source="eastmoney", method="get_industry_ranking", status="ok")
        t.record(source="eastmoney", method="get_industry_ranking", status="ok")
        t.record(source="eastmoney", method="get_industry_ranking", status="failed", error_type="RemoteProtocolError")
        t.record(source="sina", method="get_industry_ranking", status="ok")

        rates = t.source_success_rate("")
        # 默认用当天日期，source_success_rate("") 找不到 → 全返回 1.0（防御）
        # 用 daily_report 验证
        from datetime import date

        day = date.today().isoformat()
        report = t.daily_report(day)
        assert len(report["sources"]) == 2
        src_map = {s["source"]: s for s in report["sources"]}
        assert src_map["eastmoney"]["ok"] == 2
        assert src_map["eastmoney"]["failed"] == 1
        assert src_map["eastmoney"]["success_rate"] == pytest.approx(2 / 3, abs=0.001)
        assert src_map["eastmoney"]["error_breakdown"]["RemoteProtocolError"] == 1

    def test_method_aggregation(self):
        t = SourceHealthTracker()
        t.record(source="a", method="get_daily_bars", status="ok")
        t.record(source="b", method="get_daily_bars", status="failed", error_type="Timeout")
        from datetime import date

        day = date.today().isoformat()
        report = t.daily_report(day)
        method_map = {m["method"]: m for m in report["methods"]}
        assert method_map["get_daily_bars"]["total"] == 2
        assert method_map["get_daily_bars"]["ok"] == 1
        assert method_map["get_daily_bars"]["failed"] == 1
        assert t.overall_rate(day) == pytest.approx(0.5)

    def test_empty_day(self):
        t = SourceHealthTracker()
        assert t.overall_rate("2020-01-01") == 0.0
        assert t.daily_report("2020-01-01") == {"trade_date": "2020-01-01", "sources": [], "methods": []}

    def test_jsonl_flush(self, tmp_path):
        t = SourceHealthTracker(log_dir=str(tmp_path))
        t.record(source="sina", method="get_index_daily_bars", status="ok")
        from datetime import date

        f = tmp_path / f"{date.today().isoformat()}.jsonl"
        assert f.exists()
        content = f.read_text(encoding="utf-8")
        assert '"source": "sina"' in content or '"source":"sina"' in content


@pytest.mark.unit
class TestRouterTrackerWiring:
    """验证 router 调用写入全局 tracker（此前用独立实例导致零覆盖 — 审计 P1）。"""

    def _global_total(self, day: str) -> int:
        from datasource import health_tracker as ht

        report = ht.get_tracker().daily_report(day)
        return sum(s["total"] for s in report["sources"])

    def test_route_writes_global_tracker(self):
        import asyncio
        from datetime import date

        from datasource.resilience.policy import ResiliencePolicy
        from datasource.router import DataSource

        class _Ok:
            @property
            def name(self):
                return "ut_ok"

            async def get_daily_bars(self, *args, **kwargs):
                return {"ok": 1}

        class _Fail:
            @property
            def name(self):
                return "ut_fail"

            async def get_daily_bars(self, *args, **kwargs):
                raise ValueError("boom")

        table = {"get_daily_bars": {"ut_ok": "get_daily_bars", "ut_fail": "get_daily_bars"}}
        source = DataSource(
            {"ut_ok": _Ok(), "ut_fail": _Fail()}, table,
            cache_ttl=0, default_timeout=5, default_resilience=ResiliencePolicy(),
        )
        day = date.today().isoformat()
        before = self._global_total(day)

        asyncio.run(source.route("get_daily_bars", "600519"))  # 成功 → 记 1
        with pytest.raises(Exception):
            asyncio.run(source.route("get_daily_bars", "600519", provider="ut_fail", strict=True))

        assert self._global_total(day) - before == 2

    def test_quality_fail_writes_global_tracker(self):
        """质量失败（空 selector 结果）也写入全局 tracker（审计 P2 修复）。"""
        import asyncio
        from datetime import date

        from datasource import health_tracker as ht
        from datasource.errors import DataFetchError
        from datasource.resilience.policy import ResiliencePolicy
        from datasource.router import DataSource

        class _Empty:
            @property
            def name(self):
                return "ut_empty"

            async def get_industry_ranking(self, *args, **kwargs):
                return []

        table = {"get_industry_ranking": {"ut_empty": "get_industry_ranking"}}
        source = DataSource(
            {"ut_empty": _Empty()}, table,
            cache_ttl=0, default_timeout=5, default_resilience=ResiliencePolicy(),
        )
        day = date.today().isoformat()
        before = self._global_total(day)
        with pytest.raises(DataFetchError):
            asyncio.run(source.route("get_industry_ranking"))

        report = ht.get_tracker().daily_report(day)
        assert sum(s["total"] for s in report["sources"]) - before == 1
        src = {s["source"]: s for s in report["sources"]}.get("ut_empty")
        assert src is not None
        assert src["error_breakdown"].get("QualityFail") == 1
