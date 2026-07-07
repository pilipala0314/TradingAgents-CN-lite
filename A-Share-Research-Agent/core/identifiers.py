"""项目级业务常量。统一市场/板块/个股等抽象实体的 UUID，避免散落在各处。"""
from __future__ import annotations

import uuid

from core.enums import EntityType

# 整个项目里「全市场（A 股大盘）」层面的伪实体 ID，ETL、规则命中、上下文装载必须共用。
# 用确定性 UUID5 比 UUID(int=0) 更稳定，也避免和真实 security_id 冲突。
_MARKET_NAMESPACE = uuid.UUID("2fd2235d-6f60-4445-9d50-94267374b6d8")
MARKET_ENTITY_ID: uuid.UUID = uuid.uuid5(_MARKET_NAMESPACE, f"{EntityType.MARKET.value}:cn_a_share")


def entity_uuid(scope: str, key: str) -> uuid.UUID:
    """统一的 UUID5 派生入口，所有需要占位 entity_id 的地方必须走这里。"""
    return uuid.uuid5(_MARKET_NAMESPACE, f"{scope}:{key}")


__all__ = ["MARKET_ENTITY_ID", "entity_uuid"]
