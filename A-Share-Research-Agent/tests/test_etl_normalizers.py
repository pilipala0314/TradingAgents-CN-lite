from datetime import date
from uuid import UUID

import pandas as pd

from data_pipeline.normalizers.capital_flow_normalizer import CapitalFlowNormalizer
from data_pipeline.normalizers.event_normalizer import EventNormalizer
from data_pipeline.normalizers.market_bar_normalizer import MarketBarNormalizer
from data_pipeline.normalizers.sector_normalizer import SectorNormalizer
from data_pipeline.normalizers.security_normalizer import SecurityNormalizer


def test_security_normalizer_from_spot_list():
    raw_df = pd.DataFrame(
        [
            {"代码": "000001", "名称": "平安银行"},
            {"代码": "600519", "名称": "贵州茅台"},
        ]
    )

    normalized_df = SecurityNormalizer.from_spot_list(raw_df)

    assert list(normalized_df["symbol"]) == ["000001", "600519"]
    assert list(normalized_df["exchange"]) == ["sz", "sh"]
    assert normalized_df["security_type"].nunique() == 1


def test_market_bar_normalizer_from_history():
    raw_df = pd.DataFrame(
        [
            {
                "日期": "2026-07-01",
                "开盘": 10.1,
                "收盘": 10.6,
                "最高": 10.8,
                "最低": 10.0,
                "成交量": 123456,
                "成交额": 998877,
                "换手率": 2.35,
            }
        ]
    )

    normalized_df = MarketBarNormalizer.from_history(
        raw_df=raw_df,
        security_id=UUID("11111111-1111-1111-1111-111111111111"),
        source="test_source",
        bar_timeframe="1d",
    )

    record = normalized_df.iloc[0].to_dict()
    assert str(record["security_id"]) == "11111111-1111-1111-1111-111111111111"
    assert record["bar_timeframe"] == "1d"
    assert str(record["close"]) == "10.6"
    assert record["bar_date"].isoformat() == "2026-07-01"


def test_capital_flow_normalizer_market_and_northbound():
    market_df = pd.DataFrame([{"主力净流入-净额": 12.5, "今日超大单净流入-净额": 6.4}])
    northbound_df = pd.DataFrame([{"今日净流入": 30.0, "沪股通": 18.0, "深股通": 12.0}])

    market_result = CapitalFlowNormalizer.market_main_force(
        market_df,
        business_date=date(2026, 7, 1),
        source="test_market",
    )
    northbound_result = CapitalFlowNormalizer.northbound_summary(
        northbound_df,
        business_date=date(2026, 7, 1),
        source="test_northbound",
    )

    assert market_result.iloc[0]["flow_type"] == "main_force"
    assert northbound_result.iloc[0]["flow_type"] == "northbound"
    assert str(northbound_result.iloc[0]["net_inflow"]) == "30.0"


def test_event_normalizer_infers_scope_type_and_stable_entity():
    raw_df = pd.DataFrame(
        [
            {
                "title": "证监会发布并购重组新政",
                "发布时间": "2026-07-01 20:15:00",
                "summary": "鼓励优质资产注入上市公司。",
                "source_url": "https://example.com/policy",
            },
            {
                "title": "平安银行发布业绩预增公告",
                "symbol": "000001",
                "published_at": "2026-07-01 21:00:00",
            },
        ]
    )

    normalized_df = EventNormalizer.normalize(
        raw_df=raw_df,
        source="local_file",
        security_map={"000001": UUID("22222222-2222-2222-2222-222222222222")},
    )

    first_record = normalized_df.iloc[0].to_dict()
    second_record = normalized_df.iloc[1].to_dict()

    assert first_record["event_scope"] == "macro"
    assert first_record["event_type"] == "policy"
    assert first_record["entity_id"] is not None
    assert second_record["event_scope"] == "security"
    assert str(second_record["entity_id"]) == "22222222-2222-2222-2222-222222222222"


def test_sector_normalizer_builds_sector_master_and_mapping():
    board_df = pd.DataFrame([{"板块名称": "半导体", "板块代码": "BK1234"}])
    component_df = pd.DataFrame([{"代码": "000001"}, {"代码": "600519"}])

    sector_df = SectorNormalizer.from_board_list(
        board_df,
        sector_type="industry",
        source="test_source",
    )
    mapping_df = SectorNormalizer.build_mapping(
        component_df,
        security_map={
            "000001": UUID("11111111-1111-1111-1111-111111111111"),
            "600519": UUID("33333333-3333-3333-3333-333333333333"),
        },
        sector_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        business_date=date(2026, 7, 1),
    )

    assert sector_df.iloc[0]["sector_name"] == "半导体"
    assert sector_df.iloc[0]["sector_type"] == "industry"
    assert len(mapping_df) == 2
    assert str(mapping_df.iloc[0]["sector_id"]) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_event_normalizer_supports_sector_scope_binding():
    raw_df = pd.DataFrame(
        [
            {
                "title": "半导体板块受政策催化持续走强",
                "板块名称": "半导体",
                "published_at": "2026-07-01 19:30:00",
            }
        ]
    )

    normalized_df = EventNormalizer.normalize(
        raw_df=raw_df,
        source="local_file",
        sector_map={"半导体": UUID("44444444-4444-4444-4444-444444444444")},
    )

    assert normalized_df.iloc[0]["event_scope"] == "sector"
    assert str(normalized_df.iloc[0]["entity_id"]) == "44444444-4444-4444-4444-444444444444"
