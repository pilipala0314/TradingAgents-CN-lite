"""DataRouter Adapter 层 — 统一参数签名，优雅处理多 provider 签名差异。

## 设计动机

不同 provider 的同名方法参数签名往往不一致：
- `akshare.get_daily_bars(symbol, start_date, end_date, adjust)`
- `sina.get_daily_bars(symbol, scale, datalen)`
- `netease.get_daily_bars(symbol, start, end, adjust)`

直接放在同一个 fallback 链里会因为参数错位产生**静默错误**（akshare 收到
`(symbol, scale, datalen)` 会把它当 `(symbol, start_date, scale, end_date, datalen)`
处理，乱码而不抛错）。

## 解决方案 — Adapter 协议

定义统一的"标准签名" + 每个 provider 写一个 adapter：
- adapter 是 callable `(args, kwargs) -> (args, kwargs)`
- DataRouter 在调用 provider 之前先 `args, kwargs = adapter(args, kwargs)`
- 这样调用方按"标准签名"传参即可，零硬编码

## 写法

```python
from data_pipeline.adapters import adapt

# 简单 adapter — 调整位置参数
def _sina_daily_bars_adapter(args, kwargs):
    # 标准签名：(symbol, scale, datalen) — sina 本身就是这个签名，直接透传
    return args, kwargs

# 复杂 adapter — kwargs 重命名
def _akshare_daily_bars_adapter(args, kwargs):
    # 标准签名：(symbol, scale, datalen) → akshare 签名 (symbol, start_date, end_date, adjust)
    symbol = args[0] if args else kwargs.get("symbol", "")
    scale = kwargs.get("scale", 240)
    datalen = kwargs.get("datalen", 60)
    if scale == 240:  # 日 K
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=datalen * 2)).strftime("%Y%m%d")
        return (symbol,), {"start_date": start, "end_date": end, "adjust": ""}
    return (symbol,), kwargs
```

## 注入到 DataRouter

```python
DataRouter(
    providers={...},
    method_table={
        "get_daily_bars": {
            "akshare": adapt("get_daily_bars", _akshare_daily_bars_adapter),
            "sina": "get_daily_bars",  # 字符串 = 无 adapter，原生签名
        },
    },
)
```
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── 协议 ──────────────────────────────────────────────────────────────

# 同步 adapter：args/kwargs → args/kwargs
SyncAdapter = Callable[[tuple, dict], tuple[tuple, dict]]

# 异步 adapter：少见，留接口
AsyncAdapter = Callable[[tuple, dict], Awaitable[tuple[tuple, dict]]]

Adapter = SyncAdapter | AsyncAdapter


@dataclass
class AdaptedProvider:
    """Provider 方法的 adapter 包装。

    Attributes:
        fn:        原 provider 方法（async callable）
        adapter:   参数签名 adapter（可选）
        attr_name: provider 上原属性名（用于统计 / 日志）
    """

    fn: Callable[..., Awaitable[Any]]
    adapter: Adapter | None = None
    attr_name: str = ""

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.adapter is not None:
            new_args, new_kwargs = self.adapter(args, kwargs)
            return await self.fn(*new_args, **new_kwargs)
        return await self.fn(*args, **kwargs)


def adapt(
    fn_or_attr: Callable[..., Awaitable[Any]] | str,
    adapter: Adapter | None = None,
    *,
    provider: Any = None,
) -> AdaptedProvider | str:
    """工厂函数：构造 AdaptedProvider 或直接返回 attr 名字符串。

    支持 4 种用法：
    1. `adapt("method_name")`  — 仅返回字符串（无 adapter 无 provider，最简）
    2. `adapt("method_name", provider=provider)` — 从 provider 实例拿方法，返回 AdaptedProvider
    3. `adapt("method_name", adapter=fn, provider=provider)` — 从 provider 拿方法 + adapter
    4. `adapt(callable, adapter=fn)` — 直接给 callable 包 adapter

    Args:
        fn_or_attr: 字符串 = provider 上方法名（需要同时传 provider=）
                     callable = 直接传方法
        adapter:    可选 adapter 函数
        provider:   当 fn_or_attr 是字符串时，从这个 provider 实例上 get 方法
    """
    # 用法 1：纯字符串无 adapter 无 provider — 返回字符串
    if isinstance(fn_or_attr, str) and adapter is None and provider is None:
        return fn_or_attr

    # 用法 2/3：字符串 + provider（adapter 可选）
    if isinstance(fn_or_attr, str) and provider is not None:
        actual_fn = getattr(provider, fn_or_attr, None)
        if actual_fn is None:
            raise AttributeError(f"provider 上找不到方法: {fn_or_attr}")
        return AdaptedProvider(fn=actual_fn, adapter=adapter, attr_name=fn_or_attr)

    # 用法 4：直接传 callable
    if callable(fn_or_attr):
        attr_name = getattr(fn_or_attr, "__name__", "")
        return AdaptedProvider(fn=fn_or_attr, adapter=adapter, attr_name=attr_name)

    raise TypeError(f"adapt() 第一个参数必须是 str 或 callable，得到 {type(fn_or_attr)}")


def resolve_method_entry(
    entry: str | AdaptedProvider | Any,
    provider: Any,
) -> AdaptedProvider | Any:
    """解析 method_table 中的单个 entry → 可调用对象。

    Args:
        entry:    method_table 中的值（字符串 / AdaptedProvider / 直接 callable）
        provider: provider 实例

    Returns:
        - AdaptedProvider  — 已是包装好的
        - 字符串           → 用 getattr 取 provider 方法
        - callable        → 直接返回

    Raises:
        AttributeError: provider 上没有该方法
    """
    if isinstance(entry, AdaptedProvider):
        return entry
    if isinstance(entry, str):
        fn = getattr(provider, entry, None)
        if fn is None:
            raise AttributeError(f"provider 上找不到方法: {entry}")
        return AdaptedProvider(fn=fn, adapter=None, attr_name=entry)
    if callable(entry):
        return entry
    raise TypeError(f"method_table entry 类型不支持: {type(entry)}")


# ── 内置常用 adapter ─────────────────────────────────────────────────

def make_passthrough_adapter() -> SyncAdapter:
    """透传 adapter — args/kwargs 原封不动返回。"""
    def _passthrough(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
        return args, kwargs
    return _passthrough


def make_kwarg_renamer(**renames: str) -> SyncAdapter:
    """生成重命名 kwargs key 的 adapter。

    Example:
        renamer = make_kwarg_renamer(symbol="code", page_size="limit")
        # 调用：fn(symbol="000636", page_size=20)
        # → fn(code="000636", limit=20)
    """
    def _rename(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
        new_kwargs = dict(kwargs)
        for old_key, new_key in renames.items():
            if old_key in new_kwargs:
                new_kwargs[new_key] = new_kwargs.pop(old_key)
        return args, new_kwargs
    return _rename


def make_list_to_single_arg_adapter(arg_index: int = 0) -> SyncAdapter:
    """list[1 个元素] → 单个元素的 adapter（netease.get_tencent_quote 用）。

    Example:
        # facade 调用 get_tencent_quote([code])
        # → netease.get_tencent_quote(code)  (剥掉 list 包装)
    """
    def _unlist(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
        if args and isinstance(args[arg_index], (list, tuple)) and len(args[arg_index]) == 1:
            new_args = list(args)
            new_args[arg_index] = args[arg_index][0]
            return tuple(new_args), kwargs
        return args, kwargs
    return _unlist


def make_single_to_list_arg_adapter(arg_index: int = 0) -> SyncAdapter:
    """单个元素 → list 包装的 adapter（反向）。"""
    def _tolist(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
        if args and not isinstance(args[arg_index], (list, tuple)):
            new_args = list(args)
            new_args[arg_index] = [args[arg_index]]
            return tuple(new_args), kwargs
        return args, kwargs
    return _tolist


# ── 签名 lint 工具（开发用） ───────────────────────────────────────

def check_signatures_compatible(
    method: str,
    fns: dict[str, Callable[..., Any]],
) -> list[str]:
    """检查同一 method 下多个 provider 的签名是否兼容（同 positional 数 + 同 key）。

    Returns:
        警告 list（空 list = 全部兼容）
    """
    warnings: list[str] = []
    if len(fns) < 2:
        return warnings
    ref_name, ref_fn = next(iter(fns.items()))
    try:
        ref_sig = inspect.signature(ref_fn)
    except (ValueError, TypeError):
        return warnings
    ref_positional = [
        p for p in ref_sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY)
    ]
    set(ref_sig.parameters.keys())

    for name, fn in fns.items():
        if name == ref_name:
            continue
        try:
            sig = inspect.signature(fn)
        except (ValueError, TypeError) as exc:
            logger.debug("inspect.signature 跳过 fn=%s: %s", name, exc)
            continue
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY)
        ]
        # 宽松检查：positional 数差 ≤1 算可适配（varargs 兜底）
        if len(positional) > len(ref_positional) + 1:
            warnings.append(
                f"{method}: provider '{name}' 有 {len(positional)} 个位置参数，"
                f"超过参考 '{ref_name}' 的 {len(ref_positional)} 个，"
                "建议加 adapter 包装"
            )
    return warnings
