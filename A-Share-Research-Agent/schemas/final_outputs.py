
from pydantic import BaseModel, Field

# ==========================================
# 最终交付的系统级输出 Schema
# ==========================================

class MarketOutlook(BaseModel):
    """
    针对全市场/指数的最终判断
    """
    entity_type: str = "market"
    entity_id: str = "cn_a_share"
    business_date: str = Field(..., description="业务日期")
    window_type: str = Field(..., description="时间窗")
    horizon: str = Field(..., description="时间跨度: T+1, T+7, T+30")
    market_regime: str = Field(...)
    risk_preference_regime: str = Field(...)
    liquidity_regime: str = Field(...)
    direction: str = Field(..., description="bullish, neutral, bearish")
    expected_index_return_range: list[float] = Field(..., description="[low, high]")
    expected_volatility_range: list[float] = Field(..., description="[low, high]")
    drawdown_risk_range: list[float] = Field(..., description="[low, high]")
    style_bias: str = Field(...)
    state_support_mode: str = Field(...)
    preferred_buckets: list[str] = Field(default_factory=list)
    avoid_buckets: list[str] = Field(default_factory=list)
    macro_constraints: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(default_factory=list)
    invalid_conditions: list[str] = Field(default_factory=list)
    output_version: int = 1

class SectorOutlook(BaseModel):
    """
    针对板块/概念的最终判断
    """
    entity_type: str = "sector"
    entity_id: str = Field(...)
    business_date: str = Field(...)
    window_type: str = Field(...)
    horizon: str = Field(...)
    direction: str = Field(...)
    sector_stage: str = Field(...)
    strength_score: float = Field(..., ge=0.0, le=1.0)
    continuation_score: float = Field(..., ge=0.0, le=1.0)
    rotation_speed_score: float = Field(..., ge=0.0, le=1.0)
    expected_return_range: list[float]
    expected_drawdown_range: list[float]
    leading_stocks: list[str] = Field(default_factory=list)
    core_catalysts: list[str] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)
    action_bias: str = Field(...)
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(default_factory=list)
    invalid_conditions: list[str] = Field(default_factory=list)
    output_version: int = 1

class SecurityStrategy(BaseModel):
    """
    针对单只个股的最终策略方案
    """
    asset_type: str = "stock"
    entity_type: str = "security"
    entity_id: str = Field(...)
    symbol: str = Field(...)
    business_date: str = Field(...)
    window_type: str = Field(...)
    horizon: str = Field(...)
    direction: str = Field(...)
    strategy_type: str = Field(...)
    tactical_label: str = Field(default="")
    entry_mode: str = Field(...)
    expected_return_range: list[float]
    expected_drawdown_range: list[float]
    volatility_range: list[float]
    entry_plan: dict = Field(default_factory=dict)
    add_plan: dict = Field(default_factory=dict)
    reduce_plan: dict = Field(default_factory=dict)
    exit_plan: dict = Field(default_factory=dict)
    position_plan: dict = Field(default_factory=dict)
    supporting_evidence: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    intraday_risk_flags: list[str] = Field(default_factory=list)
    fundamental_view: str | None = None
    macro_constraints: list[str] = Field(default_factory=list)
    invalid_conditions: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    output_version: int = 1

class RiskAlert(BaseModel):
    """
    独立下发的风险预警事件
    """
    entity_type: str = Field(...)
    entity_id: str = Field(...)
    business_date: str = Field(...)
    window_type: str = Field(...)
    alert_type: str = Field(...)
    alert_level: str = Field(..., description="low, medium, high")
    risk_scope: str = Field(...)
    headline: str = Field(...)
    impact_horizons: list[str] = Field(default_factory=list)
    recommended_action: str = Field(...)
    max_position: float | None = None
    description: str = Field(...)
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(default_factory=list)
    output_version: int = 1
