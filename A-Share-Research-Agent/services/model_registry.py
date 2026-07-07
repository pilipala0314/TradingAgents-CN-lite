"""ModelRegistry — 模型注册表服务（P2-T7）。

设计：
- 每个 role 只能有一个 active 模型
- 切换 active：原子事务（先关旧 active，再开新 active）
- config_json: 任意配置（温度 / 提示词 / 超参）
- weight: ensemble 加权

API：
- list_models(role=None) -> List[DimModel]
- get_active(role) -> Optional[DimModel]
- register(name, role, version, ...) -> DimModel
- set_active(role, model_id) -> None
- update_config(model_id, config) -> DimModel
- get_or_create_default(role, name, ...) -> DimModel  # 兜底
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.dimensions import DimModel

logger = logging.getLogger(__name__)


class ModelRegistry:
    """模型注册表服务（单例 by AsyncSession）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_models(self, role: Optional[str] = None) -> list[DimModel]:
        q = select(DimModel).order_by(DimModel.role.asc(), DimModel.version.desc())
        if role:
            q = q.where(DimModel.role == role)
        return list((await self.session.execute(q)).scalars().all())

    async def get_active(self, role: str) -> Optional[DimModel]:
        q = select(DimModel).where(
            DimModel.role == role, DimModel.is_active.is_(True)
        ).order_by(DimModel.version.desc()).limit(1)
        return (await self.session.execute(q)).scalars().first()

    async def register(
        self,
        *,
        name: str,
        role: str,
        version: int = 1,
        model_provider: Optional[str] = None,
        model_name: Optional[str] = None,
        is_active: bool = False,
        weight: Decimal | float = Decimal("1.0"),
        config_json: Optional[dict[str, Any]] = None,
        description: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> DimModel:
        """注册新模型（若 (role, version) 已存在 → 抛 IntegrityError）。"""
        m = DimModel(
            name=name,
            role=role,
            version=version,
            model_provider=model_provider,
            model_name=model_name,
            is_active=is_active,
            weight=Decimal(str(weight)),
            config_json=config_json,
            description=description,
            tenant_id=tenant_id,
        )
        self.session.add(m)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ValueError(
                f"Model (role={role}, version={version}) already exists"
            ) from exc
        logger.info(
            "ModelRegistry.register name=%s role=%s version=%d is_active=%s",
            name, role, version, is_active,
        )
        return m

    async def set_active(self, role: str, model_id: int) -> DimModel:
        """切换某 role 的 active 模型（原子事务）。

        1) 关闭该 role 的所有 active
        2) 打开指定 model_id
        """
        # 1) 关
        await self.session.execute(
            update(DimModel)
            .where(DimModel.role == role, DimModel.is_active.is_(True))
            .values(is_active=False)
        )
        # 2) 开
        target = (
            await self.session.execute(
                select(DimModel).where(
                    DimModel.model_id == model_id, DimModel.role == role
                )
            )
        ).scalars().first()
        if not target:
            raise ValueError(f"Model {model_id} not found for role {role}")
        target.is_active = True
        await self.session.flush()
        logger.info(
            "ModelRegistry.set_active role=%s model_id=%d version=%d",
            role, target.model_id, target.version,
        )
        return target

    async def update_config(
        self, model_id: int, config: dict[str, Any],
    ) -> DimModel:
        target = (
            await self.session.execute(
                select(DimModel).where(DimModel.model_id == model_id)
            )
        ).scalars().first()
        if not target:
            raise ValueError(f"Model {model_id} not found")
        target.config_json = config
        await self.session.flush()
        return target

    async def update_weight(
        self, model_id: int, weight: Decimal | float,
    ) -> DimModel:
        target = (
            await self.session.execute(
                select(DimModel).where(DimModel.model_id == model_id)
            )
        ).scalars().first()
        if not target:
            raise ValueError(f"Model {model_id} not found")
        target.weight = Decimal(str(weight))
        await self.session.flush()
        return target

    async def get_or_create_default(
        self,
        *,
        role: str,
        name: Optional[str] = None,
        model_provider: Optional[str] = None,
        model_name: Optional[str] = None,
        config_json: Optional[dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> DimModel:
        """兜底：若该 role 无 active model → 注册并激活一个默认。"""
        existing = await self.get_active(role)
        if existing:
            return existing
        m = await self.register(
            name=name or f"{role}_default",
            role=role,
            version=1,
            model_provider=model_provider,
            model_name=model_name,
            is_active=True,
            config_json=config_json,
            description="auto-created default",
            tenant_id=tenant_id,
        )
        return m
