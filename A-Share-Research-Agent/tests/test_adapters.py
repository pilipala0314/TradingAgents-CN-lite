"""adapters.py 单元测试 — 协议 / 工厂 / 内置 adapter。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from data_pipeline.adapters import (
    AdaptedProvider,
    adapt,
    check_signatures_compatible,
    make_kwarg_renamer,
    make_list_to_single_arg_adapter,
    make_passthrough_adapter,
    make_single_to_list_arg_adapter,
    resolve_method_entry,
)

# ── 协议：AdaptedProvider ──────────────────────────────────────

class TestAdaptedProvider:
    @pytest.mark.asyncio
    async def test_no_adapter_passthrough(self) -> None:
        async def fn(x: int) -> int:
            return x * 2

        ap = AdaptedProvider(fn=fn, adapter=None)
        result = await ap(5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_with_sync_adapter(self) -> None:
        async def fn(x: int) -> int:
            return x * 2

        def adapter(args, kwargs):
            # 把 x 转成 x*10
            new_args = (args[0] * 10,)
            return new_args, {}

        ap = AdaptedProvider(fn=fn, adapter=adapter)
        assert await ap(5) == 100

    @pytest.mark.asyncio
    async def test_with_kwargs_adapter(self) -> None:
        async def fn(code: str) -> str:
            return f"got:{code}"

        def adapter(args, kwargs):
            # 把 symbol=xxx 改成 code=xxx
            new_kwargs = {**kwargs}
            if "symbol" in new_kwargs:
                new_kwargs["code"] = new_kwargs.pop("symbol")
            return args, new_kwargs

        ap = AdaptedProvider(fn=fn, adapter=adapter)
        assert await ap(symbol="000636") == "got:000636"


# ── 工厂：adapt() ──────────────────────────────────────────────

class TestAdaptFactory:
    def test_string_no_adapter(self) -> None:
        result = adapt("get_quote")
        assert result == "get_quote"

    def test_string_with_provider(self) -> None:
        class P:
            async def get_quote(self, x: int) -> int:
                return x

        result = adapt("get_quote", provider=P())
        assert isinstance(result, AdaptedProvider)
        assert result.attr_name == "get_quote"

    def test_string_with_adapter_and_provider(self) -> None:
        class P:
            async def get_quote(self, x: int) -> int:
                return x

        def adapter(args, kwargs):
            return (args[0] * 3,), {}

        result = adapt("get_quote", adapter=adapter, provider=P())
        assert isinstance(result, AdaptedProvider)

    def test_callable_input(self) -> None:
        async def my_fn(x: int) -> int:
            return x

        result = adapt(my_fn)
        assert isinstance(result, AdaptedProvider)

    def test_invalid_input_raises(self) -> None:
        with pytest.raises(TypeError):
            adapt(123)  # type: ignore[arg-type]


class TestResolveMethodEntry:
    def test_resolve_string(self) -> None:
        class P:
            async def get_quote(self):
                return 42

        entry = resolve_method_entry("get_quote", P())
        assert isinstance(entry, AdaptedProvider)

    def test_resolve_adapted_provider(self) -> None:
        async def fn():
            return 1

        ap = AdaptedProvider(fn=fn)
        entry = resolve_method_entry(ap, None)
        assert entry is ap

    def test_resolve_callable(self) -> None:
        async def fn():
            return 1

        entry = resolve_method_entry(fn, None)
        assert entry is fn

    def test_resolve_string_missing_attr_raises(self) -> None:
        class P:
            pass

        with pytest.raises(AttributeError):
            resolve_method_entry("missing_method", P())


# ── 内置 adapter ────────────────────────────────────────────────

class TestPassthroughAdapter:
    def test_passthrough(self) -> None:
        ap = make_passthrough_adapter()
        args, kwargs = ap((1, 2, 3), {"x": "y"})
        assert args == (1, 2, 3)
        assert kwargs == {"x": "y"}


class TestKwargRenamer:
    def test_rename_single_key(self) -> None:
        renamer = make_kwarg_renamer(symbol="code")
        args, kwargs = renamer((), {"symbol": "000636", "page_size": 20})
        assert kwargs == {"code": "000636", "page_size": 20}

    def test_rename_preserves_unrelated(self) -> None:
        renamer = make_kwarg_renamer(a="x", b="y")
        args, kwargs = renamer((), {"a": 1, "b": 2, "c": 3})
        assert kwargs == {"x": 1, "y": 2, "c": 3}

    def test_rename_missing_key_noop(self) -> None:
        renamer = make_kwarg_renamer(missing="replacement")
        args, kwargs = renamer((), {"x": 1})
        assert kwargs == {"x": 1}


class TestListToSingleArg:
    def test_unlist_single_element(self) -> None:
        unlist = make_list_to_single_arg_adapter(arg_index=0)
        args, kwargs = unlist((["000636"],), {})
        assert args == ("000636",)

    def test_unlist_multi_element_noop(self) -> None:
        unlist = make_list_to_single_arg_adapter(arg_index=0)
        args, kwargs = unlist((["000636", "600519"],), {})
        assert args == (["000636", "600519"],)

    def test_unlist_empty_args(self) -> None:
        unlist = make_list_to_single_arg_adapter(arg_index=0)
        args, kwargs = unlist((), {})
        assert args == ()


class TestSingleToList:
    def test_to_list_single_string(self) -> None:
        tolist = make_single_to_list_arg_adapter(arg_index=0)
        args, kwargs = tolist(("000636",), {})
        assert args == (["000636"],)

    def test_to_list_already_list_noop(self) -> None:
        tolist = make_single_to_list_arg_adapter(arg_index=0)
        args, kwargs = tolist((["000636", "600519"],), {})
        assert args == (["000636", "600519"],)


# ── 签名 lint ─────────────────────────────────────────────────

class TestCheckSignatures:
    def test_compatible_signatures(self) -> None:
        async def fn1(x, y):
            return x

        async def fn2(x, y):
            return x

        warnings = check_signatures_compatible("test_method", {"a": fn1, "b": fn2})
        assert warnings == []

    def test_incompatible_warns(self) -> None:
        async def fn1(x, y):
            return x

        async def fn2(x, y, z, w, v):
            return x

        warnings = check_signatures_compatible("test_method", {"a": fn1, "b": fn2})
        assert len(warnings) == 1
        assert "test_method" in warnings[0]
        assert "fn2" in warnings[0] or "'b'" in warnings[0]

    def test_single_provider_no_warning(self) -> None:
        async def fn1(x, y, z, w, v):
            return x

        warnings = check_signatures_compatible("test", {"a": fn1})
        assert warnings == []
