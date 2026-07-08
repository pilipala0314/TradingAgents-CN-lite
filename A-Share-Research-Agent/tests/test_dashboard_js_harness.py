"""P3 浏览器层 regression — 锁住 dashboard.js 中关键纯函数行为。

注意：dashboard.js 在浏览器中运行（无 node module 导入），我们用 minimal harness
模拟关键函数，验证修复后的行为。

P3 fix 1: renderGlobalTables 应 push 对象（带 _code），不是元组数组
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys


def _extract_function(name: str, js: str) -> str:
    """用 'function ' 锚点截取到 'function _renderMarketTable' 或下一个 function。"""
    # 找 "function name(" 起点
    pat = re.compile(rf"function\s+{re.escape(name)}\s*\(", re.MULTILINE)
    m = pat.search(js)
    if not m:
        raise ValueError(f"function {name} not found")
    # 找结尾：从这个起点开始，跟踪花括号配对
    start = m.start()
    # 找开括号
    i = js.find("{", start)
    if i < 0:
        raise ValueError("no opening brace")
    depth = 0
    j = i
    while j < len(js):
        c = js[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return js[start : j + 1]
        j += 1
    raise ValueError("unbalanced braces")


REPO = pathlib.Path(r"d:\my-project\take-it-controll-road2freedom\0.start-here\TradingAgents-CN-lite\a-share-research-agent")
JS = (REPO / "apps/api/static/dashboard.js").read_text(encoding="utf-8")


def test_renderGlobalTables_pushes_object_with_code():
    """renderGlobalTables 必须 push 对象（含 _code）到 bucket，不能 push [code, q] 元组。"""
    fn = _extract_function("renderGlobalTables", JS)
    # 找所有 bucket.push(...) 调用
    pushes = re.findall(r"bucket\.push\((.+?)\);", fn)
    assert pushes, "no bucket.push found"
    for p in pushes:
        # 不能是纯元组 [code, q]
        stripped = p.strip()
        assert not (stripped.startswith("[") and stripped.endswith("]")), (
            f"BUG: bucket.push 用元组 [code, q]，下游 row._code 取不到 code: {stripped}"
        )
        # 必须含 _code
        assert "_code" in p, f"bucket.push 必须 push 含 _code 的对象: {p}"


def test_renderGlobalTables_buckets_definition():
    """4 个桶定义必须存在。"""
    fn = _extract_function("renderGlobalTables", JS)
    assert "cn" in fn
    assert "hk" in fn
    assert "us" in fn
    assert "other" in fn


def test_renderMarketTable_uses_row_underscore_code():
    """_renderMarketTable 用 row._code 读 code 字段。"""
    fn = _extract_function("_renderMarketTable", JS)
    assert "row._code" in fn, "_renderMarketTable 必须用 row._code 取 code"


def test_no_typos_in_overview_render_path():
    """overview 渲染链路整体性：loadOverview → renderHealth → renderGlobalTables。"""
    lo = _extract_function("loadOverview", JS)
    assert "renderGlobalTables" in lo, "loadOverview 必须调 renderGlobalTables"
    assert "renderHealth" in lo, "loadOverview 必须调 renderHealth"


if __name__ == "__main__":
    import traceback
    errors = 0
    funcs = [
        test_renderGlobalTables_pushes_object_with_code,
        test_renderGlobalTables_buckets_definition,
        test_renderMarketTable_uses_row_underscore_code,
        test_no_typos_in_overview_render_path,
    ]
    for fn in funcs:
        try:
            fn()
            print(f"  OK  {fn.__name__}")
        except Exception:
            errors += 1
            print(f"  ERR {fn.__name__}")
            traceback.print_exc()
    print(f"\n=== TOTAL: ok={len(funcs) - errors}  err={errors} ===")
    sys.exit(1 if errors else 0)
