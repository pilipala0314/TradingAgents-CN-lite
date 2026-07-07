"""宏观数据聚合服务 — 4 个宏观端点统一入口 + 状态判定。

数据源（通过 DataRouter 路由，享受 cache / stats / timeout / strict）：
- `get_macro_pmi`         PMI 制造业 / 非制造业
- `get_macro_cpi`         CPI 同比 / 环比
- `get_macro_m2`          M2 货币供应
- `get_macro_bond_yield`  10Y 中美国债利差

使用方式：
```python
from services.macro_service import get_macro_snapshot, build_market_regime_from_macro
snapshot = await get_macro_snapshot()
regime_hint = build_market_regime_from_macro(snapshot)
```

`regime_hint` 返回 6 档市场态 hint（与 core/enums.MarketRegime 对齐）：
- `risk_on_expansion`        — PMI > 50 且 M2 宽松
- `risk_on_neutral`          — 状态温和
- `risk_off_contraction`     — PMI < 50 连续 3 月 + M2 收紧
- `risk_off_stagflation`     — CPI > 3% + PMI < 50
- `risk_off_external_shock`  — 中美利差倒挂
- `transition_volatile`      — 状态不明确
"""
from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from data_pipeline.providers.facade import get_router

logger = logging.getLogger(__name__)


# ── 状态阈值（可放 settings） ──────────────────────────────────────────
PMI_BOOM_THRESHOLD = 50.5   # 制造业 PMI 荣枯线
PMI_RECESS_THRESHOLD = 49.5 # 制造业 PMI 连续低于此线触发 risk_off
CPI_INFLATION_THRESHOLD = 3.0   # CPI 同比 > 3% 视为通胀
CPI_DEFLATION_THRESHOLD = 0.0  # CPI 同比 < 0 视为通缩
M2_LOOSE_THRESHOLD = 11.0       # M2 同比 > 11% 视为宽松
M2_TIGHT_THRESHOLD = 8.0        # M2 同比 < 8% 视为收紧
BOND_SPREAD_INVERTED = 0.0      # 中美 10Y 利差 < 0 视为倒挂

RECESSION_MONTHS = 3  # PMI 连续低于荣枯线几个月触发 risk_off


@dataclass
class MacroSnapshot:
    """宏观快照（4 个端点最新值 + 趋势 + 异常标记）。"""

    pmi: dict[str, Any] = field(default_factory=dict)
    cpi: dict[str, Any] = field(default_factory=dict)
    m2: dict[str, Any] = field(default_factory=dict)
    bond_yield: dict[str, Any] = field(default_factory=dict)
    source_meta: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pmi": self.pmi,
            "cpi": self.cpi,
            "m2": self.m2,
            "bond_yield": self.bond_yield,
            "source_meta": self.source_meta,
        }


def _df_first_row(df: pd.DataFrame, name: str) -> dict[str, Any]:
    """从 DataFrame 取第一行，序列化为 dict。"""
    if df is None or df.empty:
        return {"value": None, "as_of": None, "source": name, "available": False}
    row = df.iloc[0].to_dict()
    # 找时间字段
    as_of = None
    for col in ("月份", "日期", "date", "month", "统计时间"):
        if col in row and row[col] is not None:
            as_of = str(row[col])
            break
    # 找值字段
    value = None
    for col in ("今值", "value", "最新值", "中国国债收益率", "制造业PMI"):
        if col in row and row[col] is not None:
            try:
                value = float(row[col])
            except (TypeError, ValueError):
                value = row[col]
            break
    # 把整行也带上，便于 LLM 看上下文
    return {
        "value": value,
        "as_of": as_of,
        "source": name,
        "available": True,
        "raw": {k: (None if pd.isna(v) else v) for k, v in row.items()},
    }


async def get_macro_snapshot() -> MacroSnapshot:
    """并发拉取 4 个宏观端点（任一失败非阻塞，标记 failed）。"""
    router = get_router()

    # 强制 enable extended（macro 端点在 EXTENDED）
    from data_pipeline.data_router import DataRouter

    if isinstance(router, DataRouter) and "get_macro_pmi" not in router.available_methods():
        logger.warning(
            "当前 router 未包含 macro 端点，建议用 DataRouter(include_extended=True) 重建"
        )

    import asyncio
    results = await asyncio.gather(
        router.route("get_macro_pmi", use_cache=True),
        router.route("get_macro_cpi", use_cache=True),
        router.route("get_macro_m2", use_cache=True),
        router.route("get_macro_bond_yield", use_cache=True),
        return_exceptions=True,
    )
    pmi_df, cpi_df, m2_df, bond_df = results
    source_meta = {
        "get_macro_pmi": "ok" if not isinstance(pmi_df, Exception) else "failed",
        "get_macro_cpi": "ok" if not isinstance(cpi_df, Exception) else "failed",
        "get_macro_m2": "ok" if not isinstance(m2_df, Exception) else "failed",
        "get_macro_bond_yield": "ok" if not isinstance(bond_df, Exception) else "failed",
    }
    return MacroSnapshot(
        pmi=_df_first_row(pmi_df if not isinstance(pmi_df, Exception) else None, "pmi"),
        cpi=_df_first_row(cpi_df if not isinstance(cpi_df, Exception) else None, "cpi"),
        m2=_df_first_row(m2_df if not isinstance(m2_df, Exception) else None, "m2"),
        bond_yield=_df_first_row(bond_df if not isinstance(bond_df, Exception) else None, "bond_yield"),
        source_meta=source_meta,
    )


