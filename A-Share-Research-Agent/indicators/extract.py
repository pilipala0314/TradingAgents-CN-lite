"""L2 单源提取 — 把 route() 返回的数据按注册表声明取成标量值。

纯函数（无 I/O）。失败语义（实现计划已批准）：
- 本模块只产 ``OK`` / ``EMPTY``：取到 → OK；空表 / 匹配不到 / 字段不符 → EMPTY；
- route 层异常与提取异常由调用方（pipeline）记 ``FAILED``。

匹配语义：
- ``match`` 列名不做模板替换；期望值经 ``{var}`` 模板解析；
- 期望值含未解析 ``{component}`` 时改用集合匹配（``component_set``）；
- 相等判定：数值近似 → 去 ``-``/``/`` 归一字符串比较 → 兜底 ``str==str``。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from foundation.registry import ExtractKind, SourceSpec
from foundation.schemas import DataStatus
from datasource.providers._shared.utils import to_native

__all__ = [
    "ExtractedValue",
    "extract_source",
    "resolve_templates",
    "has_unresolved",
    "norm_code",
]


@dataclass
class ExtractedValue:
    """单源提取结果（status 仅 OK / EMPTY）。"""

    status: DataStatus
    value: float | int | str | None = None
    payload: Any = None
    matched_rows: int = 0
    note: str = ""


# ── 模板与工具 ────────────────────────────────────────────────────

def resolve_templates(obj: Any, variables: dict[str, str]) -> Any:
    """递归替换 ``{var}``；未提供的变量原样保留（由调用方判定为未解析）。"""
    if isinstance(obj, str):
        out = obj
        for k, v in variables.items():
            out = out.replace("{" + k + "}", str(v))
        return out
    if isinstance(obj, dict):
        return {k: resolve_templates(v, variables) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [resolve_templates(v, variables) for v in obj]
    return obj


def has_unresolved(obj: Any) -> bool:
    """模板变量是否残留（如 ``{index_code}`` 未提供）。"""
    if isinstance(obj, str):
        return "{" in obj and "}" in obj
    if isinstance(obj, dict):
        return any(has_unresolved(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(has_unresolved(v) for v in obj)
    return False


def norm_code(value: Any) -> str:
    """股票代码归一（数字串补足 6 位）。"""
    s = str(value).strip()
    return s.zfill(6) if s.isdigit() else s


def _to_number(value: Any) -> float | None:
    """可数值化 → float；否则 None（百分比/千分位清洗；NaN/Inf 视为不可用）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    else:
        try:
            f = float(str(value).replace(",", "").replace("%", "").strip())
        except (TypeError, ValueError):
            return None
    return None if math.isnan(f) or math.isinf(f) else f


def _is_zero_padded(value: Any) -> bool:
    s = str(value).strip()
    return s.isdigit() and len(s) > 1 and s.startswith("0")


def _values_equal(actual: Any, expected: Any) -> bool:
    """match 相等判定（支持日期/代码的格式差异）。"""
    def _norm(v: Any) -> str:
        return str(v).strip().replace("-", "").replace("/", "").replace(" ", "")

    if _is_zero_padded(actual) or _is_zero_padded(expected):
        return _norm(actual) == _norm(expected)
    na, ne = _to_number(actual), _to_number(expected)
    if na is not None and ne is not None:
        return abs(na - ne) <= 1e-9
    return _norm(actual) == _norm(expected)


def _row_matches(row: dict[str, Any], match: dict[str, Any], component_set: set[str] | None) -> bool:
    for col, expected in match.items():
        if col not in row:
            return False
        if isinstance(expected, str) and "{component}" in expected and component_set is not None:
            if norm_code(row[col]) not in component_set:
                return False
            continue
        if not _values_equal(row[col], expected):
            return False
    return True


def _to_rows(data: Any) -> list[dict[str, Any]] | None:
    """归一到行（list[dict]）；不支持的形态返回 None。"""
    if isinstance(data, pd.DataFrame):
        return data.to_dict(orient="records")
    if isinstance(data, list):
        if not data:
            return []
        if all(isinstance(x, dict) for x in data):
            return data
        return None
    if isinstance(data, dict):
        return [data]
    return None


def _jsonable(value: Any) -> Any:
    """递归 to_native（去 numpy 标量），保证 model_dump(json) 可序列化。"""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return to_native(value)


def _columns_hint(data: Any) -> str:
    cols: list[str] = []
    if isinstance(data, pd.DataFrame):
        cols = [str(c) for c in data.columns]
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        cols = [str(k) for k in data[0].keys()]
    elif isinstance(data, dict):
        cols = [str(k) for k in data.keys()]
    return str(cols[:8])


