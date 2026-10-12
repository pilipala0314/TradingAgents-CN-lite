"""L2 编排 — 板块指标包构建（本模块是 L2 唯一有 I/O 的层）。

流程：时点闸门 → 成分股解析（requires_components）→ 逐因子逐源取数（provider 锁定）
→ 提取（extract）→ 融合（fusion）→ data_quality 聚合 → IndicatorPack。

失败语义（实现计划已批准）：
- 单源失败只记 evidence（FAILED），绝不中断因子；
- 全源失败 → 因子 value=None；维度 data_quality 按最坏状态聚合；
- 时点闸门：仅当 trade_date == 今日（+08）时生效；``--force`` 放行；round>=2 跳过 available_from。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from foundation.registry import (
    DisclosureProfile,
    ExtractKind,
    FactorRegistry,
    FactorSpec,
    SECTOR_NAME_MAPS,
    SourceSpec,
    load_registry,
)
from foundation.schemas import (
    Caliber,
    DataQualityEntry,
    DataStatus,
    Factor,
    IndicatorPack,
    PackMeta,
    PackScope,
    SourceAgreement,
    SourceEvidence,
)

from indicators.extract import extract_source, has_unresolved, norm_code, resolve_templates
from indicators.fusion import SourceResult, fuse

logger = logging.getLogger(__name__)

# 中国无夏令时，固定 +08:00 与 Asia/Shanghai 等价（免 tzdata 依赖）
_TZ = timezone(timedelta(hours=8))

DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "foundation" / "registry" / "sector_factors.yaml"
)

# 注册表里"逐指数调用"的变量：sector 包默认锚定上证（后续多指数场景由调用方覆盖）
DEFAULT_VARS: dict[str, str] = {"index_code": "000001"}

_ROUTE_TIMEOUT = 25.0
_EVIDENCE_ERROR_MAX = 240

# 成分股循环系统性故障早停阈值：至今零响应且连续失败达此数 → 判源级故障，跳过其余成分股。
# 取 8（而非 5）以降低对高抖动源的误判概率（源 50% 失败时前 8 连败仅约 0.4%）。
_COMPONENT_ABORT_THRESHOLD = 8
# 早停哨兵：被跳过的成分股（非异常、非空，需与真实结果区分）
_SKIPPED: object = object()

__all__ = ["RunConfig", "ComponentSet", "build_sector_pack", "normalize_trade_date"]


@dataclass
class RunConfig:
    """单次板块 pack 构建的配置。"""

    sector: str
    trade_date: str  # 归一为 YYYY-MM-DD
    generation_round: int = 1
    force: bool = False
    now: datetime | None = None  # 注入时钟（测试用）；缺省=当前 +08
    max_workers: int = 3  # 客户侧并发上限（所有 route 调用共享）
    component_limit: int = 60  # 逐成分股循环截断上限（WHOLE 类再收紧到 15）


@dataclass
class ComponentSet:
    """板块成分股（一次解析、运行内复用）。"""

    codes: list[str] = field(default_factory=list)
    status: DataStatus = DataStatus.EMPTY
    note: str = ""


def normalize_trade_date(text: str) -> str:
    """``YYYYMMDD`` / ``YYYY-MM-DD`` → ``YYYY-MM-DD``。"""
    s = str(text).strip().replace("/", "-")
    if "-" in s:
        return s
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


async def build_sector_pack(
    sector: str,
    trade_date: str,
    *,
    registry: FactorRegistry | None = None,
    ds: Any | None = None,
    generation_round: int = 1,
    force: bool = False,
    now: datetime | None = None,
) -> IndicatorPack:
    """构建单板块 IndicatorPack（不做落盘；落盘用 ``indicators.store.write_pack``）。"""
    cfg = RunConfig(
        sector=sector,
        trade_date=normalize_trade_date(trade_date),
        generation_round=max(int(generation_round), 1),
        force=force,
        now=now,
    )
    reg = registry if registry is not None else load_registry(DEFAULT_REGISTRY_PATH)
    source = ds if ds is not None else _default_source()
    now_dt = cfg.now or datetime.now(_TZ)
    trade_dt = datetime.fromisoformat(cfg.trade_date).replace(tzinfo=_TZ)
    sem = asyncio.Semaphore(cfg.max_workers)

    variables: dict[str, str] = {
        "sector": cfg.sector,
        "trade_date": cfg.trade_date.replace("-", ""),
        **DEFAULT_VARS,
    }
    # 跨口径板块名路由：为有映射表的源注入 sector_{provider}（注册表按需引用）
    for prov, mapping in SECTOR_NAME_MAPS.items():
        variables[f"sector_{prov}"] = mapping.get(cfg.sector, cfg.sector)

    components: ComponentSet | None = None
    if any(f.requires_components for f in reg.factors):
        components = await _resolve_components(cfg, source, sem)

    factors: list[Factor] = []
    for spec in reg.factors:
        results = await _eval_factor(spec, cfg, source, sem, variables, components, now_dt, trade_dt)
        factors.append(fuse(spec, results))

    pack = IndicatorPack(
        meta=PackMeta(
            trade_date=cfg.trade_date,
            scope=PackScope.SECTOR,
            symbols=components.codes if components and components.codes else [],
            sector=cfg.sector,
            sector_caliber=_sector_caliber(factors),
            generation_round=cfg.generation_round,
            produced_by="indicators",
        ),
        data_quality=_aggregate_quality(factors),
        factors=factors,
    )
    return pack


def _default_source() -> Any:
    from datasource import get_source  # 延迟导入：便于测试注入 ds，同时避免无谓初始化

    return get_source()


# ── 成分股 ────────────────────────────────────────────────────────

async def _resolve_components(cfg: RunConfig, src: Any, sem: asyncio.Semaphore) -> ComponentSet:
    """成分股一次解析（行业板块 → 概念板块兜底），失败如实标注。"""
    last_error = ""
    had_error = False
    for method in ("get_industry_board_components", "get_concept_board_components"):
        try:
            async with sem:
                data = await src.route(method, cfg.sector, use_cache=False, timeout=_ROUTE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - 每个候选方法独立降级
            had_error = True
            last_error = f"{method}: {type(exc).__name__}: {exc}"
            continue

        if isinstance(data, pd.DataFrame):
            rows: Any = data.to_dict(orient="records")
        elif isinstance(data, dict):
            rows = [data]
        else:
            rows = data
        codes: list[str] = []
        seen: set[str] = set()
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for col in ("代码", "code", "symbol"):
                    if col in row:
                        code = norm_code(row[col])
                        if code and code not in seen:
                            seen.add(code)
                            codes.append(code)
                        break
        if codes:
            return ComponentSet(codes=codes, status=DataStatus.OK, note=f"{len(codes)} 只成分股（{method}）")
        last_error = f"{method}: 返回空成分股"

    status = DataStatus.FAILED if had_error and not last_error.endswith("返回空成分股") else DataStatus.EMPTY
    return ComponentSet(codes=[], status=status, note=last_error[:_EVIDENCE_ERROR_MAX])


# ── 因子评估 ──────────────────────────────────────────────────────

async def _eval_factor(
    spec: FactorSpec,
    cfg: RunConfig,
    src: Any,
    sem: asyncio.Semaphore,
    variables: dict[str, str],
    components: ComponentSet | None,
    now_dt: datetime,
    trade_dt: datetime,
) -> list[SourceResult]:
    gate = _availability_gate(spec, cfg, now_dt)
    if gate is not None:
        return [
            _temporal_result(s, i, gate, now_dt)
            for i, s in enumerate(spec.sources)
        ]

    if spec.requires_components and (components is None or components.status != DataStatus.OK):
        reason = components.note if components else "成分股未解析"
        cstat = components.status if components else DataStatus.FAILED
        return [
            SourceResult(
                spec=s,
                order=i,
                status=cstat,
                evidence=SourceEvidence(
                    source=s.provider, method=s.method, status=cstat,
                    fetched_at=now_dt, error=f"成分股不可用: {reason}"[:_EVIDENCE_ERROR_MAX],
                ),
                note="成分股不可用，未拉取",
            )
            for i, s in enumerate(spec.sources)
        ]

    results: list[SourceResult] = []
    for order, s in enumerate(spec.sources):
        if _needs_component_loop(s):
            results.append(
                await _eval_component_source(spec, s, order, cfg, src, sem, variables, components, now_dt, trade_dt)
            )
            continue
        args = resolve_templates(s.args, variables)
        if has_unresolved(args):
            results.append(_failed_result(s, order, now_dt, f"args 存在未解析模板变量: {args}"))
            continue
        results.append(await _eval_plain_source(s, order, args, src, sem, variables, components, now_dt, trade_dt))
    return results


def _needs_component_loop(spec: SourceSpec) -> bool:
    """args 含 ``{component}`` → 逐成分股循环（match 里的 {component} 是集合匹配，不循环）。"""
    return "{component}" in json.dumps(spec.args, ensure_ascii=False)


async def _eval_plain_source(
    s: SourceSpec,
    order: int,
    args: dict[str, Any],
    src: Any,
    sem: asyncio.Semaphore,
    variables: dict[str, str],
    components: ComponentSet | None,
    now_dt: datetime,
    trade_dt: datetime,
) -> SourceResult:
    t0 = time.perf_counter()
    try:
        async with sem:
            data = await src.route(s.method, provider=s.provider, use_cache=False, timeout=_ROUTE_TIMEOUT, **args)
    except Exception as exc:  # noqa: BLE001 - 单源失败只降级，不中断
        return _failed_result(s, order, now_dt, f"{type(exc).__name__}: {exc}", latency_ms=_ms(t0))

    latency = _ms(t0)
    try:
        ex = extract_source(s, data, variables, component_set=_component_codes(components))
    except Exception as exc:  # noqa: BLE001 - 提取异常同记 FAILED
        return _failed_result(s, order, now_dt, f"提取异常: {type(exc).__name__}: {exc}", latency_ms=latency)

    return SourceResult(
        spec=s,
        order=order,
        status=ex.status,
        value=ex.value,
        payload=ex.payload,
        note=ex.note,
        evidence=SourceEvidence(
            source=s.provider,
            method=s.method,
            status=ex.status,
            as_of=trade_dt if ex.status == DataStatus.OK else None,
            fetched_at=now_dt,
            latency_ms=latency,
            error=ex.note[:_EVIDENCE_ERROR_MAX] if ex.status != DataStatus.OK else None,
        ),
    )


async def _eval_component_source(
    spec: FactorSpec,
    s: SourceSpec,
    order: int,
    cfg: RunConfig,
    src: Any,
    sem: asyncio.Semaphore,
    variables: dict[str, str],
    components: ComponentSet | None,
    now_dt: datetime,
    trade_dt: datetime,
) -> SourceResult:
    """逐成分股取数（member_fund_flow 的 SUM / sector_news 的 WHOLE）。

    evidence 每源一条（note 记 ok/total 与截断），避免逐股 evidence 爆表。
    """
    assert components is not None and components.codes  # 调用前已保证
    limit = min(cfg.component_limit, 15) if s.extract_kind == ExtractKind.WHOLE else cfg.component_limit
    selected = components.codes[:limit]
    truncated = len(components.codes) - len(selected)

    t0 = time.perf_counter()

    # 系统性故障早停：若某源对整类成分股接口失效（WAF 封锁 / 接口下线），
    # 逐只成分股会放大成 60~187 次无效请求（每次还含重试）→ 白烧付费代理。
    # 判据从严：**至今零响应**且连续失败达阈值，才判系统性故障并跳过其余成分股。
    # （一旦有任一成分股拿到响应，说明源可用，后续失败只当抖动，绝不停——避免丢数据）
    fail_streak = 0
    responded = False
    systemic_abort = False
    abort_at = min(_COMPONENT_ABORT_THRESHOLD, len(selected))

    async def _one(code: str) -> Any:
        nonlocal fail_streak, responded, systemic_abort
        if systemic_abort:
            return _SKIPPED
        comp_vars = {**variables, "component": code}
        args = resolve_templates(s.args, comp_vars)
        try:
            async with sem:
                if systemic_abort:  # 拿到并发槽后二次确认（可能有更早的任务已触发早停）
                    return _SKIPPED
                data = await src.route(s.method, provider=s.provider, use_cache=False, timeout=_ROUTE_TIMEOUT, **args)
        except BaseException:
            fail_streak += 1
            if not responded and fail_streak >= abort_at:
                systemic_abort = True
            raise
        responded = True  # 源对成分股有响应（成功或空）→ 视为源可用，不再早停
        fail_streak = 0
        return extract_source(s, data, comp_vars, component_set=None)

    outcomes = await asyncio.gather(*(_one(c) for c in selected), return_exceptions=True)

    ok_count = 0
    empty_count = 0
    skipped_count = 0
    errors: list[str] = []
    nums: list[float] = []
    payloads: list[Any] = []
    for outcome in outcomes:
        if outcome is _SKIPPED:
            skipped_count += 1
        elif isinstance(outcome, BaseException):
            errors.append(f"{type(outcome).__name__}: {outcome}"[:120])
        elif outcome.status == DataStatus.OK:
            ok_count += 1
            if isinstance(outcome.value, (int, float)) and not isinstance(outcome.value, bool):
                nums.append(float(outcome.value))
            if isinstance(outcome.payload, list):
                payloads.extend(outcome.payload)
            elif outcome.payload is not None:
                payloads.append(outcome.payload)
        elif outcome.status == DataStatus.EMPTY:
            empty_count += 1

    latency = _ms(t0)
    suffix = f"；已截断 {len(selected)}/{len(components.codes)}" if truncated else ""
    if systemic_abort:
        suffix += f"；疑似系统性故障（连续 {abort_at} 只无响应），跳过其余 {skipped_count} 只"
    summary = f"成分股 {len(selected)} 只：ok={ok_count} empty={empty_count} err={len(errors)}{suffix}"
    if skipped_count and not systemic_abort:
        summary += f"；跳过 {skipped_count}"

    if ok_count == 0:
        status = DataStatus.FAILED if errors else DataStatus.EMPTY
        error = (errors[0] if errors else summary)[:_EVIDENCE_ERROR_MAX]
        return SourceResult(
            spec=s, order=order, status=status, note=summary,
            evidence=SourceEvidence(
                source=s.provider, method=s.method, status=status,
                fetched_at=now_dt, latency_ms=latency, error=error, preview=summary,
            ),
        )

    if s.extract_kind == ExtractKind.SUM:
        value: Any = sum(nums) if nums else None
        payload: Any = payloads or None
        if value is None:
            status = DataStatus.EMPTY
        else:
            status = DataStatus.OK
    else:  # WHOLE（union_dedup 在 fusion 层去重）
        value = len(payloads)
        payload = payloads
        status = DataStatus.OK

    return SourceResult(
        spec=s, order=order, status=status, value=value, payload=payload, note=summary,
        evidence=SourceEvidence(
            source=s.provider, method=s.method, status=status,
            as_of=trade_dt if status == DataStatus.OK else None,
            fetched_at=now_dt, latency_ms=latency,
            error=None if status == DataStatus.OK else summary, preview=summary,
        ),
    )


# ── 时点闸门 / 工具 ───────────────────────────────────────────────

def _availability_gate(spec: FactorSpec, cfg: RunConfig, now_dt: datetime) -> DataStatus | None:
    """可用性闸门：返回 None=放行；否则为 UNAVAILABLE_YET。

    规则：force 放行一切；仅 trade_date == 今日（+08）时生效（历史日数据已披露）；
    next_day 恒闸；round>=2 跳过 available_from。
    """
    if cfg.force:
        return None
    if cfg.trade_date != now_dt.date().isoformat():
        return None
    avail = spec.availability
    if avail.profile == DisclosureProfile.NEXT_DAY:
        return DataStatus.UNAVAILABLE_YET
    if cfg.generation_round >= 2:
        return None
    if avail.available_from:
        hh, mm = (int(x) for x in avail.available_from.split(":"))
        if (now_dt.hour, now_dt.minute) < (hh, mm):
            return DataStatus.UNAVAILABLE_YET
    return None


def _temporal_result(s: SourceSpec, order: int, status: DataStatus, now_dt: datetime) -> SourceResult:
    return SourceResult(
        spec=s,
        order=order,
        status=status,
        note="时点闸门未放行，未拉取",
        evidence=SourceEvidence(
            source=s.provider, method=s.method, status=status,
            fetched_at=now_dt, error="时点未到（--force 可绕过）",
        ),
    )


def _failed_result(
    s: SourceSpec, order: int, now_dt: datetime, error: str, latency_ms: float = 0.0,
) -> SourceResult:
    return SourceResult(
        spec=s,
        order=order,
        status=DataStatus.FAILED,
        note=error[:_EVIDENCE_ERROR_MAX],
        evidence=SourceEvidence(
            source=s.provider, method=s.method, status=DataStatus.FAILED,
            fetched_at=now_dt, latency_ms=latency_ms, error=error[:_EVIDENCE_ERROR_MAX],
        ),
    )


def _component_codes(components: ComponentSet | None) -> set[str] | None:
    return set(components.codes) if components and components.codes else None


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _sector_caliber(factors: list[Factor]) -> Caliber:
    for f in factors:
        if f.id == "sector_pct_change":
            return f.caliber
    return Caliber.UNKNOWN


# ── data_quality 聚合 ─────────────────────────────────────────────

_STATUS_RANK = {
    DataStatus.OK: 1,
    DataStatus.EMPTY: 2,
    DataStatus.UNAVAILABLE_YET: 3,
    DataStatus.CONFLICT: 4,
    DataStatus.FAILED: 5,
}


def _factor_status(f: Factor) -> DataStatus:
    """因子层面的可用性状态。

    - CONFLICT → conflict（is_usable False）；
    - 有标量值 → OK；
    - 无标量值但有 OK 证据（WHOLE 类整表，value 恒为 None 避免误导数字）→ OK；
    - 否则按证据最坏：FAILED > UNAVAILABLE_YET > EMPTY。
    """
    if f.source_agreement == SourceAgreement.CONFLICT:
        return DataStatus.CONFLICT
    if f.value is not None:
        return DataStatus.OK
    statuses = [e.status for e in f.evidence]
    if any(s == DataStatus.OK for s in statuses):
        return DataStatus.OK
    if any(s == DataStatus.FAILED for s in statuses):
        return DataStatus.FAILED
    if any(s == DataStatus.UNAVAILABLE_YET for s in statuses):
        return DataStatus.UNAVAILABLE_YET
    return DataStatus.EMPTY


def _aggregate_quality(factors: list[Factor]) -> list[DataQualityEntry]:
    """按 dimension 聚合质量声明：状态取该维度因子的最坏（保守）。"""
    by_dim: dict[str, list[Factor]] = {}
    for f in factors:
        by_dim.setdefault(f.dimension.value, []).append(f)

    entries: list[DataQualityEntry] = []
    for dim, fs in by_dim.items():
        worst = DataStatus.OK
        evidence: list[SourceEvidence] = []
        as_of: datetime | None = None
        for f in fs:
            st = _factor_status(f)
            if _STATUS_RANK[st] > _STATUS_RANK[worst]:
                worst = st
            evidence.extend(f.evidence)
            if f.value is not None and as_of is None:
                as_of = f.as_of
        entries.append(
            DataQualityEntry(
                dimension=dim,
                status=worst,
                as_of=as_of,
                note=", ".join(f"{f.id}:{_factor_status(f).value}" for f in fs)[:500],
                evidence=evidence,
            )
        )
    return entries