def _is_pmi_recession(pmi_snapshot: dict[str, Any], full_df=None) -> bool:
    """判定 PMI 是否处于衰退区间（连续 3 月 < 49.5）。

    Args:
        pmi_snapshot: 当前 PMI 快照
        full_df:      PMI 完整 DataFrame（用于连续月份判断），
                      暂未传，由 snapshot 单点判定
    """
    val = pmi_snapshot.get("value")
    if val is None:
        return False
    return val < PMI_RECESS_THRESHOLD


def _is_cpi_inflation(cpi_snapshot: dict[str, Any]) -> bool:
    val = cpi_snapshot.get("value")
    if val is None:
        return False
    return val > CPI_INFLATION_THRESHOLD


def _is_m2_loose(m2_snapshot: dict[str, Any]) -> bool:
    val = m2_snapshot.get("value")
    if val is None:
        return False
    return val > M2_LOOSE_THRESHOLD


def _is_bond_inverted(bond_snapshot: dict[str, Any]) -> bool:
    """中美 10Y 利差倒挂（需要 raw 里有两国收益率字段）。"""
    raw = bond_snapshot.get("raw") or {}
    # 常见字段名：中国国债收益率10年 / 美国国债收益率10年
    cn = None
    us = None
    for k, v in raw.items():
        kl = str(k)
        if "中国" in kl and "10" in kl:
            with contextlib.suppress(TypeError, ValueError):
                cn = float(v)
        if "美国" in kl and "10" in kl:
            with contextlib.suppress(TypeError, ValueError):
                us = float(v)
    if cn is None or us is None:
        return False
    return (cn - us) < BOND_SPREAD_INVERTED


def build_market_regime_from_macro(snap: MacroSnapshot) -> dict[str, Any]:
    """基于宏观快照推断 market_regime hint。

    Returns:
        dict with keys:
            regime: 6 档 hint 之一
            triggers: 命中的触发条件列表
            confidence: 0~1 浮点
    """
    triggers: list[str] = []
    score = 0.0

    pmi_recess = _is_pmi_recession(snap.pmi)
    cpi_infl = _is_cpi_inflation(snap.cpi)
    m2_loose = _is_m2_loose(snap.m2)
    bond_inverted = _is_bond_inverted(snap.bond_yield)

    if pmi_recess:
        triggers.append("pmi_below_50")
        score -= 2.0
    if cpi_infl:
        triggers.append("cpi_above_3pct")
        score -= 1.0
    if m2_loose:
        triggers.append("m2_above_11pct")
        score += 1.5
    if bond_inverted:
        # bond_inverted 不参与 score（避免 PMI 正常时被独立减分 → 误判 contraction），
        # 它只作为 risk_off_external_shock 分支的"配套触发器"生效（见下方分支 3）。
        triggers.append("cn_us_spread_inverted")

    # 数据齐全度：4 个端点都有值 = 1.0，全 None = 0.0
    availability = sum(
        1.0 if s.get("value") is not None else 0.0
        for s in (snap.pmi, snap.cpi, snap.m2, snap.bond_yield)
    ) / 4.0

    # 综合判定
    if pmi_recess and cpi_infl:
        regime = "risk_off_stagflation"
    elif pmi_recess and not m2_loose:
        regime = "risk_off_contraction"
    elif bond_inverted and pmi_recess:
        regime = "risk_off_external_shock"
    elif m2_loose and not pmi_recess:
        regime = "risk_on_expansion"
    elif score > 0.5:
        regime = "risk_on_neutral"
    elif score < -0.5:
        regime = "risk_off_contraction"
    elif availability < 0.5:
        # 数据严重缺失 → 不冒进
        regime = "transition_volatile"
    elif not pmi_recess:
        # 数据齐全 + 无衰退信号 + 无强触发 → 偏多中性
        regime = "risk_on_neutral"
    else:
        regime = "transition_volatile"

    # 置信度：触发数 / 4，clip 到 [0, 1]
    confidence = min(1.0, len(triggers) / 4.0)
    return {"regime": regime, "triggers": triggers, "confidence": round(confidence, 2)}