# ── 提取主入口 ────────────────────────────────────────────────────

def extract_source(
    spec: SourceSpec,
    data: Any,
    variables: dict[str, str],
    component_set: set[str] | None = None,
) -> ExtractedValue:
    """按 spec.extract_kind 提取单源值（只产 OK/EMPTY）。"""
    if spec.extract_kind == ExtractKind.WHOLE:
        return _extract_whole(spec, data, variables, component_set)

    rows = _to_rows(data)
    if rows is None:
        return ExtractedValue(
            status=DataStatus.EMPTY,
            note=f"返回形态不支持行提取: {type(data).__name__}",
        )
    if not rows:
        return ExtractedValue(status=DataStatus.EMPTY, note="空表")

    match = resolve_templates(spec.match, variables) if spec.match else {}
    if match:
        rows = [r for r in rows if _row_matches(r, match, component_set)]
    matched = len(rows)

    if spec.extract_kind == ExtractKind.COUNT:
        # 表非空但 0 行匹配 = 合法的"零命中"（如当日无该板块涨停）
        return ExtractedValue(status=DataStatus.OK, value=matched, matched_rows=matched)

    if spec.extract_kind == ExtractKind.VALUE:
        if not rows:
            return ExtractedValue(
                status=DataStatus.EMPTY,
                note=f"未匹配到行（可用列: {_columns_hint(data)}）",
            )
        if spec.field not in rows[0]:
            return ExtractedValue(
                status=DataStatus.EMPTY,
                note=f"字段 {spec.field!r} 不在返回列（可用列: {_columns_hint(data)}）",
            )
        raw = rows[0][spec.field]
        num = _to_number(raw)
        if num is None and (raw is None or (isinstance(raw, float) and math.isnan(raw))):
            return ExtractedValue(status=DataStatus.EMPTY, matched_rows=matched, note="字段值为空")
        value: float | str = num * spec.unit_scale if num is not None else str(raw)
        return ExtractedValue(
            status=DataStatus.OK,
            value=to_native(value),
            payload=to_native(raw),
            matched_rows=matched,
        )

    if spec.extract_kind == ExtractKind.SUM:
        if not rows:
            return ExtractedValue(status=DataStatus.EMPTY, note="未匹配到行")
        nums: list[float] = []
        for r in rows:
            if spec.field in r:
                n = _to_number(r[spec.field])
                if n is not None:
                    nums.append(n * spec.unit_scale)
        if not nums:
            return ExtractedValue(
                status=DataStatus.EMPTY,
                matched_rows=matched,
                note=f"匹配 {matched} 行但字段不可数值化",
            )
        return ExtractedValue(
            status=DataStatus.OK,
            value=to_native(sum(nums)),
            payload=to_native(nums),
            matched_rows=matched,
        )

    # schema 之外的扩展 kind：不猜测
    return ExtractedValue(status=DataStatus.EMPTY, note=f"未知 extract_kind: {spec.extract_kind}")


def _extract_whole(
    spec: SourceSpec,
    data: Any,
    variables: dict[str, str],
    component_set: set[str] | None,
) -> ExtractedValue:
    """整体取用：value=None（整表非标量，禁止产生误导性数字），payload=原始结构，matched_rows=条目数。"""
    if data is None:
        return ExtractedValue(status=DataStatus.EMPTY, note="空结果")

    if isinstance(data, dict):
        # 单对象聚合（如快照/行情 dict）：match 不适用，原样保留
        payload = _jsonable(data)
        if not payload:
            return ExtractedValue(status=DataStatus.EMPTY, payload=payload, note="空结果")
        return ExtractedValue(status=DataStatus.OK, payload=payload, matched_rows=len(payload))

    rows = _to_rows(data)
    if rows is None:
        return ExtractedValue(
            status=DataStatus.EMPTY,
            note=f"返回形态不支持整体取用: {type(data).__name__}",
        )
    if not rows:
        return ExtractedValue(status=DataStatus.EMPTY, note="空表")

    match = resolve_templates(spec.match, variables) if spec.match else {}
    if match:
        rows = [r for r in rows if _row_matches(r, match, component_set)]
        if not rows:
            return ExtractedValue(status=DataStatus.EMPTY, note="未匹配到行（成分股过滤后为空）")

    payload = _jsonable(rows)
    return ExtractedValue(status=DataStatus.OK, payload=payload, matched_rows=len(rows))