"""test_model_registry — P2-T7 Model Registry 单元测试。

覆盖：
- list_models / list by role
- get_active: 有/无 active
- register: 成功 + 重复 (role, version) 报错
- set_active: 原子切换（旧 active 关闭，新 active 打开）
- update_config: 修改配置
- update_weight: 修改权重
- get_or_create_default: 兜底创建
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from models.dimensions import DimModel
from services.model_registry import ModelRegistry


def _make_model(*, model_id=1, role="r1", version=1, is_active=False, name="m1",
                config=None, weight=Decimal("1.0")):
    m = DimModel(
        model_id=model_id,
        name=name,
        role=role,
        version=version,
        is_active=is_active,
        weight=weight,
        config_json=config,
    )
    # 真实字段（用于 model_id 等属性读取）
    return m


class _FakeResult:
    def __init__(self, items: list) -> None:
        self._items = items

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeScalars:
    def __init__(self, items: list) -> None:
        self._items = items

    def all(self) -> list:
        return self._items

    def first(self):
        return self._items[0] if self._items else None


class TestModelRegistry:
    @pytest.mark.asyncio
    async def test_list_models_no_filter(self) -> None:
        session = MagicMock()
        items = [
            _make_model(model_id=1, role="r1", version=1),
            _make_model(model_id=2, role="r1", version=2),
            _make_model(model_id=3, role="r2", version=1),
        ]
        session.execute = AsyncMock(return_value=_FakeResult(items))
        reg = ModelRegistry(session)
        out = await reg.list_models()
        assert len(out) == 3

    @pytest.mark.asyncio
    async def test_get_active_found(self) -> None:
        session = MagicMock()
        items = [_make_model(model_id=2, role="r1", version=2, is_active=True)]
        session.execute = AsyncMock(return_value=_FakeResult(items))
        reg = ModelRegistry(session)
        m = await reg.get_active("r1")
        assert m is not None
        assert m.is_active is True

    @pytest.mark.asyncio
    async def test_get_active_none(self) -> None:
        session = MagicMock()
        session.execute = AsyncMock(return_value=_FakeResult([]))
        reg = ModelRegistry(session)
        m = await reg.get_active("r1")
        assert m is None

    @pytest.mark.asyncio
    async def test_register_success(self) -> None:
        session = MagicMock()
        session.flush = AsyncMock()
        session.add = MagicMock()
        reg = ModelRegistry(session)
        m = await reg.register(
            name="m1", role="r1", version=1, model_provider="openai", model_name="gpt-4o",
        )
        assert m.role == "r1"
        assert m.model_provider == "openai"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_register_duplicate_raises(self) -> None:
        session = MagicMock()
        from sqlalchemy.exc import IntegrityError
        session.flush = AsyncMock(side_effect=IntegrityError("dup", {}, Exception()))
        session.rollback = AsyncMock()
        session.add = MagicMock()
        reg = ModelRegistry(session)
        with pytest.raises(ValueError, match="already exists"):
            await reg.register(name="m1", role="r1", version=1)
        session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_active_atomic(self) -> None:
        session = MagicMock()
        # 第一次 execute: 关旧 active；第二次: SELECT target；flush
        old_active = _make_model(model_id=1, role="r1", version=1, is_active=True)
        new_target = _make_model(model_id=2, role="r1", version=2, is_active=False)
        call_results = [
            _FakeResult([]),  # 关旧 active
            _FakeResult([new_target]),  # SELECT new
        ]
        session.execute = AsyncMock(side_effect=call_results)
        session.flush = AsyncMock()
        reg = ModelRegistry(session)
        result = await reg.set_active("r1", 2)
        assert result is new_target
        assert new_target.is_active is True
        # 2 次 execute + 1 次 flush
        assert session.execute.await_count == 2
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_active_target_not_found(self) -> None:
        session = MagicMock()
        session.execute = AsyncMock(side_effect=[
            _FakeResult([]),  # 关旧
            _FakeResult([]),  # SELECT 没找到
        ])
        reg = ModelRegistry(session)
        with pytest.raises(ValueError, match="not found"):
            await reg.set_active("r1", 99)

    @pytest.mark.asyncio
    async def test_update_config(self) -> None:
        session = MagicMock()
        target = _make_model(model_id=1, role="r1", config={"temp": 0.5})
        session.execute = AsyncMock(return_value=_FakeResult([target]))
        session.flush = AsyncMock()
        reg = ModelRegistry(session)
        m = await reg.update_config(1, {"temp": 0.7, "top_p": 0.9})
        assert m.config_json == {"temp": 0.7, "top_p": 0.9}

    @pytest.mark.asyncio
    async def test_update_weight(self) -> None:
        session = MagicMock()
        target = _make_model(model_id=1, role="r1", weight=Decimal("1.0"))
        session.execute = AsyncMock(return_value=_FakeResult([target]))
        session.flush = AsyncMock()
        reg = ModelRegistry(session)
        m = await reg.update_weight(1, 0.5)
        assert m.weight == Decimal("0.5")

    @pytest.mark.asyncio
    async def test_get_or_create_default_existing(self) -> None:
        session = MagicMock()
        existing = _make_model(model_id=1, role="r1", is_active=True)
        session.execute = AsyncMock(return_value=_FakeResult([existing]))
        reg = ModelRegistry(session)
        m = await reg.get_or_create_default(role="r1")
        assert m is existing
        # 不应调 register
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_or_create_default_creates(self) -> None:
        session = MagicMock()
        session.execute = AsyncMock(side_effect=[
            _FakeResult([]),  # get_active 返回空
        ])
        session.flush = AsyncMock()
        session.add = MagicMock()
        reg = ModelRegistry(session)
        m = await reg.get_or_create_default(
            role="r2", name="r2_default", model_provider="openai", model_name="gpt-4o",
        )
        assert m.role == "r2"
        assert m.is_active is True
        session.add.assert_called_once()
