"""apps/api/routers/admin — 运维管理端点（P3.5 / P5.3 / P0-T7）。

- GET  /admin/alert/rules                 列出内置 + 自定义告警规则
- GET  /admin/alert/channels              列出告警投递通道
- POST /admin/alert/channel               新增 webhook 通道
- POST /admin/alert/test                  手动触发评估（返回触发的 events）
- POST /admin/alert/dispatch              投递一条自定义事件
- GET  /admin/alert/history?limit=50      告警历史
- POST /admin/retention/run               触发一次数据保留清理
- GET  /admin/retention/status            保留策略 + 表行数
- GET  /admin/tenants                     列出所有 tenant_id
- GET  /admin/tenant/{tenant_id}/usage    单租户数据量
- GET  /admin/scheduler/status            P0-T7 cron job 状态（前端 hero 卡片）
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.health_alert import (
    AlertEvent,
    HealthAlertService,
    WebhookChannel,
)
from services.scheduler_status_service import collect_scheduler_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ── 告警：规则 / 通道 / 触发 ────────────────────────


@router.get("/alert/rules")
def list_alert_rules() -> dict[str, Any]:
    return {"rules": HealthAlertService.get().list_rules()}


@router.get("/alert/channels")
def list_alert_channels() -> dict[str, Any]:
    return {"channels": HealthAlertService.get().list_channels()}


class AddChannelReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., min_length=8)
    headers: dict[str, str] | None = None
    timeout: float = 5.0


@router.post("/alert/channel")
def add_alert_channel(req: AddChannelReq) -> dict[str, Any]:
    ch = WebhookChannel(name=req.name, url=req.url, headers=req.headers, timeout=req.timeout)
    HealthAlertService.get().add_channel(ch)
    return {"ok": True, "name": ch.name}


@router.post("/alert/test")
def alert_test() -> dict[str, Any]:
    """手动触发评估，返回触发的 events。"""
    events = HealthAlertService.get().evaluate_now()
    return {"triggered": len(events), "events": [e.to_dict() for e in events]}


class DispatchReq(BaseModel):
    rule: str = Field(..., min_length=1, max_length=64)
    level: str = Field("warn", pattern="^(info|warn|error|critical)$")
    message: str = Field(..., min_length=1, max_length=512)
    value: float = 0.0
    threshold: float = 0.0


@router.post("/alert/dispatch")
def alert_dispatch(req: DispatchReq) -> dict[str, Any]:
    """投递一条自定义事件（用于外部系统告警 / 测试）。"""
    import uuid as _uuid
    from datetime import datetime, timezone
    event = AlertEvent(
        event_id=_uuid.uuid4().hex,
        rule=req.rule,
        level=req.level,
        message=req.message,
        value=req.value,
        threshold=req.threshold,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    HealthAlertService.get()._dispatch(event)
    return {"ok": True, "event": event.to_dict()}


@router.get("/alert/history")
def alert_history(limit: int = 50) -> dict[str, Any]:
    return {"history": HealthAlertService.get().history(limit=limit)}


# ── 数据保留 ────────────────────────────


@router.get("/retention/status")
async def retention_status() -> dict[str, Any]:
    from services.retention import get_retention_status
    return await get_retention_status()


@router.post("/retention/run")
async def retention_run(dry_run: bool = True) -> dict[str, Any]:
    from services.retention import run_retention
    return await run_retention(dry_run=dry_run)


# ── 租户 ──────────────────────────────


@router.get("/tenants")
async def list_tenants() -> dict[str, Any]:
    from services.tenant import list_tenants_with_usage
    return {"tenants": await list_tenants_with_usage()}


@router.get("/tenant/{tenant_id}/usage")
async def tenant_usage(tenant_id: str) -> dict[str, Any]:
    from services.tenant import get_tenant_usage
    usage = await get_tenant_usage(tenant_id)
    if usage is None:
        raise HTTPException(status_code=404, detail=f"tenant_id={tenant_id} not found")
    return {"tenant_id": tenant_id, "usage": usage}


# ── Scheduler 状态（P0-T7）─────────────────────


@router.get("/scheduler/status")
async def scheduler_status() -> dict[str, Any]:
    """返回 APScheduler 当前状态 + 全部 jobs（前端 signals hero 卡片消费）。

    字段：
    - running       scheduler 实例是否已注册
    - timezone      调度器时区（Asia/Shanghai）
    - job_count     jobs 数量
    - jobs[]        每 job：id / name / trigger / next_run_time / next_run_in_sec / coalesce / max_instances
    - fetched_at    服务端返回时间（ISO）

    不依赖外部 IO，lifespan 未启动时返回 running=False + 空 jobs。
    """
    return collect_scheduler_status()
