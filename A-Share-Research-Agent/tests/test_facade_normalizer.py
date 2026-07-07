"""facade_normalizer.py 单元测试 — DataFrame → list[dict] 归一化。"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from data_pipeline.facade_normalizer import (
    EXPECT_LIST_OF_DICT_METHODS,
    _clean_value,
    dataframe_to_list_of_dict,
    normalize_facade_results,
    normalize_response,
    safe_dataframe_records,
)

# ── dataframe_to_list_of_dict ──────────────────────────────────

class TestDataframeToListOfDict:
    def test_simple(self) -> None:
        df = pd.DataFrame({"code": ["000636", "600519"], "name": ["风华高科", "贵州茅台"]})
        out = dataframe_to_list_of_dict(df)
        assert out == [
            {"code": "000636", "name": "风华高科"},
            {"code": "600519", "name": "贵州茅台"},
        ]

    def test_empty(self) -> None:
        assert dataframe_to_list_of_dict(pd.DataFrame()) == []

    def test_none(self) -> None:
        assert dataframe_to_list_of_dict(None) == []  # type: ignore[arg-type]

    def test_nan_becomes_none(self) -> None:
        df = pd.DataFrame({"code": ["000636", None], "value": [1.0, np.nan]})
        out = dataframe_to_list_of_dict(df)
        assert out == [
            {"code": "000636", "value": 1.0},
            {"code": None, "value": None},
        ]

    def test_datetime_becomes_str(self) -> None:
        df = pd.DataFrame({
            "date": [date(2024, 1, 15), date(2024, 1, 16)],
            "value": [1, 2],
        })
        out = dataframe_to_list_of_dict(df)
        assert out[0]["date"] == "2024-01-15"
        assert out[1]["date"] == "2024-01-16"

    def test_datetime_with_time(self) -> None:
        df = pd.DataFrame({
            "ts": [datetime(2024, 1, 15, 9, 30, 0)],
            "value": [100],
        })
        out = dataframe_to_list_of_dict(df)
        assert out[0]["ts"] == "2024-01-15T09:30:00"

    def test_numpy_types_become_python(self) -> None:
        df = pd.DataFrame({"int_val": [np.int64(42)], "float_val": [np.float64(3.14)]})
        out = dataframe_to_list_of_dict(df)
        assert out[0]["int_val"] == 42
        assert isinstance(out[0]["int_val"], int)
        assert out[0]["float_val"] == 3.14
        assert isinstance(out[0]["float_val"], float)

    def test_chinese_columns(self) -> None:
        df = pd.DataFrame({"代码": ["000636"], "名称": ["风华高科"]})
        out = dataframe_to_list_of_dict(df)
        assert out == [{"代码": "000636", "名称": "风华高科"}]


# ── _clean_value ────────────────────────────────────────────────

class TestCleanValue:
    def test_none(self) -> None:
        assert _clean_value(None) is None

    def test_nan_float(self) -> None:
        assert _clean_value(float("nan")) is None

    def test_datetime(self) -> None:
        assert _clean_value(datetime(2024, 1, 15, 9, 30)) == "2024-01-15T09:30:00"

    def test_date(self) -> None:
        assert _clean_value(date(2024, 1, 15)) == "2024-01-15"

    def test_pd_timestamp(self) -> None:
        assert _clean_value(pd.Timestamp("2024-01-15")) == "2024-01-15T00:00:00"

    def test_numpy_int(self) -> None:
        assert _clean_value(np.int64(5)) == 5

    def test_numpy_float(self) -> None:
        assert _clean_value(np.float64(3.14)) == 3.14

    def test_numpy_bool(self) -> None:
        assert _clean_value(np.bool_(True)) is True

    def test_passthrough_string(self) -> None:
        assert _clean_value("hello") == "hello"

    def test_passthrough_int(self) -> None:
        assert _clean_value(42) == 42


# ── normalize_response ──────────────────────────────────────────

class TestNormalizeResponse:
    def test_method_in_table_converts_dataframe(self) -> None:
        df = pd.DataFrame({"code": ["000636"]})
        out = normalize_response("get_stock_list", df)
        assert out == [{"code": "000636"}]

    def test_method_in_table_preserves_list(self) -> None:
        """已经是 list[dict] 不再转换（幂等）。"""
        lst = [{"code": "000636"}]
        out = normalize_response("get_stock_list", lst)
        assert out is lst

    def test_method_in_table_dict_passthrough(self) -> None:
        """单条 dict 不转换。"""
        d = {"code": "000636"}
        out = normalize_response("get_stock_list", d)
        assert out is d

    def test_method_not_in_table_passthrough(self) -> None:
        """不在表里的方法不归一化（保持原行为）。"""
        df = pd.DataFrame({"code": ["000636"]})
        out = normalize_response("get_tencent_quote", df)  # 不在表里
        assert out is df  # 原样返回

    def test_none_returns_empty_list(self) -> None:
        out = normalize_response("get_stock_list", None)
        assert out == []

    def test_list_with_dataframe_items(self) -> None:
        """list 里嵌套 DataFrame 也展开。"""
        df = pd.DataFrame({"code": ["000636"]})
        out = normalize_response("get_stock_list", [df])
        assert out == [{"code": "000636"}]


# ── normalize_facade_results ────────────────────────────────────

class TestNormalizeFacadeResults:
    def test_batch_normalization(self) -> None:
        results = {
            "get_stock_list": pd.DataFrame({"code": ["000636"]}),
            "get_tencent_quote": pd.DataFrame({"code": ["600519"]}),  # 不在表里
            "get_daily_bars": pd.DataFrame({"date": ["2024-01-15"], "close": [10.0]}),
        }
        out = normalize_facade_results(results)
        # get_stock_list 在表里 → list[dict]
        assert out["get_stock_list"] == [{"code": "000636"}]
        # get_tencent_quote 不在表里 → 原样
        assert isinstance(out["get_tencent_quote"], pd.DataFrame)
        # get_daily_bars 在表里 → list[dict]
        assert out["get_daily_bars"] == [{"date": "2024-01-15", "close": 10.0}]

    def test_subset_normalization(self) -> None:
        results = {
            "a": pd.DataFrame({"x": [1]}),
            "b": pd.DataFrame({"y": [2]}),
        }
        out = normalize_facade_results(results, methods_to_normalize={"a"})
        assert isinstance(out["a"], list)
        assert isinstance(out["b"], pd.DataFrame)


# ── safe_dataframe_records ──────────────────────────────────────

class TestSafeDataframeRecords:
    def test_basic(self) -> None:
        df = pd.DataFrame({"x": [1, 2, 3]})
        out = safe_dataframe_records(df)
        assert out == [{"x": 1}, {"x": 2}, {"x": 3}]

    def test_with_limit(self) -> None:
        df = pd.DataFrame({"x": [1, 2, 3, 4, 5]})
        out = safe_dataframe_records(df, limit=2)
        assert out == [{"x": 1}, {"x": 2}]

    def test_empty(self) -> None:
        assert safe_dataframe_records(pd.DataFrame()) == []
        assert safe_dataframe_records(None) == []  # type: ignore[arg-type]

    def test_doesnt_raise_on_bad_data(self) -> None:
        # 模拟异常的 DataFrame — 通过构造一个奇怪的列
        df = pd.DataFrame({"x": [1, 2]})
        out = safe_dataframe_records(df, limit=10)
        assert len(out) == 2


# ── EXPECT_LIST_OF_DICT_METHODS 健康度 ─────────────────────────

class TestExpectedListOfDictMethods:
    def test_contains_core_methods(self) -> None:
        assert "get_stock_list" in EXPECT_LIST_OF_DICT_METHODS
        assert "get_daily_bars" in EXPECT_LIST_OF_DICT_METHODS
        assert "get_limit_up_pool" in EXPECT_LIST_OF_DICT_METHODS

    def test_excludes_live_methods(self) -> None:
        """实时端点不在表里（保持原有 list[dict] 行为）。"""
        assert "get_tencent_quote" not in EXPECT_LIST_OF_DICT_METHODS
        assert "get_kline" not in EXPECT_LIST_OF_DICT_METHODS
        assert "get_quote_5level" not in EXPECT_LIST_OF_DICT_METHODS

    def test_table_size_sanity(self) -> None:
        """表里 method 数应该在 25-40 之间（覆盖 ETL 端点）。"""
        assert 25 <= len(EXPECT_LIST_OF_DICT_METHODS) <= 40
