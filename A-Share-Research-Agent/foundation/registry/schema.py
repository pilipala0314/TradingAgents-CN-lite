"""因子注册表 schema — 指标 → 数据需求的声明式映射。

这是「设计时 AI 产物」的落地形式：每个因子声明它需要哪些数据 API、
如何取值、何时可用、多源如何融合。**运行时纯代码查表调 route()，AI 不参与。**

设计要点：
1. **一张表承载两个信息**：指标→API 映射 + 数据可用性（披露时点），
   避免两份声明不同步。
2. **多源关系显式化**：`sources` 列出全部候选源，融合策略写在 `fusion`，
   这是"多因子/多源交叉验证"的机制基础。
3. **口径不合并**：不同口径作为**独立因子**并列（各自 caliber），
   不在注册表里做归一。
4. **可对账**：`method` / `provider` 会被 loader 校验存在于
   `datasource.method_table.FULL_METHOD_TABLE`，防止声明漂移。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from foundation.schemas.common import Caliber
from foundation.schemas.factor import FactorDimension


class DisclosureProfile(str, Enum):
    """数据披露档位 — 决定拉取时点与是否二次刷新。

    - AFTER_CLOSE   收盘后即可（K 线、板块排名、涨停池）
    - EVENING       傍晚陆续披露（资金流 16:00-18:00）
    - EXCHANGE      交易所披露窗口（龙虎榜 17:00-19:00、北向 17:00+）
    - NEXT_DAY      T+1 才有（晚间公告、财报），盘后拉取必然 UNAVAILABLE_YET
    """

    AFTER_CLOSE = "after_close"
    EVENING = "evening"
    EXCHANGE = "exchange"
    NEXT_DAY = "next_day"


class ExtractKind(str, Enum):
    """取值方式 — 决定 `field` / `match` 的必填语义。

    - VALUE  取单个字段值           → field 必填
    - SUM    对匹配行的字段求和      → field 必填，match 建议
    - COUNT  对匹配行计数           → field 空，match 可选
    - WHOLE  整体取用（列表/整表）  → field 空；由指标层后续加工
    """

    VALUE = "value"
    SUM = "sum"
    COUNT = "count"
    WHOLE = "whole"


# 合法 24 小时制时分格式：HH:MM（00:00-23:59）
_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class AvailabilitySpec(BaseModel):
    """数据可用性 — 两轮拉取编排的依据。"""

    profile: DisclosureProfile
    available_from: str = Field(
        default="", pattern=_TIME_PATTERN,
        description="期望可用时点（本地 HH:MM），如 '15:30' / '18:00'",
    )
    second_round: bool = Field(
        default=False,
        description="是否需要二次刷新（盘后首轮拿不到的延迟数据）",
    )
    second_round_at: str | None = Field(
        default=None, pattern=_TIME_PATTERN,
        description="二次刷新时点，如 '20:30'；second_round=True 时必填",
    )
    note: str = Field(default="", description="备注，如 '交易所 19:00 披露，可能延迟'")

    def model_post_init(self, __context) -> None:
        if self.second_round and not self.second_round_at:
            raise ValueError(f"second_round=True 时必须指定 second_round_at（note={self.note!r}）")


class SourceSpec(BaseModel):
    """单个数据源声明 — 因子 → API 的映射单元。

    `field` / `match` 用 `{var}` 模板占位，运行时由调用方注入
    （如 `{sector}` → "半导体"），不在此处做参数解析。
    """

    provider: str = Field(description="provider 名，如 'eastmoney'")
    method: str = Field(description="route() 方法名，如 'get_sector_fund_flow'")
    caliber: Caliber = Field(description="该源的口径来源")

    args: dict[str, object] = Field(
        default_factory=dict,
        description="传给 route() 的参数；值可含 {var} 模板占位",
    )
    extract_kind: ExtractKind = Field(
        default=ExtractKind.VALUE,
        description="取值方式；决定 field/match 的必填语义",
    )
    field: str = Field(
        default="",
        description="取值字段（DataFrame 列 / dict 键）；VALUE/SUM 必填，COUNT/WHOLE 留空",
    )
    match: dict[str, str] = Field(
        default_factory=dict,
        description="行匹配条件，如 {'board_name': '{sector}'}；用于从列表中筛选目标行",
    )
    unit_scale: float = Field(
        default=1.0,
        description="取值后乘以该系数做单位换算（如 元→亿 = 1e-8）",
    )
    priority: int = Field(
        default=0, ge=0,
        description="同因子内源的优先级，0 最高；single/first 策略下决定取谁",
    )
    note: str = Field(
        default="",
        description="备注；含'待校准'表示取值路径尚未实测确认",
    )

    @property
    def extraction_consistent(self) -> bool:
        """extract_kind 与 field/match 是否自洽（不看是否已实测）。"""
        if self.extract_kind in (ExtractKind.VALUE, ExtractKind.SUM):
            return bool(self.field)
        return True  # COUNT/WHOLE 不要求 field

    @property
    def is_calibrated(self) -> bool:
        """取值路径是否已实测校准。

        两个条件同时满足：
        1. extract_kind 与 field/match 自洽（VALUE/SUM 必须有 field）
        2. note 未标注「待校准」
        """
        return self.extraction_consistent and "待校准" not in self.note


class FusionStrategy(str, Enum):
    """多源取值合成策略（合成前先做单位归一）。"""

    SINGLE = "single"  # 单源，无合成
    FIRST = "first"  # 按 priority 取第一个成功的
    MEDIAN = "median"  # 中位数（抗离群）
    MEAN = "mean"  # 均值
    VOTE = "vote"  # 众数（用于枚举/标签类）
    UNION_DEDUP = "union_dedup"  # 并集去重（用于列表类，如新闻/榜单）


class FusionSpec(BaseModel):
    """多源融合策略。"""

    strategy: FusionStrategy = FusionStrategy.SINGLE
    conflict_threshold: float = Field(
        default=0.05, ge=0.0,
        description="相对偏差阈值；(max-min)/|mean| 超过即判 conflict",
    )
    require_agreement: bool = Field(
        default=False,
        description="True=必须多源一致才可用（孤证置信度降级但不阻塞）",
    )


class FactorSpec(BaseModel):
    """因子定义 — 注册表的核心单元。"""

    id: str = Field(description="因子唯一标识，如 'sector_main_net_inflow'")
    dimension: FactorDimension
    name: str = Field(description="人类可读名称")
    unit: str = Field(default="", description="目标单位（合成后）")
    description: str = Field(default="", description="因子含义说明")
    scope: str = Field(
        default="sector",
        description="适用范围：sector | market | stock；决定它进哪个指标包分组",
    )

    availability: AvailabilitySpec
    sources: list[SourceSpec] = Field(min_length=1, description="至少一个数据源")
    fusion: FusionSpec = Field(default_factory=FusionSpec)

    requires_components: bool = Field(
        default=False,
        description="是否需要按板块成分股聚合（如涨停家数、个股资金流加总）",
    )
    priority: int = Field(
        default=0, ge=0,
        description="因子在报告中的呈现优先级，0 最高",
    )

    @property
    def is_multi_source(self) -> bool:
        return len(self.sources) > 1

    @property
    def needs_second_round(self) -> bool:
        return self.availability.second_round

    def source_methods(self) -> list[tuple[str, str]]:
        """[(provider, method), ...] — 供 loader 与 method_table 对账。"""
        return [(s.provider, s.method) for s in self.sources]


class FactorRegistry(BaseModel):
    """因子注册表 — 一份设计时产物。

    落盘为 YAML（可 diff、可 A/B、可回放），运行时由 loader 校验后加载。
    """

    version: str = "1.0"
    name: str = Field(description="注册表用途，如 'sector_report' / 'market_report'")
    description: str = ""
    trade_timezone: str = "Asia/Shanghai"
    factors: list[FactorSpec] = Field(min_length=1)

    def by_id(self) -> dict[str, FactorSpec]:
        return {f.id: f for f in self.factors}

    def by_dimension(self) -> dict[str, list[FactorSpec]]:
        out: dict[str, list[FactorSpec]] = {}
        for f in self.factors:
            out.setdefault(f.dimension.value, []).append(f)
        return out

    def second_round_factors(self) -> list[FactorSpec]:
        """需要二次刷新的因子 — 两轮拉取编排的清单。"""
        return [f for f in self.factors if f.needs_second_round]

    def multi_source_factors(self) -> list[FactorSpec]:
        """多源因子 — 源级融合模块要处理的对象。"""
        return [f for f in self.factors if f.is_multi_source]

    def calibration_report(self) -> dict[str, object]:
        """取值路径校准度 — 设计时产物的完成度体检。"""
        total = sum(len(f.sources) for f in self.factors)
        calibrated = sum(1 for f in self.factors for s in f.sources if s.is_calibrated)
        uncalibrated = [
            f"{f.id}.{s.provider}"
            for f in self.factors
            for s in f.sources
            if not s.is_calibrated
        ]
        return {
            "sources_total": total,
            "sources_calibrated": calibrated,
            "calibration_rate": round(calibrated / total, 3) if total else 0.0,
            "uncalibrated": uncalibrated,
        }


__all__ = [
    "DisclosureProfile",
    "ExtractKind",
    "AvailabilitySpec",
    "SourceSpec",
    "FusionStrategy",
    "FusionSpec",
    "FactorSpec",
    "FactorRegistry",
]
