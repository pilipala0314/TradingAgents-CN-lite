"""A-Share-Research-Agent API 入口。

- 启动 / 关闭 lifespan
- 挂载各 domain router
- 顶层依赖（settings / logger）
- 启动 APScheduler（每日 17:00 触发 Celery 收盘 ETL）
- P3.4 监控中间件：trace_id 注入 + Prometheus 指标
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.api.middleware import MetricsMiddleware, TracingMiddleware
from apps.api.routers import (
    admin,
    backtest,
    dashboard,
    etl,
    health,
    macro,
    metrics,
    research,
    rules,
)
from apps.scheduler.etl_scheduler import start_scheduler as start_etl_scheduler
from apps.scheduler.research_scheduler import register_research_jobs
from core.config import settings
from core.logger import setup_logging
from core.metrics import APP_INFO
from services.scheduler_status_service import (
    clear_scheduler as _clear_sched,
)
from services.scheduler_status_service import (
    set_scheduler as _set_sched,
)

setup_logging()
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("A-Share-Research-Agent API 启动")
    # P3.4 启动元信息
    APP_INFO.labels(version=settings.VERSION, env=getattr(settings, "ENV", "dev")).set(1)
    scheduler = start_etl_scheduler()
    # P6: 在同一 scheduler 上注册 research job（17:30 daily）
    register_research_jobs(scheduler)
    # P0-T7: 把 scheduler 实例登记到全局（供 /admin/scheduler/status 暴露）
    _set_sched(scheduler)
    try:
        yield
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception as exc:
            logger.warning("scheduler shutdown 失败: %s", exc)
        _clear_sched()
        logger.info("A-Share-Research-Agent API 关闭")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="A 股 AI 投研中台的最小可运行 API 入口。",
    lifespan=lifespan,
)

# P3.4 监控中间件（顺序：tracing 在最外层 → 内层 metrics 仍能拿到 status）
app.add_middleware(MetricsMiddleware)
app.add_middleware(TracingMiddleware)

# 域级 router 注册
app.include_router(health.router)
app.include_router(etl.router)
app.include_router(rules.router)
app.include_router(research.router)
app.include_router(macro.router)
app.include_router(dashboard.router)
app.include_router(backtest.router)
app.include_router(metrics.router)
app.include_router(admin.router)

# 静态资源（Dashboard HTML / JS / CSS）
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def root_index() -> FileResponse:
    """Dashboard 首页（直接打开浏览器即可看可视化）。"""
    return FileResponse(str(STATIC_DIR / "index.html"))
