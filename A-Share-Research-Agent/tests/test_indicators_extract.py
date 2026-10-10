"""L2 单源提取单测（无网络，纯函数）。"""
import pandas as pd
import pytest

from foundation.registry import ExtractKind, SourceSpec
from foundation.schemas import Caliber, DataStatus

from indicators.extract import (
    extract_source,
    has_unresolved,
    norm_code,
    resolve_templates,
)


def _spec(**kw) -> SourceSpec:
    base = dict(provider="fake", method="get_x", caliber=Caliber.AKSHARE)
    base.update(kw)
    return SourceSpec(**base)


@pytest.mark.unit
class TestTemplates:
    def test_resolve_nested(self):
        got = resolve_templates(
            {"symbol": "{component}", "date": "{trade_date}", "keep": "x"},
            {"component": "600519", "trade_date": "20260930"},
        )
        assert got == {"symbol": "600519", "date": "20260930", "keep": "x"}

    def test_unresolved_detection(self):
        assert has_unresolved({"a": "{index_code}"}) is True
        assert has_unresolved({"a": "000001"}) is False
        assert has_unresolved(["x", {"b": "{c}"}]) is True

    def test_norm_code(self):
        assert norm_code("1") == "000001"
        assert norm_code(600519) == "600519"
        assert norm_code("sh600519") == "sh600519"


@pytest.mark.unit
class TestValueExtraction:
    def test_value_from_list_with_date_normalization(self):
        data = [
            {"日期": "2026-09-30", "净额": 5},
            {"日期": "2026-09-29", "净额": 9},
        ]
        spec = _spec(extract_kind=ExtractKind.VALUE, field="净额", match={"日期": "{trade_date}"})
        ex = extract_source(spec, data, {"trade_date": "20260930"})
        assert ex.status == DataStatus.OK
        assert ex.value == 5

    def test_value_from_dataframe_native_types(self):
        df = pd.DataFrame([{"board_name": "半导体", "change_pct": 2}] + [{"board_name": "X", "change_pct": 1}] * 0)
        spec = _spec(extract_kind=ExtractKind.VALUE, field="change_pct", match={"board_name": "{sector}"})
        ex = extract_source(spec, df, {"sector": "半导体"})
        assert ex.status == DataStatus.OK
        assert ex.value == pytest.approx(2.0)
        assert type(ex.value).__module__ != "numpy"

    def test_value_field_missing_is_empty(self):
        spec = _spec(extract_kind=ExtractKind.VALUE, field="不存在")
        ex = extract_source(spec, {"a": 1}, {})
        assert ex.status == DataStatus.EMPTY
        assert "不在返回列" in ex.note

    def test_value_no_match_is_empty(self):
        spec = _spec(extract_kind=ExtractKind.VALUE, field="v", match={"板块": "半导体"})
        ex = extract_source(spec, [{"板块": "光伏", "v": 1}], {})
        assert ex.status == DataStatus.EMPTY
        assert "未匹配到行" in ex.note

    def test_value_non_numeric_string_ok(self):
        spec = _spec(extract_kind=ExtractKind.VALUE, field="name")
        ex = extract_source(spec, {"name": "中芯国际"}, {})
        assert ex.status == DataStatus.OK
        assert ex.value == "中芯国际"

    def test_value_none_is_empty(self):
        spec = _spec(extract_kind=ExtractKind.VALUE, field="v")
        ex = extract_source(spec, {"v": None}, {})
        assert ex.status == DataStatus.EMPTY

    def test_empty_table_is_empty(self):
        spec = _spec(extract_kind=ExtractKind.VALUE, field="v")
        assert extract_source(spec, [], {}).status == DataStatus.EMPTY
        assert extract_source(spec, pd.DataFrame(), {}).status == DataStatus.EMPTY


@pytest.mark.unit
class TestSumAndCount:
    def test_sum_with_unit_scale_and_skip_dirty(self):
        data = [{"v": 1e8}, {"v": "2e8"}, {"v": "—"}]
        spec = _spec(extract_kind=ExtractKind.SUM, field="v", unit_scale=1e-8)
        ex = extract_source(spec, data, {})
        assert ex.status == DataStatus.OK
        assert ex.value == pytest.approx(3.0)

    def test_sum_no_numeric_is_empty(self):
        spec = _spec(extract_kind=ExtractKind.SUM, field="v")
        ex = extract_source(spec, [{"v": "x"}], {})
        assert ex.status == DataStatus.EMPTY

    def test_count_with_component_set(self):
        data = [{"代码": "600519"}, {"代码": "000001"}, {"代码": "999999"}]
        spec = _spec(extract_kind=ExtractKind.COUNT, match={"代码": "{component}"})
        ex = extract_source(spec, data, {}, component_set={"600519", "000001"})
        assert ex.status == DataStatus.OK
        assert ex.value == 2

    def test_count_zero_matches_on_nonempty_is_ok_zero(self):
        spec = _spec(extract_kind=ExtractKind.COUNT, match={"代码": "{component}"})
        ex = extract_source(spec, [{"代码": "999999"}], {}, component_set={"600519"})
        assert ex.status == DataStatus.OK
        assert ex.value == 0

    def test_count_empty_table_is_empty(self):
        spec = _spec(extract_kind=ExtractKind.COUNT)
        assert extract_source(spec, [], {}).status == DataStatus.EMPTY


@pytest.mark.unit
class TestWholeExtraction:
    def test_whole_list_keeps_payload_no_scalar(self):
        data = [{"title": "a"}, {"title": "b"}]
        spec = _spec(extract_kind=ExtractKind.WHOLE)
        ex = extract_source(spec, data, {})
        assert ex.status == DataStatus.OK
        assert ex.value is None  # 整表非标量：禁止产生误导性数字
        assert ex.matched_rows == 2
        assert ex.payload == data

    def test_whole_with_component_filter(self):
        data = [{"代码": "600519", "name": "x"}, {"代码": "999999", "name": "y"}]
        spec = _spec(extract_kind=ExtractKind.WHOLE, match={"代码": "{component}"})
        ex = extract_source(spec, data, {}, component_set={"600519"})
        assert ex.status == DataStatus.OK
        assert ex.matched_rows == 1
        assert ex.payload[0]["name"] == "x"

    def test_whole_dict_and_empty(self):
        spec = _spec(extract_kind=ExtractKind.WHOLE)
        ex = extract_source(spec, {"a": 1, "b": 2}, {})
        assert ex.status == DataStatus.OK
        assert ex.value is None
        assert ex.matched_rows == 2
        assert extract_source(spec, {}, {}).status == DataStatus.EMPTY
        assert extract_source(spec, None, {}).status == DataStatus.EMPTY
        assert extract_source(spec, [], {}).status == DataStatus.EMPTY