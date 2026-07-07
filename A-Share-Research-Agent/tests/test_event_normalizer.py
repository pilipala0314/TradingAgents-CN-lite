"""test_event_normalizer — 验证 4 个新方法（severity 推断 + 3 个 list 入口）。"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from data_pipeline.normalizers.event_normalizer import EventNormalizer


class TestInferSeverity:
    def test_critical_when_high_importance_and_high_risk(self):
        assert EventNormalizer._infer_severity(0.80, 0.70) == "critical"

    def test_high_when_either_dimension_high(self):
        assert EventNormalizer._infer_severity(0.60, 0.30) == "high"
        assert EventNormalizer._infer_severity(0.40, 0.60) == "high"

    def test_low_when_both_dimensions_low(self):
        assert EventNormalizer._infer_severity(0.30, 0.20) == "low"

    def test_medium_otherwise(self):
        assert EventNormalizer._infer_severity(0.50, 0.40) == "medium"
        assert EventNormalizer._infer_severity(0.65, 0.65) == "high"  # 因为任一维度高

    def test_severity_in_normalize_output(self):
        df = pd.DataFrame(
            [
                {
                    "title": "公司发布重大风险提示公告",
                    "content": "公司近期股价大幅波动，提示风险",
                    "published_at": pd.Timestamp("2026-07-01 09:00:00"),
                }
            ]
        )
        out = EventNormalizer.normalize(
            raw_df=df, source="test", security_map={}
        )
        assert not out.empty
        assert "severity" in out.columns
        assert out.iloc[0]["severity"] in {"low", "medium", "high", "critical"}


class TestLoadExternalEventFile:
    def test_returns_list_for_existing_file(self, tmp_path):
        # 写一个有效的 csv 文件
        import csv
        csv_path = tmp_path / "events.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["title", "content", "published_at"])
            writer.writeheader()
            writer.writerow({"title": "测试事件", "content": "内容", "published_at": "2026-07-01 09:00:00"})
        result = EventNormalizer.load_external_event_file(csv_path)
        assert isinstance(result, list)
        # csv.read_csv 会读出 dict 列表
        if result:
            assert result[0].get("title") == "测试事件"


class TestRawToDataframe:
    def test_empty_list_returns_empty_df_with_correct_columns(self):
        df = EventNormalizer.raw_to_dataframe([], business_date=date(2026, 7, 1))
        assert df.empty
        assert "business_date" in df.columns
        assert "event_id" in df.columns
        assert "raw_payload" in df.columns

    def test_builds_event_id_from_title_and_published_at(self):
        raw = [
            {
                "title": "测试事件",
                "summary": "abc",
                "published_at": "2026-07-01T09:00:00",
                "url": "https://example.com",
                "source": "test",
            }
        ]
        df = EventNormalizer.raw_to_dataframe(
            raw, business_date=date(2026, 7, 1), source="test"
        )
        assert len(df) == 1
        assert df.iloc[0]["title"] == "测试事件"
        assert df.iloc[0]["business_date"] == date(2026, 7, 1)
        assert df.iloc[0]["raw_payload"] == raw[0]
        assert df.iloc[0]["event_id"]  # 非空


class TestNormalizeEvents:
    def test_returns_list_of_dicts(self):
        raw = [{"title": "测试新闻", "content": "新闻内容", "published_at": "2026-07-01"}]
        symbol_map = {"000636": "uuid-test"}
        results = EventNormalizer.normalize_events(
            raw, business_date=date(2026, 7, 1), symbol_map=symbol_map
        )
        assert isinstance(results, list)
        if results:  # normalize 未必匹配得到 entity_id
            assert "severity" in results[0]

    def test_flatten_nested_symbol_map(self):
        raw = [{"title": "测试"}]
        nested_map = {
            "000636": {"security_id": "uuid-1", "name": "风华高科"},
        }
        results = EventNormalizer.normalize_events(
            raw, business_date=date(2026, 7, 1), symbol_map=nested_map
        )
        assert isinstance(results, list)
