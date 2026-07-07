"""APScheduler — 调度 daily ETL（容器内 API 进程同进程调度，worker 跑任务）。

默认 cron：
- 17:00 (工作日) 触发 run_close_etl_yesterday

不依赖 worker 进程 — 此调度器是 API 进程内的"瘦客户端"，发到 Celery broker。
"""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from apps.worker.tasks import run_close_etl_yesterday

logger = logging.getLogger(__name__)


def start_scheduler() -> AsyncIOScheduler:
    """启动 APScheduler 调度器，注册每日 close-window ETL。"""
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    # 工作日 17:00 跑昨日 close ETL
    scheduler.add_job(
        _safe_dispatch_yesterday,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=0),
        id="daily_close_etl",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("APScheduler 启动完成，已注册 daily_close_etl @ 17:00 (mon-fri)")
    return scheduler


def _safe_dispatch_yesterday() -> None:
    """把 daily task 发到 Celery broker（不阻塞 scheduler）。"""
    try:
        result = run_close_etl_yesterday.apply_async(countdown=5)
        logger.info(
            "[scheduler] 已派发 daily close ETL, task_id=%s, dispatch_time=%s",
            result.id,
            datetime.now().isoformat(),
        )
    except Exception as exc:
        logger.error("[scheduler] 派发 daily close ETL 失败: %s", exc)
