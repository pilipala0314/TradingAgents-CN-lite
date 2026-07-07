"""Scheduler Status Service — 全局 scheduler 注册表（P0-T7）。

目的：
- main.py 的 lifespan 启动 AsyncIOScheduler 后，把实例 set 进这里
- admin.py 路由 / scheduler 状态接口从这里取 jobs / next run time
- 任何路由（signals / dashboard / health）都能读

设计：
- 线程安全：用 threading.Lock 保护 setter / getter
- 状态读是只读快照：get_jobs() 返回 list[dict]，每次新 copy
- next_run_time 用 APScheduler 原生 Job.next_run_time（DateTime 或 None）
- 没有 scheduler 时（lifespan 没启动 / 测试 mock），返回空 jobs + 标记 running=False
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_scheduler_ref: Any = None  # apscheduler.schedulers.asyncio.AsyncIOScheduler | None


def set_scheduler(scheduler: Any) -> None:
    """main.py lifespan 启动后调用，把 scheduler 实例登记到全局。

    测试也可以传入 mock 对象（只要有 .get_jobs() 即可）。
    """
    global _scheduler_ref
    with _lock:
        _scheduler_ref = scheduler
    logger.info("[scheduler_status] scheduler registered")


def clear_scheduler() -> None:
    """main.py lifespan 关闭时调用（测试可重置）。"""
    global _scheduler_ref
    with _lock:
        _scheduler_ref = None


def get_scheduler() -> Any:
    """返回当前 scheduler 实例（无则 None）。"""
    with _lock:
        return _scheduler_ref


def _safe_get_jobs() -> list[Any]:
    """安全读 jobs，scheduler 不存在或抛错 → 返回空 list。"""
    sched = get_scheduler()
    if sched is None:
        return []
    try:
        get_jobs = getattr(sched, "get_jobs", None)
        if not callable(get_jobs):
            return []
        jobs = get_jobs()
        return list(jobs) if jobs else []
    except Exception as exc:  # pragma: no cover
        logger.warning("[scheduler_status] get_jobs 失败: %s", exc)
        return []


def _format_next_run(next_run: Any) -> str | None:
    """把 APScheduler next_run_time 转 ISO 字符串。None/异常 → None。"""
    if next_run is None:
        return None
    try:
        # next_run 是 tz-aware datetime（Asia/Shanghai）
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        return next_run.isoformat()
    except Exception:
        return None


def collect_scheduler_status() -> dict[str, Any]:
    """返回前端消费的 scheduler 状态快照。

    Schema:
    ```json
    {
      "running": true,
      "timezone": "Asia/Shanghai",
      "job_count": 2,
      "jobs": [
        {
          "id": "daily_close_etl",
          "name": "_safe_dispatch_yesterday",
          "trigger": "cron[day_of_week='mon-fri', hour='17', minute='0']",
          "next_run_time": "2026-07-08T17:00:00+08:00",
          "next_run_in_sec": 86400,
          "coalesce": true,
        },
        ...
      ],
      "fetched_at": "2026-07-07T20:30:00+08:00"
    }
    ```
    """
    sched = get_scheduler()
    running = sched is not None
    timezone_str = ""
    if sched is not None:
        tz = getattr(sched, "timezone", None)
        timezone_str = str(tz) if tz is not None else ""

    now = datetime.now(timezone.utc)
    jobs_out: list[dict[str, Any]] = []
    for job in _safe_get_jobs():
        try:
            trigger = job.trigger
            trigger_str = str(trigger) if trigger is not None else ""
            next_run = job.next_run_time
            next_run_iso = _format_next_run(next_run)
            next_run_in_sec: float | None = None
            if next_run is not None and next_run.tzinfo is not None:
                delta = (next_run - now).total_seconds()
                next_run_in_sec = max(0.0, delta)
        except Exception as exc:  # pragma: no cover
            logger.warning("[scheduler_status] 解析 job=%s 失败: %s", getattr(job, "id", "?"), exc)
            continue

        jobs_out.append({
            "id": getattr(job, "id", "") or "",
            "name": getattr(job, "name", "") or getattr(job, "func_ref", getattr(job, "func", "")) or "",
            "trigger": trigger_str,
            "next_run_time": next_run_iso,
            "next_run_in_sec": next_run_in_sec,
            "coalesce": bool(getattr(job, "coalesce", False)),
            "max_instances": getattr(job, "max_instances", 1),
        })

    return {
        "running": running,
        "timezone": timezone_str,
        "job_count": len(jobs_out),
        "jobs": jobs_out,
        "fetched_at": now.isoformat(),
    }
