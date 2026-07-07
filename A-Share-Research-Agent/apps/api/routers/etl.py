"""ETL 端点。"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from apps.api.schemas import PipelineRequest
from data_pipeline.etl import FactETLPipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/etl", tags=["etl"])


@router.post("/close")
async def run_close_etl(payload: PipelineRequest) -> dict[str, Any]:
    """执行收盘 ETL 主链：主数据 -> 行情 -> 资金流 -> 事件 -> 特征。"""
    pipeline = FactETLPipeline()
    try:
        # run_close_pipeline 内部自管 session，不要外面传
        results = await pipeline.run_close_pipeline(
            business_date=payload.business_date,
            symbols=payload.symbols,
            event_file_path=payload.event_file_path,
        )
        return {"ok": True, "results": results}
    except Exception as exc:
        logger.error("ETL 执行失败", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
