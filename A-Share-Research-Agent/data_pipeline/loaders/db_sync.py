import logging
from typing import Any

import pandas as pd
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def upsert_dataframe(
    session: AsyncSession,
    df: pd.DataFrame,
    model: type[Any],
    index_elements: list[str],
) -> None:
    """
    将 Pandas DataFrame 批量 Upsert (插入或更新) 到 PostgreSQL 数据库。
    适用于同步 AkShare 数据和持久化各类特征快照。

    :param session: SQLAlchemy 异步 Session
    :param df: 要入库的 DataFrame
    :param model: 目标 SQLAlchemy ORM 模型
    :param index_elements: 用于判定冲突的唯一索引/主键列表 (ON CONFLICT)
    """
    if df.empty:
        logger.warning(f"DataFrame 为空，跳过入库操作: 表名 {model.__tablename__}")
        return

    model_columns = {column.name for column in model.__table__.columns}
    unknown_columns = sorted(set(df.columns) - model_columns)
    if unknown_columns:
        raise ValueError(
            f"待入库 DataFrame 含模型未定义字段，表={model.__tablename__}, "
            f"unknown_columns={unknown_columns}"
        )

    records = df.where(pd.notnull(df), None).to_dict(orient="records")
    stmt = insert(model).values(records)

    update_dict = {
        c.name: c
        for c in stmt.excluded
        if c.name not in index_elements and c.name != "created_at"
    }

    if update_dict:
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements,
            set_=update_dict,
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=index_elements)

    try:
        await session.execute(stmt)
        await session.commit()
        logger.info(f"成功 Upsert {len(records)} 条记录到表 {model.__tablename__}")
    except Exception as e:
        await session.rollback()
        logger.error(f"Upsert 到 {model.__tablename__} 时发生异常: {e}", exc_info=True)
        raise
