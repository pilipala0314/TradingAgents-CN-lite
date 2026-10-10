"""列出 datasource 当前可用的数据 API（运行时自检工具）。

用法（项目根目录执行）：
    python skills/using-datasource/scripts/list_apis.py              # 列出全部方法（按分类）
    python skills/using-datasource/scripts/list_apis.py --providers  # 列出已注册 provider 与健康检查
    python skills/using-datasource/scripts/list_apis.py --chains     # 列出多源 fallback 链
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from datasource import get_source
from datasource.method_table import CORE_METHOD_TABLE, EXTENDED_METHOD_TABLE, TOOL_CATEGORIES
from datasource.registry import list_providers


def print_methods() -> None:
    source = get_source()
    available = set(source.available_methods())
    print(f"可用方法总数: {len(available)}（核心 {len(CORE_METHOD_TABLE)} + 扩展 {len(EXTENDED_METHOD_TABLE)}）\n")
    categorized = {tool for cat in TOOL_CATEGORIES.values() for tool in cat["tools"]}
    for name, cat in TOOL_CATEGORIES.items():
        tools = [t for t in cat["tools"] if t in available]
        print(f"[{name}] {cat['description']}")
        for t in tools:
            chain = " → ".join((CORE_METHOD_TABLE.get(t) or EXTENDED_METHOD_TABLE.get(t, {})).keys())
            print(f"    {t}  ({chain})")
        print()
    extras = sorted(available - categorized)
    if extras:
        print("[其他]")
        for t in extras:
            print(f"    {t}")


async def print_providers() -> None:
    source = get_source()
    print(f"已注册 provider: {len(source.providers)} / {len(list_providers())}\n")
    for name, inst in sorted(source.providers.items()):
        try:
            ok = await inst.health_check()
        except Exception as exc:
            ok = f"error: {exc}"
        n = len(inst.supported_methods())
        print(f"    {name:<16} health={ok}  methods={n}")


def print_chains() -> None:
    source = get_source()
    print("多源 fallback 链（声明顺序，运行期按成功率/延迟自适应）:\n")
    for m in sorted(source.available_methods()):
        chain = list(source._method_table.get(m, {}).keys())
        if len(chain) > 1:
            print(f"    {m}: {' → '.join(chain)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="datasource 可用 API 自检")
    parser.add_argument("--providers", action="store_true", help="列出 provider 与健康检查")
    parser.add_argument("--chains", action="store_true", help="列出多源 fallback 链")
    args = parser.parse_args()

    if args.providers:
        asyncio.run(print_providers())
    elif args.chains:
        print_chains()
    else:
        print_methods()


if __name__ == "__main__":
    main()
