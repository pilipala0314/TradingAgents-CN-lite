from typing import Any

from prompts.base import compose_prompt
from schemas.agent_io import MarketRegimeOutput, SectorStageOutput, StockTacticalOutput

_MARKET_ROLE_PROMPT = """你是中国 A 股市场的宏观量化分析师（Market Regime Analyst）。

你只能做市场层判断，不能替代板块准入排序或个股交易决策。

市场阶段只允许从以下五个标签中二选一输出：
1. risk_on_expansion: 扩张期
2. risk_on_rotation: 轮动期
3. neutral_mixed: 震荡/混沌期
4. risk_off_stabilized: 防守抵抗期
5. risk_off_panic: 恐慌冰点期

请特别关注 A 股特有信号：炸板率、连板高度、国家队托底代理指标、指数与个股背离、题材存活率。
不要被单一指数涨跌误导；如果出现“权重维稳但题材亏钱效应扩散”，应优先识别为偏防守状态。"""

_SECTOR_ROLE_PROMPT = """你是中国 A 股市场的板块生命周期分析师（Sector Analyst）。

你只能给出板块阶段命名与主线属性判断，不能做最终排序准入。

板块生命周期只允许从以下六个标签中选择：
1. watching: 观察期
2. emerging: 启动期
3. confirmed: 确认期
4. strengthening: 强化/高潮期
5. diverging: 分歧期
6. decaying: 衰退期

请重点审查：起点、确认、强化、退出四类指标是否真正成立，尤其要把“提前离场”放在“事后解释”之前。"""


def render_market_analyst_prompt(
    market_snapshot: dict[str, Any],
    rule_hits: list[dict[str, Any]],
) -> tuple[str, str]:
    return compose_prompt(
        role_instruction=_MARKET_ROLE_PROMPT,
        schema=MarketRegimeOutput,
        task_instruction="请基于市场快照与规则命中，评估当前市场阶段并输出结构化结论。",
        context_blocks={
            "市场特征快照": market_snapshot,
            "触发的风控规则": rule_hits or [],
        },
    )


def render_sector_analyst_prompt(
    sector_snapshot: dict[str, Any],
    market_regime: str,
    rule_hits: list[dict[str, Any]],
) -> tuple[str, str]:
    sector_role_prompt = (
        f"{_SECTOR_ROLE_PROMPT}\n\n"
        f"当前市场整体阶段为：{market_regime}。\n"
        "必须服从上位约束：在 risk_off_stabilized 或 risk_off_panic 阶段，"
        "板块极难进入 confirmed 或 strengthening，审核标准必须显著提高。"
    )
    return compose_prompt(
        role_instruction=sector_role_prompt,
        schema=SectorStageOutput,
        task_instruction="请基于板块快照、市场阶段与规则命中，评估该板块生命周期并输出结构化结论。",
        context_blocks={
            "市场阶段": {"market_regime": market_regime},
            "板块特征快照": sector_snapshot,
            "触发的板块级风控规则": rule_hits or [],
        },
    )


_STOCK_ROLE_PROMPT = """你是中国 A 股市场的个股战术分析师（Stock Analyst）。
你只针对单只标的做战术判断，不替代板块主线识别或市场阶段判断。
必须严格服从上位约束：市场阶段和所属板块生命周期是硬前提；当下位信号与上位冲突时，
应明确标记 upstream_alignment=conflicting 并降低 conviction_score。

请重点审查：
1. 战术标签（streak_break / first_yin_after_streak / reversal_board / weak_to_strong / a_kill / limit_up_to_limit_down / limit_down_to_limit_up）是否真实成立。
2. 资金信号（lhb_inst_buy_ratio, lhb_famous_buy_flag, crowding_score）是否支持当前位置。
3. 公告与事件风险（announcement_risk_score, is_st, abnormal_halt_risk）。
4. 估值百分位（valuation_percentile）与趋势方向的匹配度。
5. 严格执行提前离场：bubble 信号、首阴、A 杀、天地板等应优先于“再创新高”叙事。

请使用 A 股术语（连板、断板、炸板、A 杀、地天板、弱转强、龙头/跟风/补涨）输出判断。"""


def render_stock_analyst_prompt(
    stock_snapshot: dict[str, Any],
    market_regime: str,
    sector_stage: str,
    rule_hits: list[dict[str, Any]],
) -> tuple[str, str]:
    stock_role_prompt = (
        f"{_STOCK_ROLE_PROMPT}\n\n"
        f"当前市场整体阶段为：{market_regime}。\n"
        f"该个股所属板块当前生命周期为：{sector_stage}。\n"
        "必须服从上位约束：\n"
        "- 当市场处于 risk_off_panic 时，无论个股技术形态如何，risk_mode 应为 avoid。\n"
        "- 当板块处于 watching / decaying 时，趋势与动量信号应大幅折损。\n"
        "- 当板块处于 strengthening / confirmed 且市场偏进攻时，板块龙头可被赋予更高 conviction。"
    )
    return compose_prompt(
        role_instruction=stock_role_prompt,
        schema=StockTacticalOutput,
        task_instruction="请基于个股特征快照、上位市场/板块约束与个股级风控规则，输出可执行的战术结论。",
        context_blocks={
            "市场阶段": {"market_regime": market_regime},
            "板块阶段": {"sector_stage": sector_stage},
            "个股特征快照": stock_snapshot,
            "触发的个股级风控规则": rule_hits or [],
        },
    )
