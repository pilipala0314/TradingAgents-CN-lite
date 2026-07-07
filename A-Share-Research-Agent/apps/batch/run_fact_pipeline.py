from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

from core.logger import setup_logging
from data_pipeline.etl import FactETLPipeline

setup_logging()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行事实层 ETL 批任务。")
    parser.add_argument("--business-date", required=True, help="业务日期，格式 YYYY-MM-DD")
    parser.add_argument(
        "--symbols",
        default="",
        help="可选，限制同步的股票代码，逗号分隔，例如 000001,600519",
    )
    parser.add_argument(
        "--event-file-path",
        default="",
        help="可选，本地事件文件路径，支持 csv/json/jsonl",
    )
    return parser


async def _main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    business_date = date.fromisoformat(args.business_date)
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()] or None
    event_file_path = args.event_file_path or None

    pipeline = FactETLPipeline()
    # pipeline 内部自管 session（见 FactETLPipeline.run_close_pipeline），
    # 外部不传 AsyncSession。
    result = await pipeline.run_close_pipeline(
        business_date=business_date,
        symbols=symbols,
        event_file_path=event_file_path,
    )
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
