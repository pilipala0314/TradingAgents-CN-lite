from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import Base


class FeatureMarketSnapshot(Base):
    __tablename__ = "feature_market_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "business_date",
            "window_type",
            "feature_version",
            name="uq_feature_market_snapshot_business_window_version",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    business_date = Column(Date, nullable=False)
    window_type = Column(String(16), nullable=False)
    market_sentiment_cycle = Column(String(16))
    total_turnover = Column(Numeric(24, 4))
    advancers = Column(Integer)
    decliners = Column(Integer)
    limit_up_count = Column(Integer)
    limit_down_count = Column(Integer)
    max_board_height = Column(Integer)
    northbound_net_inflow = Column(Numeric(24, 4))
    market_regime = Column(String(32))
    risk_preference_regime = Column(String(16))
    risk_preference_score = Column(Numeric(8, 4))
    style_bias = Column(String(16))
    style_confidence = Column(Numeric(8, 4))
    liquidity_regime = Column(String(16))
    liquidity_score = Column(Numeric(8, 4))
    high_level_loss_effect_score = Column(Numeric(8, 4))
    theme_survival_score = Column(Numeric(8, 4))
    state_support_mode = Column(String(16))
    state_support_proxy_score = Column(Numeric(8, 4))
    broad_etf_support_score = Column(Numeric(8, 4))
    large_cap_stabilization_score = Column(Numeric(8, 4))
    bank_shield_strength_score = Column(Numeric(8, 4))
    dividend_defense_score = Column(Numeric(8, 4))
    index_stock_divergence_score = Column(Numeric(8, 4))
    tail_lift_intervention_score = Column(Numeric(8, 4))
    theme_suppression_score = Column(Numeric(8, 4))
    bomb_rate = Column(Numeric(8, 4))
    reseal_rate = Column(Numeric(8, 4))
    one_board_ratio = Column(Numeric(8, 4))
    turnover_board_ratio = Column(Numeric(8, 4))
    prev_zt_next_day_return = Column(Numeric(8, 4))
    limit_up_streak_distribution = Column(JSONB)
    feature_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class FeatureSectorSnapshot(Base):
    __tablename__ = "feature_sector_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "sector_id",
            "business_date",
            "window_type",
            "feature_version",
            name="uq_feature_sector_snapshot_sector_date_window_version",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sector_id = Column(UUID(as_uuid=True), nullable=False)
    business_date = Column(Date, nullable=False)
    window_type = Column(String(16), nullable=False)
    sector_return = Column(Numeric(12, 6))
    turnover_share = Column(Numeric(12, 6))
    turnover_share_delta = Column(Numeric(12, 6))
    turnover_share_trend = Column(Numeric(8, 4))
    sync_move_ratio = Column(Numeric(8, 4))
    net_flow_acceleration = Column(Numeric(12, 6))
    leader_strength_score = Column(Numeric(8, 4))
    breadth_score = Column(Numeric(8, 4))
    ladder_integrity_score = Column(Numeric(8, 4))
    continuation_score = Column(Numeric(8, 4))
    rotation_speed_score = Column(Numeric(8, 4))
    profit_effect_spread_score = Column(Numeric(8, 4))
    high_low_coordination_score = Column(Numeric(8, 4))
    branch_rotation_health_score = Column(Numeric(8, 4))
    sentiment_amplification_score = Column(Numeric(8, 4))
    sentiment_divergence_score = Column(Numeric(8, 4))
    follow_through_quality_score = Column(Numeric(8, 4))
    persistence_score = Column(Numeric(8, 4))
    consensus_score = Column(Numeric(8, 4))
    turnover_share_stability = Column(Numeric(8, 4))
    turnover_share_fall_score = Column(Numeric(8, 4))
    leader_decay_score = Column(Numeric(8, 4))
    breadth_contraction_score = Column(Numeric(8, 4))
    high_level_loss_effect_score = Column(Numeric(8, 4))
    risk_event_pressure_score = Column(Numeric(8, 4))
    market_regime_mismatch_score = Column(Numeric(8, 4))
    capital_rotation_out_score = Column(Numeric(8, 4))
    event_freshness_score = Column(Numeric(8, 4))
    event_sector_mapping_score = Column(Numeric(8, 4))
    leader_ignition_score = Column(Numeric(8, 4))
    institution_bias_score = Column(Numeric(8, 4))
    state_fund_signal_score = Column(Numeric(8, 4))
    stabilization_benefit_score = Column(Numeric(8, 4))
    theme_crowd_out_score = Column(Numeric(8, 4))
    sector_stage = Column(String(16))
    mainline_flag = Column(Boolean)
    feature_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class FeatureStockSnapshot(Base):
    __tablename__ = "feature_stock_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "business_date",
            "window_type",
            "feature_version",
            name="uq_feature_stock_snapshot_security_date_window_version",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    security_id = Column(UUID(as_uuid=True), nullable=False)
    business_date = Column(Date, nullable=False)
    window_type = Column(String(16), nullable=False)
    relative_strength_score = Column(Numeric(8, 4))
    liquidity_score = Column(Numeric(8, 4))
    volatility_score = Column(Numeric(8, 4))
    crowding_score = Column(Numeric(8, 4))
    valuation_percentile = Column(Numeric(8, 4))
    announcement_risk_score = Column(Numeric(8, 4))
    concept_purity_score = Column(Numeric(8, 4))
    auction_signal = Column(String(32))
    open_gap_pct = Column(Numeric(8, 4))
    open_direction = Column(String(16))
    open_high_drop = Column(Boolean)
    open_low_reversal = Column(Boolean)
    first_yin_after_streak = Column(Boolean)
    streak_break = Column(Boolean)
    reversal_board = Column(Boolean)
    weak_to_strong = Column(Boolean)
    rotten_board_reseal = Column(Boolean)
    a_kill = Column(Boolean)
    position_steal = Column(Boolean)
    laggard_rise = Column(Boolean)
    limit_up_to_limit_down = Column(Boolean)
    limit_down_to_limit_up = Column(Boolean)
    lhb_listed = Column(Boolean)
    lhb_net_buy = Column(Numeric(16, 4))
    lhb_inst_buy_ratio = Column(Numeric(8, 4))
    lhb_inst_sell_ratio = Column(Numeric(8, 4))
    lhb_famous_buy_flag = Column(Boolean)
    lhb_famous_sell_flag = Column(Boolean)
    limit_type = Column(String(16))
    is_st = Column(Boolean)
    abnormal_halt_risk = Column(Boolean)
    feature_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
