from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import JobStatus
from models.facts import OpsJobRun


class OpsAuditService:
    """记录 ETL / 特征 / 输出任务的执行审计信息。"""

    @staticmethod
    async def start_job(
        session: AsyncSession,
        *,
        job_type: str,
        business_date: date,
        window_type: str,
        idempotency_key: str,
        input_version: int = 1,
        feature_version: int | None = None,
        rule_version: int | None = None,
        output_version: int = 1,
        trace_payload: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        existing_job_id = await session.scalar(
            select(OpsJobRun.job_id).where(OpsJobRun.idempotency_key == idempotency_key)
        )
        if existing_job_id:
            await session.execute(
                OpsJobRun.__table__
                .update()
                .where(OpsJobRun.job_id == existing_job_id)
                .values(
                    status=JobStatus.RUNNING.value,
                    started_at=datetime.utcnow(),
                    finished_at=None,
                    error_message=None,
                    trace_payload=trace_payload,
                )
            )
            await session.commit()
            return existing_job_id

        job = OpsJobRun(
            job_type=job_type,
            business_date=business_date,
            window_type=window_type,
            idempotency_key=idempotency_key,
            status=JobStatus.RUNNING.value,
            input_version=input_version,
            feature_version=feature_version,
            rule_version=rule_version,
            output_version=output_version,
            trace_payload=trace_payload,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.job_id

    @staticmethod
    async def finish_job_success(
        session: AsyncSession,
        job_id: uuid.UUID,
        *,
        output_ref_type: str | None = None,
        output_ref_id: uuid.UUID | None = None,
        trace_payload: dict[str, Any] | None = None,
    ) -> None:
        await session.execute(
            OpsJobRun.__table__
            .update()
            .where(OpsJobRun.job_id == job_id)
            .values(
                status=JobStatus.SUCCESS.value,
                finished_at=datetime.utcnow(),
                output_ref_type=output_ref_type,
                output_ref_id=output_ref_id,
                trace_payload=trace_payload,
            )
        )
        await session.commit()

    @staticmethod
    async def finish_job_failed(
        session: AsyncSession,
        job_id: uuid.UUID,
        error_message: str,
        *,
        trace_payload: dict[str, Any] | None = None,
    ) -> None:
        await session.execute(
            OpsJobRun.__table__
            .update()
            .where(OpsJobRun.job_id == job_id)
            .values(
                status=JobStatus.FAILED.value,
                finished_at=datetime.utcnow(),
                error_message=error_message[:5000],
                trace_payload=trace_payload,
            )
        )
        await session.commit()
