"""同花顺板块源降级逻辑测试（mock akshare，无网络）。

验证 get_sector_fund_flow 的「东财优先、同花顺降级」策略。
"""
import asyncio
import types
from typing import Any

import pandas as pd
import pytest

import datasource.providers.akshare as ak_mod
from datasource.providers.akshare import AkshareProvider


def _mock_akshare(**attrs: Any) -> types.SimpleNamespace:
    """构造 mock akshare 模块（替代真实 import）。"""
    return types.SimpleNamespace(**attrs)


def _hist_df() -> pd.DataFrame:
    """东财 stock_sector_fund_flow_hist 返回结构。"""
    return pd.DataFrame(
        [{"日期": "2026-09-30", "主力净流入": 123.4, "净流入": 456.7, "涨跌幅": 1.23}]
    )


def _ths_df() -> pd.DataFrame:
    """同花顺 stock_board_industry_summary_ths 返回结构。"""
    return pd.DataFrame(
        [
            {"板块": "半导体", "涨跌幅": -1.27, "净流入": -5.58, "总成交额": 2300.7,
             "上涨家数": 40, "下跌家数": 149, "领涨股": "和林微纳"},
            {"板块": "银行", "涨跌幅": 0.5, "净流入": 10.0, "总成交额": 800.0,
             "上涨家数": 30, "下跌家数": 5, "领涨股": "招商银行"},
        ]
    )


def _provider_with(monkeypatch, ak) -> AkshareProvider:
    """构造 provider，并把模块级 `ak` 替换为 mock（provider 内部用模块级 `ak` 判断）。"""
    monkeypatch.setattr(ak_mod, "ak", ak)
    p = AkshareProvider()

    async def _retry(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    p._run_with_retry = _retry  # type: ignore[assignment]
    return p


@pytest.mark.unit
class TestSectorFundFlowFallback:
    def test_eastmoney_ok_no_fallback(self, monkeypatch):
        """东财成功 → 直接用东财数据，不触发同花顺。"""
        ak = _mock_akshare(stock_sector_fund_flow_hist=lambda **kw: _hist_df())
        p = _provider_with(monkeypatch, ak)

        df = asyncio.run(p.get_sector_fund_flow("半导体"))
        assert not df.empty
        assert "main_net_inflow" in df.columns
        assert df.iloc[0]["main_net_inflow"] == 123.4

    def test_eastmoney_fails_falls_back_to_ths(self, monkeypatch):
        """东财失败 → 降级同花顺（真独立源）。"""

        def _boom(**kw):
            raise ConnectionError("东财被 WAF 封锁")

        ak = _mock_akshare(
            stock_sector_fund_flow_hist=_boom,
            stock_board_industry_summary_ths=lambda: _ths_df(),
        )
        p = _provider_with(monkeypatch, ak)

        df = asyncio.run(p.get_sector_fund_flow("半导体"))
        assert not df.empty
        assert df.iloc[0]["sector"] == "半导体"
        assert df.iloc[0]["net_inflow"] == -5.58
        assert df.iloc[0]["pct_change"] == -1.27

    def test_ths_filter_by_sector(self, monkeypatch):
        """同花顺路径按板块名过滤。"""
        ak = _mock_akshare(stock_board_industry_summary_ths=lambda: _ths_df())
        p = _provider_with(monkeypatch, ak)

        df = asyncio.run(p.get_sector_fund_flow_ths("半导体"))
        assert len(df) == 1
        assert df.iloc[0]["sector"] == "半导体"

    def test_ths_columns_aligned(self, monkeypatch):
        """同花顺降级结果的列与东财对齐（供 fallback 链统一消费）。"""
        ak = _mock_akshare(stock_board_industry_summary_ths=lambda: _ths_df())
        p = _provider_with(monkeypatch, ak)

        df = asyncio.run(p.get_sector_fund_flow_ths(""))
        expected = {"sector", "pct_change", "net_inflow", "amount", "up_count", "down_count", "lead_name"}
        assert expected <= set(df.columns)
        assert df.iloc[0]["net_inflow"] == -5.58
        assert df.iloc[0]["pct_change"] == -1.27

    def test_both_sources_missing_interface_raises(self, monkeypatch):
        """两源接口都缺失 → 抛 ProviderError（不静默返空，交路由层 fallback）。"""
        from datasource.errors import ProviderError

        ak = _mock_akshare()  # 无任何板块资金流接口
        p = _provider_with(monkeypatch, ak)

        with pytest.raises(ProviderError):
            asyncio.run(p.get_sector_fund_flow("半导体"))

    def test_non_retryable_error_not_downgraded(self, monkeypatch):
        """参数类错误（NonRetryableProviderError）→ 不降级同花顺，快速失败。"""
        from datasource.errors import NonRetryableProviderError

        def _bad(**_kw):
            raise NonRetryableProviderError("参数错误", provider="akshare", method="x")

        ths_called: list[int] = []
        ak = _mock_akshare(
            stock_sector_fund_flow_hist=_bad,
            stock_board_industry_summary_ths=lambda: (ths_called.append(1), _ths_df())[1],
        )
        p = _provider_with(monkeypatch, ak)

        with pytest.raises(NonRetryableProviderError):
            asyncio.run(p.get_sector_fund_flow("半导体"))
        assert not ths_called  # 同花顺未被调用（降级无意义）
