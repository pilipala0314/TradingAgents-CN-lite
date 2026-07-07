import pandas as pd

from features_engine.calculators.sector_features import SectorFeatureCalculator


def test_build_sector_snapshot_derives_stage_and_mainline_flag():
    sector_df = pd.DataFrame(
        {
            "sector_id": ["s1", "s2"],
            "sector_turnover": [150.0, 15.0],
            "sector_return": [4.2, 0.8],
            "rising_count": [8, 1],
            "component_count": [10, 8],
            "leader_change_pct": [9.5, 1.2],
        }
    )

    snapshots = SectorFeatureCalculator.build_sector_snapshot(
        sector_df=sector_df,
        market_turnover=1000.0,
        market_regime="risk_on_expansion",
        business_date="2026-07-01",
        window_type="close",
    )

    first_row = snapshots.iloc[0]
    second_row = snapshots.iloc[1]

    assert first_row["sector_stage"] in {"confirmed", "strengthening"}
    assert bool(first_row["mainline_flag"]) is True
    assert second_row["sector_stage"] == "watching"
    assert "event_freshness_score" in snapshots.columns
    assert "institution_bias_score" in snapshots.columns
