"""Celery tasks — ETL 批任务入口。

注册到 broker 后，可用 `from apps.worker.celery_app import celery_app; `
或者通过 flower UI / `celery call` 命令触发。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from apps.worker.celery_app import celery_app
from core.logger import setup_logging
from data_pipeline.etl import FactETLPipeline
from data_pipeline.etl.hk_kline_etl import HKKlineETL

setup_logging()
logger = logging.getLogger(__name__)


async def _run_close_async(
    business_date: date,
    *,
    symbols: list[str] | None = None,
    event_file_path: str | None = None,
) -> dict:
    pipeline = FactETLPipeline()
    # run_close_pipeline 内部自管 session，外部不传
    return await pipeline.run_close_pipeline(
        business_date=business_date,
        symbols=symbols,
        event_file_path=event_file_path,
    )


# ── Celery 任务 ──────────────────────────────────────────────────────
@celery_app.task(name="ashare.run_close_etl", bind=True, max_retries=2)
def run_close_etl(
    self,
    business_date_str: str,
    symbols: list[str] | None = None,
    event_file_path: str | None = None,
) -> dict:
    """收盘后事实层 ETL（默认对接 close window）。

    失败策略：自动重试 2 次，间隔 60s。
    """
    business_date = date.fromisoformat(business_date_str)
    try:
        return asyncio.run(
            _run_close_async(
                business_date, symbols=symbols, event_file_path=event_file_path
            )
        )
    except Exception as exc:
        logger.exception("ETL 失败，准备重试: %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc


@celery_app.task(name="ashare.run_close_etl_yesterday", bind=True, max_retries=2)
def run_close_etl_yesterday(self) -> dict:
    """便捷任务：自动取昨日（工作日 17:00 之后触发）。"""
    yesterday = (datetime.now() - timedelta(days=1)).date()
    return run_close_etl(yesterday.isoformat())


# ── P0-T1: 港股 K 线 ETL ──────────────────────────────────────────────


async def _run_hk_close_async(
    business_date: date,
    *,
    symbols: list[str] | None = None,
    datalen: int = 30,
) -> dict:
    """P0-T1: 港股 K 线 ETL（先 sync 主数据，再拉 K 线）。"""
    from core.database import AsyncSessionLocal

    etl = HKKlineETL()
    async with AsyncSessionLocal() as session:
        try:
            result = await etl.run_full(
                session,
                business_date=business_date,
                symbols=symbols,
                datalen=datalen,
            )
            await session.commit()
            return {
                "security_sync_count": result["security_sync"]["synced_count"],
                "kline_synced_count": result["kline"]["synced_count"],
                "kline_failure_count": result["kline"]["failure_count"],
            }
        except Exception:
            await session.rollback()
            raise


@celery_app.task(name="ashare.run_hk_close_etl", bind=True, max_retries=2)
def run_hk_close_etl(
    self,
    business_date_str: str | None = None,
    symbols: list[str] | None = None,
    datalen: int = 30,
) -> dict:
    """P0-T1: 港股 K 线 ETL。

    失败策略：自动重试 2 次，间隔 60s。
    """
    business_date = (
        date.fromisoformat(business_date_str)
        if business_date_str
        else (datetime.now() - timedelta(days=1)).date()
    )
    try:
        return asyncio.run(
            _run_hk_close_async(
                business_date, symbols=symbols, datalen=datalen
            )
        )
    except Exception as exc:
        logger.exception("港股 K 线 ETL 失败，准备重试: %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc
