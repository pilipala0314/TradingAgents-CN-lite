"""apps/api/routers/metrics — Prometheus /metrics 端点 + trace 端点。

- GET /metrics         Prometheus 文本格式（运维 Grafana / alertmanager 拉取）
- GET /metrics/traces  最近 N 个 span（调试用）
- GET /metrics/info    App 元信息（version / env / uptime）

注意：FastAPI 0.139 弃用 @on_event，APP_INFO 初始化在 main.py 的 lifespan 中做。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from core.metrics import REGISTRY
from core.telemetry import Tracer

router = APIRouter(prefix="/metrics", tags=["metrics"])
_START_TS = time.time()


@router.get("", include_in_schema=False)
def prometheus_metrics() -> Response:
    """Prometheus 抓取端点（text/plain 格式）。"""
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


@router.get("/traces")
def recent_traces(limit: int = 100) -> dict[str, object]:
    """最近完成的 span（按时间倒序）。"""
    spans = Tracer.get().completed(limit=limit)
    return {"count": len(spans), "spans": spans}


@router.get("/info")
def app_info_endpoint() -> dict[str, object]:
    """App 元信息 + 运行时长。"""
    # 延迟 import 避免 main 启动时 settings 顺序问题
    from core.config import settings
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "env": getattr(settings, "ENV", "dev"),
        "uptime_s": round(time.time() - _START_TS, 2),
    }
