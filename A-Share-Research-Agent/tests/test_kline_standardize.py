"""K 线列名标准化助手单测。"""
import pandas as pd
import pytest

from datasource.providers._shared.utils import standardize_kline_df


@pytest.mark.unit
class TestStandardizeKlineDf:
    def test_chinese_columns_mapped(self):
        df = pd.DataFrame(
            {
                "日期": ["2025-01-02"],
                "开盘": [1.0],
                "最高": [2.0],
                "最低": [0.5],
                "收盘": [1.5],
                "成交量": [1000],
                "成交额": [1500.0],
            }
        )
        out = standardize_kline_df(df)
        assert list(out.columns) == [
            "date", "open", "high", "low", "close", "volume", "成交额",
        ]
        assert out.iloc[0]["date"] == "2025-01-02"
        assert out.iloc[0]["volume"] == 1000

    def test_english_columns_idempotent(self):
        df = pd.DataFrame(
            {"date": ["2025-01-02"], "open": [1.0], "high": [2.0],
             "low": [0.5], "close": [1.5], "volume": [100]}
        )
        out = standardize_kline_df(df)
        assert list(out.columns) == ["date", "open", "high", "low", "close", "volume"]

    def test_keep_extra_false(self):
        df = pd.DataFrame(
            {"日期": ["2025-01-02"], "开盘": [1.0], "最高": [2.0], "最低": [0.5],
             "收盘": [1.5], "成交量": [100], "成交额": [1500.0]}
        )
        out = standardize_kline_df(df, keep_extra=False)
        assert list(out.columns) == ["date", "open", "high", "low", "close", "volume"]

    def test_vol_alias(self):
        df = pd.DataFrame({"date": ["2025-01-02"], "vol": [10]})
        out = standardize_kline_df(df)
        assert "volume" in out.columns
        assert out.iloc[0]["volume"] == 10

    def test_original_df_unchanged(self):
        df = pd.DataFrame({"日期": ["2025-01-02"], "开盘": [1.0]})
        standardize_kline_df(df)
        assert list(df.columns) == ["日期", "开盘"]  # 不改动原 df

    def test_no_standard_columns_returns_same(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        out = standardize_kline_df(df)
        assert list(out.columns) == ["a", "b"]
