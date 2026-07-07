

from pydantic import BaseModel, Field

# ==========================================
# 分析 Agent 的标准化输入输出契约
# ==========================================

class AgentOutputEnvelope(BaseModel):
    """
    所有分析 Agent 统一输出的包裹器 (Envelope)。
    强制 Agent 将结论分离为结构化数据、叙事总结、证据引用等。
    """
    agent_name: str = Field(..., description="Agent的名称")
    structured_output: dict = Field(..., description="结构化的核心结论键值对")
    narrative_summary: str = Field(..., description="供人类阅读的自然语言叙事总结")
    evidence_refs: list[str] = Field(default_factory=list, description="所引用事件或特征的ID列表")
    confidence: float = Field(..., ge=0.0, le=1.0, description="结论置信度")
    warnings: list[str] = Field(default_factory=list, description="风险提示或不确定性声明")
    output_version: int = Field(default=1, description="输出契约版本")
    # P4.4 — 决策 trace 落地（committee 等"最终决策者"用）:
    # 存 "decision_trace" / "context_snapshot" 等不影响契约的元数据，方便事后审计与归因。
    extra_metadata: dict = Field(
        default_factory=dict,
        description="非契约元数据（决策 trace / 上下文快照等），落库时由 persister 读取",
    )

class MarketRegimeOutput(BaseModel):
    """
    Market Regime Analyst 输出契约
    """
    market_regime: str = Field(..., description="市场阶段: risk_on_expansion, risk_on_rotation, neutral_mixed, risk_off_stabilized, risk_off_panic")
    risk_mode: str = Field(..., description="防守或进攻建议")
    style_rotation_hint: str = Field(..., description="风格轮动倾向 (如大盘/小盘/题材)")
    state_support_mode: str = Field(..., description="维稳托底模式强度")


class MacroInterpretationOutput(BaseModel):
    """P0-T6: Macro Interpretation Agent 输出契约。

    解读 4 个宏观指标（PMI / CPI / M2 / 中美利差），给出整体立场和分项观点。
    """
    overall_stance: str = Field(
        ...,
        description="整体立场: aggressive(进攻) / cautious(谨慎乐观) / neutral(中性) / defensive(防守) / panic(恐慌)",
    )
    summary: str = Field(..., description="3-5 句话的整体解读")
    pmi_view: str = Field(..., description="PMI 解读（1-2 句）")
    inflation_view: str = Field(..., description="CPI/PPI 通胀解读（1-2 句）")
    liquidity_view: str = Field(..., description="M2/社融流动性解读（1-2 句）")
    rate_spread_view: str = Field(..., description="中美利差解读（1-2 句）")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度 0-1")
    caveats: list[str] = Field(default_factory=list, description="风险/不确定性说明")

class SectorStageOutput(BaseModel):
    """
    Sector Analyst 输出契约
    """
    sector_stage: str = Field(..., description="板块生命周期: watching, emerging, confirmed, strengthening, diverging, decaying")
    mainline_flag: bool = Field(..., description="是否属于市场核心主线")
    sector_score: float = Field(..., ge=0.0, le=1.0, description="板块绝对强度评分")
    stage_evidence: str = Field(..., description="当前所处阶段的证据")

class TechnicalAnalysisOutput(BaseModel):
    """
    Tech Analyst 输出契约
    """
    entry_zone: list[float] = Field(..., description="建议买入区间 [low, high]")
    breakout_level: float = Field(..., description="关键突破阻力位")
    stop_loss_hint: float = Field(..., description="建议止损位")
    technical_risk_flags: list[str] = Field(default_factory=list, description="技术面风险(如顶背离)")


class StockTacticalOutput(BaseModel):
    """
    Stock Analyst 输出契约
    接收个股特征快照 + 上位市场/板块约束，输出可执行的战术判断。
    """

    trend_signal: str = Field(
        ...,
        description="趋势方向: bullish / neutral / bearish",
    )
    entry_timing: str = Field(
        ...,
        description="入场时机: now / wait_pullback / wait_breakout / avoid",
    )
    risk_mode: str = Field(
        ...,
        description="风险模式: aggressive / balanced / defensive / avoid",
    )
    position_role: str = Field(
        ...,
        description="在所属板块中的角色: leader / follower / laggard",
    )
    conviction_score: float = Field(..., ge=0.0, le=1.0, description="做多/做空置信度")
    support_level: float = Field(..., description="关键技术支撑位 (单位: 元)")
    resistance_level: float = Field(..., description="关键技术阻力位 (单位: 元)")
    stop_loss_hint: float = Field(..., description="建议止损位 (单位: 元)")
    tactical_flags: list[str] = Field(
        default_factory=list,
        description="战术标签: streak_break / first_yin_after_streak / reversal_board / weak_to_strong / a_kill / limit_up_to_limit_down 等",
    )
    key_evidence: str = Field(..., description="核心证据摘要，供人类阅读")
    upstream_alignment: str = Field(
        ...,
        description="与上位市场/板块状态的契合度: aligned / conflicting / neutral",
    )
