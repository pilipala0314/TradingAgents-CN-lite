"""``python -m indicators`` — 构建并落盘单板块指标包。

退出码：0=写出成功（封锁导致的大量 failed 不算错误）；1=注册表/落盘等致命失败。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from indicators.pipeline import build_sector_pack
from indicators.store import pack_path, write_pack

_TZ = timezone(timedelta(hours=8))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m indicators",
        description="L2 源级融合 — 构建板块指标包（IndicatorPack）",
    )
    parser.add_argument("--sector", required=True, help="板块名，如 半导体")
    parser.add_argument("--trade-date", default=None, help="YYYY-MM-DD 或 YYYYMMDD；缺省=今天（+08）")
    parser.add_argument("--round", type=int, default=1, dest="generation_round", help="第几轮拉取（2=二次刷新）")
    parser.add_argument("--force", action="store_true", help="绕过时点闸门")
    parser.add_argument("--registry", default=None, help="注册表 YAML 路径（缺省=项目内置）")
    parser.add_argument("--reports-dir", default="reports", help="产物目录（缺省 reports/）")
    parser.add_argument("--print", action="store_true", dest="print_summary", help="额外打印 pack.summary()")
    args = parser.parse_args(argv)

    trade_date = args.trade_date or datetime.now(_TZ).date().isoformat()
    try:
        registry = None
        if args.registry:
            from foundation.registry import load_registry

            registry = load_registry(args.registry)

        pack = asyncio.run(
            build_sector_pack(
                args.sector,
                trade_date,
                registry=registry,
                generation_round=args.generation_round,
                force=args.force,
            )
        )
        path = pack_path(args.reports_dir, pack.meta.trade_date, args.sector, pack.meta.generation_round)
        write_pack(pack, path)
    except Exception as exc:  # noqa: BLE001 - CLI 顶层兜底：输出根因并以 1 退出
        print(f"失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"pack 已写入: {path}")
    if args.print_summary:
        print(json.dumps(pack.summary(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())