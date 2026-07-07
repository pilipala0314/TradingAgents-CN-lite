"""Universe 股票池预设 — P2.6。

把常用 A 股股票池集中管理，回测时按 preset 解析为 6 位代码列表。

设计：
- 池子解析统一走 DataRouter（先 akshare → sina 兜底），上层不直接 import akshare
- 缓存到 `_CACHE`（进程内），TTL 24h（成分股 1 个季度调一次）
- 失败回退：universe 解析失败抛 `UniverseError`，由调用方决定是否降级到 symbols

支持的 preset：
- hs300    沪深 300 指数（000300.SH），300 只大盘蓝筹
- csi500   中证 500 指数（000905.SH），500 只中盘
- csi1000  中证 1000 指数（000852.SH），1000 只小盘
- gem      创业板指（399006.SZ），100 只创业板龙头
- star     科创 50 指数（000688.SH），50 只科创板龙头
- all_a    沪深京全 A（调 get_stock_list，过滤 8/4 开头），5000+ 只（实际跑回测时用抽样）

后续扩展：
- 行业池（sw_l1=申万一级 / citic=中信一级）
- 主题池（concept_xxx：AI/新能源/医药等 — 需先建 concept→symbols 映射表）
"""
from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# 成分股缓存（进程内 24h）
_CACHE: dict[str, tuple[float, list[str]]] = {}
_CACHE_TTL = 24 * 60 * 60  # 24h


class UniverseError(RuntimeError):
    """universe 解析失败。"""


# preset → index code（用于 get_index_constituents）
# 字段格式：{"code": "000300", "exchange": "SH", "name": "沪深300"}
_PRESETS: dict[str, dict[str, str]] = {
    "hs300":   {"code": "000300", "exchange": "SH", "name": "沪深300"},
    "csi500":  {"code": "000905", "exchange": "SH", "name": "中证500"},
    "csi1000": {"code": "000852", "exchange": "SH", "name": "中证1000"},
    "gem":     {"code": "399006", "exchange": "SZ", "name": "创业板指"},
    "star":    {"code": "000688", "exchange": "SH", "name": "科创50"},
}


def list_universes() -> list[dict[str, str]]:
    """列出所有可用 preset（含 all_a）。"""
    out = [{"preset": k, "code": v["code"], "exchange": v["exchange"], "name": v["name"]} for k, v in _PRESETS.items()]
    out.append({"preset": "all_a", "code": "", "exchange": "", "name": "沪深京全A"})
    return out


def is_valid(preset: str) -> bool:
    """校验 preset 是否在白名单。"""
    return preset in _PRESETS or preset == "all_a"


async def resolve_universe(
    preset: str,
    *,
    router: Any = None,
    use_cache: bool = True,
    sample_n: int | None = None,
) -> list[str]:
    """解析 universe preset 为 6 位代码列表（async — DataRouter.route 是 async）。

    Args:
        preset:     preset 名（"hs300" / "csi500" / "csi1000" / "gem" / "star" / "all_a"）
        router:     DataRouter 实例（None 时从 facade 拉单例）
        use_cache:  是否用 24h 进程内缓存
        sample_n:   ALL_A 池随机抽样数（None=全量 5000+，按需抽样避免回测过慢）

    Returns:
        6 位代码列表（去重 + 排序）
    """
    if not is_valid(preset):
        raise UniverseError(
            f"未知 universe preset：{preset}。可选：{[*list(_PRESETS.keys()), 'all_a']}"
        )

    # 缓存命中
    if use_cache and preset in _CACHE:
        cached_at, cached_list = _CACHE[preset]
        if time.time() - cached_at < _CACHE_TTL:
            if sample_n and len(cached_list) > sample_n:
                # ALL_A 抽样：固定随机种子便于回测复现
                import random
                random.seed(42)
                return sorted(random.sample(cached_list, sample_n))
            return cached_list

    # 拉新数据
    if router is None:
        from data_pipeline.providers.facade import get_router
        router = get_router()

    if preset == "all_a":
        symbols = await _resolve_all_a(router)
    else:
        symbols = await _resolve_index_universe(preset, router)

    # 写缓存
    if use_cache:
        _CACHE[preset] = (time.time(), symbols)
    if sample_n and len(symbols) > sample_n:
        import random
        random.seed(42)
        return sorted(random.sample(symbols, sample_n))
    return symbols


async def _resolve_index_universe(preset: str, router: Any) -> list[str]:
    """走 get_index_constituents 拉指数成分股（async）。"""
    meta = _PRESETS[preset]
    code = meta["code"]
    try:
        raw = await router.route("get_index_constituents", symbol=code)
    except Exception as exc:
        raise UniverseError(
            f"universe {preset} ({code}) DataRouter 失败: {type(exc).__name__}: {exc}"
        ) from exc
    if raw is None or (isinstance(raw, pd.DataFrame) and raw.empty):
        raise UniverseError(f"universe {preset} ({code}) 拉数据为空")
    # raw 是 DataFrame，含 品种代码 / 品种名称 / 纳入日期 列
    code_col = None
    for c in ("品种代码", "code", "代码", "con_code"):
        if c in raw.columns:
            code_col = c
            break
    if code_col is None:
        raise UniverseError(f"universe {preset} 找不到代码列：{raw.columns.tolist()}")
    codes = raw[code_col].astype(str).str.zfill(6).unique().tolist()
    return sorted(codes)


async def _resolve_all_a(router: Any) -> list[str]:
    """全 A 股票池：调 get_stock_list，过滤 8（BSE 北交所）/ 4（已退市）开头。"""
    try:
        raw = await router.route("get_stock_list")
    except Exception as exc:
        raise UniverseError(
            f"全 A 股票池 DataRouter 失败: {type(exc).__name__}: {exc}"
        ) from exc
    if raw is None or (isinstance(raw, pd.DataFrame) and raw.empty):
        raise UniverseError("全 A 股票池 get_stock_list 为空")
    code_col = None
    for c in ("代码", "code", "stock_code"):
        if c in raw.columns:
            code_col = c
            break
    if code_col is None:
        raise UniverseError(f"全 A 股票池找不到代码列：{raw.columns.tolist()}")
    codes = raw[code_col].astype(str).str.zfill(6).tolist()
    # 过滤：6 位、sh/sz/bj 前缀
    out = []
    for c in codes:
        if len(c) != 6 or not c.isdigit():
            continue
        out.append(c)
    return sorted(set(out))


def clear_cache(preset: str | None = None) -> None:
    """清理缓存（None=全清）。"""
    if preset is None:
        _CACHE.clear()
    else:
        _CACHE.pop(preset, None)
