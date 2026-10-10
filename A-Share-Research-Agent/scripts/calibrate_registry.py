#!/usr/bin/env python
"""因子注册表校准脚本 — 实测 API 返回结构，回填取值路径。

用途：把 `foundation/registry/*.yaml` 里标注「待校准」的 source 实测校准，
     提升 `calibration_report().calibration_rate`。

用法（项目根目录执行）::

    # 1) 探针所有源，输出报告（只读，不改文件）
    python scripts/calibrate_registry.py --report

    # 2) 确认无误后回写 YAML（用 ruamel round-trip 保留注释）
    python scripts/calibrate_registry.py --apply

    # 3) 只看某个因子 / 只看某个 provider
    python scripts/calibrate_registry.py --report --factor sector_pct_change
    python scripts/calibrate_registry.py --report --provider akshare

设计原则：
- **脚本只采集证据与给出建议，不做无证据的猜测**。confidence=low 时 apply 不回写。
- **回写保留注释**：YAML 是设计时产物（人可读、可 diff），不能被 dump 重排。
- **失败不阻塞**：单源探针失败记录到报告，不影响其余源。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field as dc_field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from foundation.registry import (  # noqa: E402
    ExtractKind,
    FactorSpec,
    FactorRegistry,
    SourceSpec,
    load_registry,
)

DEFAULT_YAML = PROJECT_ROOT / "foundation" / "registry" / "sector_factors.yaml"

# 探针用的真实参数值（模拟调用方注入）。
# trade_date 取较早的历史日：避开节假日（如国庆）导致的「无数据」误判，
# 校准目标是验证「取值路径」而非「当日有无数据」。
PROBE_VARS: dict[str, str] = {
    "sector": "半导体",
    "component": "600519",
    "index_code": "000001",
    "trade_date": (date.today() - timedelta(days=12)).strftime("%Y%m%d"),
}

# 因子语义 → 候选字段名/匹配列名（按优先级排列，用于启发式推断）
# 这里是「校准工具的知识」，不是设计时产物，所以放脚本里不放 YAML。
FACTOR_HINTS: dict[str, dict[str, Any]] = {
    "sector_pct_change": {
        "kind": "value",
        "fields": ["change_pct", "pct_change", "pctChg", "涨跌幅", "涨跌", "change", "f3"],
        "match": ["board_name", "板块名称", "name", "f14", "board_name_full"],
    },
    "sector_recent_bars": {"kind": "whole", "fields": [], "match": []},
    "sector_main_net_inflow": {
        "kind": "value",
        "fields": ["main_net", "主力净流入", "netMfAmount", "net_mf_amount", "主力净流入-净额", "net"],
        "match": ["board_name", "板块名称", "name", "f14"],
    },
    "sector_member_fund_flow": {
        "kind": "sum",
        "fields": ["main_net", "主力净流入", "netMfAmount", "net"],
        "match": [],
    },
    "northbound_flow": {
        "kind": "value",
        "fields": ["资金净流入", "净流入", "net", "north_net", "f174"],
        "match": ["交易日", "日期", "date"],
        "match_value": "{trade_date}",
    },
    "sector_limit_up_count": {"kind": "count", "fields": [], "match": ["代码", "code", "symbol"]},
    "sector_limit_down_count": {"kind": "count", "fields": [], "match": ["代码", "code", "symbol"]},
    "sector_bomb_board_count": {"kind": "count", "fields": [], "match": ["代码", "code", "symbol"]},
    "sector_leaders": {"kind": "whole", "fields": [], "match": []},
    "sector_event_signals": {"kind": "whole", "fields": [], "match": []},
    "sector_news": {"kind": "whole", "fields": [], "match": []},
    "sector_dragon_tiger": {"kind": "whole", "fields": [], "match": ["代码", "code", "symbol"]},
    # K 线类整体取用：pct_change 由指标层从 close 计算，不在源侧提取
    "market_index_pct": {"kind": "whole", "fields": [], "match": []},
    "market_breadth": {"kind": "whole", "fields": [], "match": []},
    "market_fund_flow": {
        "kind": "value",
        "fields": ["主力净流入-净额", "主力净流入", "main_net", "netMfAmount", "net"],
        "match": ["交易日", "日期", "date"],
        "match_value": "{trade_date}",
    },
}


@dataclass
class ProbeResult:
    """单个 source 的探针结果。"""

    factor_id: str
    provider: str
    method: str
    status: str = "pending"  # ok | error
    error: str = ""
    shape: str = ""
    columns: list[str] = dc_field(default_factory=list)
    row_count: int = 0

    suggested_kind: str = ""
    suggested_field: str = ""
    suggested_match: dict[str, str] = dc_field(default_factory=dict)
    confidence: str = "none"  # high | medium | low | none
    reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.factor_id}.{self.provider}"


def _resolve(value: Any) -> Any:
    """把 {var} 模板替换为探针值（PROBE_VARS 含 trade_date）。"""
    if isinstance(value, str):
        out = value
        for k, v in PROBE_VARS.items():
            out = out.replace("{" + k + "}", v)
        return out
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    return value


def _describe(response: Any) -> tuple[str, list[str], int]:
    """描述返回结构：(shape, columns, row_count)。"""
    try:
        import pandas as pd

        if isinstance(response, pd.DataFrame):
            return (
                f"DataFrame({len(response)} rows × {len(response.columns)} cols)",
                [str(c) for c in response.columns],
                len(response),
            )
    except Exception:
        pass
    if isinstance(response, list):
        cols: list[str] = []
        if response and isinstance(response[0], dict):
            cols = [str(k) for k in response[0].keys()]
        return f"list[dict] × {len(response)}", cols, len(response)
    if isinstance(response, dict):
        return f"dict({len(response)} keys)", [str(k) for k in response.keys()], 1
    return type(response).__name__, [], 0


def _score_field(candidate: str, hints: list[str]) -> tuple[int, str]:
    """候选字段名与提示词的匹配分。

    Returns:
        (score, match_type) — score>0 时 match_type ∈ {exact, partial}；否则 ("", "")
    """
    c = candidate.strip().lower()
    for i, h in enumerate(hints):
        hl = h.strip().lower()
        if c == hl:
            return 100 - i, "exact"
        if hl in c or c in hl:
            return 50 - i, "partial"
    return 0, ""


def _match_template(factor: FactorSpec, column: str, override: str = "") -> str:
    """根据匹配列语义决定模板占位符。

    优先级：hints 显式指定 > 列名语义推断 > 因子 scope 兜底。
    """
    if override:
        return override
    col = column.lower()
    if any(k in col for k in ("日期", "交易日", "date")):
        return "{trade_date}"
    if any(k in col for k in ("代码", "code", "symbol", "sec")):
        return "{component}"
    if any(k in col for k in ("名称", "name", "board", "板块")):
        return "{sector}"
    return "{component}" if factor.requires_components else "{sector}"


def _infer(factor: FactorSpec, source: SourceSpec, res: ProbeResult) -> None:
    """根据返回结构 + 因子提示词推断取值路径。"""
    if res.status != "ok":
        res.confidence = "none"
        res.reason = "探针失败，无法推断"
        return

    hints = FACTOR_HINTS.get(factor.id, {"kind": "whole", "fields": [], "match": []})
    target_kind = hints.get("kind", "whole")
    res.suggested_kind = target_kind

    if target_kind == "whole":
        # 整体取用：只要探针成功即视为已校准
        res.confidence = "high"
        res.reason = "整体取用（列表/整表交指标层加工），探针成功即校准"
        return

    if not res.columns:
        # 无结构化字段（如标量返回）
        if target_kind == "value":
            res.confidence = "low"
            res.reason = f"返回值为 {res.shape}，无字段可提取"
        else:
            res.confidence = "low"
            res.reason = f"返回值为 {res.shape}，不适合 {target_kind}"
        return

    # 字段打分
    best_field, best_score, best_field_type = "", 0, ""
    for cand in res.columns:
        s, mtype = _score_field(cand, hints.get("fields", []))
        if s > best_score:
            best_field, best_score, best_field_type = cand, s, mtype

    # 匹配列打分
    best_match_col, best_match_score, best_match_type = "", 0, ""
    for cand in res.columns:
        s, mtype = _score_field(cand, hints.get("match", []))
        if s > best_match_score:
            best_match_col, best_match_score, best_match_type = cand, s, mtype

    match_override = hints.get("match_value", "")

    if target_kind == "count":
        res.suggested_kind = "count"
        res.suggested_field = ""
        if best_match_col and best_match_type:
            res.suggested_match = {best_match_col: _match_template(factor, best_match_col, match_override)}
            res.confidence = "high" if best_match_type == "exact" else "medium"
            res.reason = f"计数类；匹配列 {best_match_col!r} 命中提示（{best_match_type}）"
        else:
            res.confidence = "medium"
            res.reason = "计数类；未找到明确匹配列，按全表计数"
        return

    if not best_field:
        res.suggested_kind = "whole" if target_kind != "value" else "value"
        res.confidence = "low"
        res.reason = (
            f"在 {res.columns[:8]} 中未命中提示词 {hints.get('fields', [])}，"
            f"需人工确认取值字段"
        )
        return

    res.suggested_kind = target_kind
    res.suggested_field = best_field
    # 单行结果（provider 侧已按 {sector} 过滤）无需 match 列；
    # 多行结果才建议 match，避免把 lead_name 之类"含 name 的列"误判为匹配列。
    if res.row_count > 1 and best_match_col and best_match_type:
        res.suggested_match = {best_match_col: _match_template(factor, best_match_col, match_override)}
    res.confidence = "high" if best_field_type == "exact" else "medium"
    res.reason = f"字段 {best_field!r} 命中提示（{best_field_type}, score={best_score}）"


async def _probe_one(source: SourceSpec, factor: FactorSpec) -> ProbeResult:
    """探针单个 source：真实调用 route() 并推断取值路径。"""
    res = ProbeResult(factor_id=factor.id, provider=source.provider, method=source.method)
    resolved_args = _resolve(source.args)

    try:
        from datasource import get_source

        ds = get_source()
        # 必须锁定 provider：否则 route 会走整条 fallback 链（且候选按自适应排序），
        # 探针结果可能来自其它源，导致「按声明 provider 标签展示」的报告失真。
        response = await ds.route(
            source.method, provider=source.provider, use_cache=False, timeout=25, **resolved_args
        )
        res.status = "ok"
        res.shape, res.columns, res.row_count = _describe(response)
    except Exception as exc:  # noqa: BLE001 - 探针要记录所有失败原因
        res.status = "error"
        res.error = f"{type(exc).__name__}: {str(exc)[:180]}"
        res.reason = "探针失败"
        res.confidence = "none"
        return res

    _infer(factor, source, res)
    return res


async def _probe_all(
    registry: FactorRegistry, factor_filter: str | None, provider_filter: str | None
) -> list[ProbeResult]:
    tasks: list[asyncio.Task] = []
    pairs: list[tuple[SourceSpec, FactorSpec]] = []
    for f in registry.factors:
        if factor_filter and f.id != factor_filter:
            continue
        for s in f.sources:
            if provider_filter and s.provider != provider_filter:
                continue
            pairs.append((s, f))

    sem = asyncio.Semaphore(3)  # 控制并发，避免触发东财/akshare 限流

    async def _run(s: SourceSpec, f: FactorSpec) -> ProbeResult:
        async with sem:
            return await _probe_one(s, f)

    for s, f in pairs:
        tasks.append(asyncio.create_task(_run(s, f)))
    return await asyncio.gather(*tasks)


def _print_report(results: list[ProbeResult]) -> None:
    ok = [r for r in results if r.status == "ok"]
    print(f"\n{'=' * 78}")
    print(f"校准探针报告  共 {len(results)} 个源  成功 {len(ok)}  失败 {len(results) - len(ok)}")
    print(f"{'=' * 78}\n")

    for r in results:
        mark = "✓" if r.status == "ok" else "✗"
        print(f"[{mark}] {r.key}")
        print(f"      method={r.method}  shape={r.shape or r.error}")
        if r.columns:
            cols = r.columns[:10]
            print(f"      columns={cols}{' ...' if len(r.columns) > 10 else ''}")
        if r.suggested_kind:
            match_str = f"  match={r.suggested_match}" if r.suggested_match else ""
            print(
                f"      → kind={r.suggested_kind}  field={r.suggested_field!r}{match_str}"
                f"  confidence={r.confidence}"
            )
        print(f"      {r.reason}")
        print()

    # 汇总
    high = sum(1 for r in results if r.confidence == "high")
    med = sum(1 for r in results if r.confidence == "medium")
    low = sum(1 for r in results if r.confidence == "low")
    print(f"{'─' * 78}")
    print(f"置信度分布:  high={high}  medium={med}  low={low}  none={len(results) - high - med - low}")
    print(f"{'─' * 78}\n")


def _apply_to_yaml(results: list[ProbeResult], yaml_path: Path) -> int:
    """把 high/medium 置信度的建议回写 YAML（ruamel round-trip 保留注释）。

    Returns:
        实际回写的 source 数量。
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    with yaml_path.open("r", encoding="utf-8") as fh:
        doc = y.load(fh)

    # 建立 (factor_id, provider) → 建议 的索引
    suggestions = {
        r.key: r for r in results
        if r.status == "ok" and r.confidence in ("high", "medium") and r.suggested_kind
    }

    applied = 0
    factors_seq: CommentedSeq = doc.get("factors")
    if factors_seq is None:
        raise SystemExit(f"YAML 结构异常：缺 factors 键 {yaml_path}")

    for factor_node in factors_seq:
        if not isinstance(factor_node, CommentedMap):
            continue
        fid = factor_node.get("id")
        sources_seq = factor_node.get("sources")
        if not isinstance(sources_seq, CommentedSeq):
            continue
        for source_node in sources_seq:
            if not isinstance(source_node, CommentedMap):
                continue
            key = f"{fid}.{source_node.get('provider')}"
            sug = suggestions.get(key)
            if sug is None:
                continue

            changed = False
            # 1) extract_kind
            if sug.suggested_kind and source_node.get("extract_kind") != sug.suggested_kind:
                source_node["extract_kind"] = sug.suggested_kind
                changed = True
            # 2) field
            if sug.suggested_kind in ("value", "sum"):
                if source_node.get("field") != sug.suggested_field:
                    source_node["field"] = sug.suggested_field
                    changed = True
            # 3) match
            if sug.suggested_match:
                if dict(source_node.get("match") or {}) != sug.suggested_match:
                    source_node["match"] = CommentedMap(sug.suggested_match)
                    changed = True
            # 4) note：去掉「待校准」标记（若成功校准）
            note = str(source_node.get("note") or "")
            if "待校准" in note:
                cleaned = (
                    note.replace("待校准 — 返回字段需实测确认", "")
                    .replace("待校准 — 全市场涨停池需按成分股过滤后计数", "")
                    .replace("待校准 — 全市场榜单需按成分股过滤", "")
                    .replace("待校准", "")
                ).strip(" —-—")
                if cleaned != note:
                    source_node["note"] = cleaned
                    changed = True

            if changed:
                applied += 1

    if applied:
        with yaml_path.open("w", encoding="utf-8") as fh:
            y.dump(doc, fh)
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description="因子注册表校准脚本")
    parser.add_argument("--yaml", default=str(DEFAULT_YAML), help="注册表 YAML 路径")
    parser.add_argument("--report", action="store_true", help="输出探针报告（默认）")
    parser.add_argument("--apply", action="store_true", help="把建议回写 YAML（保留注释）")
    parser.add_argument("--factor", default=None, help="只探针指定因子 id")
    parser.add_argument("--provider", default=None, help="只探针指定 provider")
    parser.add_argument("--json", default=None, help="把报告写入 JSON 文件")
    args = parser.parse_args()

    yaml_path = Path(args.yaml)
    registry = load_registry(yaml_path)
    before = registry.calibration_report()

    results = asyncio.run(_probe_all(registry, args.factor, args.provider))
    _print_report(results)

    if args.json:
        Path(args.json).write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"报告已写入: {args.json}")

    if args.apply:
        applied = _apply_to_yaml(results, yaml_path)
        print(f"已回写 {applied} 个 source 到 {yaml_path.name}")
        after = load_registry(yaml_path).calibration_report()
        print(f"\n校准率: {before['calibration_rate']:.1%} → {after['calibration_rate']:.1%}")
        if after["uncalibrated"]:
            print(f"仍待校准 {len(after['uncalibrated'])} 个:")
            for k in after["uncalibrated"]:
                print(f"    {k}")
    else:
        print(f"当前校准率: {before['calibration_rate']:.1%}（用 --apply 回写后生效）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
