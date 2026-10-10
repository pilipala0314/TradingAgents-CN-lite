"""_shared/utils.py 纯函数单测。"""
import numpy as np
import pytest

from datasource.providers._shared.utils import (
    CNINFO_ALL_MARKETS,
    cninfo_market_params,
    fmt_date,
    to_native,
)


@pytest.mark.unit
class TestFmtDate:
    def test_none(self):
        assert fmt_date(None) == ""

    def test_iso(self):
        assert fmt_date("2025-07-01") == "20250701"

    def test_compact(self):
        assert fmt_date("20250701") == "20250701"

    def test_date_obj(self):
        from datetime import date

        assert fmt_date(date(2025, 7, 1)) == "20250701"


@pytest.mark.unit
class TestToNative:
    def test_none(self):
        assert to_native(None) is None

    def test_python_int_float(self):
        assert to_native(3) == 3.0
        assert to_native(3.14) == 3.14

    def test_numpy_scalars(self):
        # 回归：np.int64 不是 int 子类，旧 isinstance(v,(int,float)) 会误判成 str
        assert to_native(np.int64(7)) == 7.0
        assert to_native(np.float32(1.5)) == pytest.approx(1.5)
        assert isinstance(to_native(np.int64(7)), float)

    def test_bool_stays_bool(self):
        assert to_native(True) is True
        assert to_native(False) is False

    def test_string(self):
        assert to_native("abc") == "abc"

    def test_numpy_str(self):
        assert to_native(np.str_("x")) == "x"


@pytest.mark.unit
class TestCninfoMarketParams:
    """回归：历史上 column/plate 硬编码 szse，沪市/北交所股票一律查不到。"""

    def test_shanghai(self):
        assert cninfo_market_params("600519") == ("sse", "sh;shmb;shkcp")
        assert cninfo_market_params("900901") == ("sse", "sh;shmb;shkcp")

    def test_shenzhen(self):
        assert cninfo_market_params("000001") == ("szse", "sz;szmb;szzx;szcn")
        assert cninfo_market_params("300750") == ("szse", "sz;szmb;szzx;szcn")

    def test_beijing(self):
        assert cninfo_market_params("832000") == ("bse", "bj")
        assert cninfo_market_params("430047") == ("bse", "bj")

    def test_empty_falls_back_default(self):
        assert cninfo_market_params("") == ("szse", "sz;szmb;szzx;szcn")

    def test_all_markets_covers_three(self):
        assert len(CNINFO_ALL_MARKETS) == 3
        cols = [c for c, _ in CNINFO_ALL_MARKETS]
        assert "sse" in cols and "szse" in cols and "bse" in cols
