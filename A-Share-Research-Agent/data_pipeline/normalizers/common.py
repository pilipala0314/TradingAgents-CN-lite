from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def first_existing(record: dict[str, Any], *candidates: str, default: Any = None) -> Any:
    for candidate in candidates:
        if candidate in record and pd.notna(record[candidate]):
            return record[candidate]
    return default


def to_decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        if pd.isna(value):
            return default
    except TypeError as exc:
        logger.debug("pd.isna 对 %r 不可用: %s", value, exc)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        logger.debug("Decimal 转换失败, value=%r: %s", value, exc)
        return default


def to_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def to_datetime_value(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def infer_exchange(symbol: str) -> str:
    if symbol.startswith(("600", "601", "603", "605", "688", "900")):
        return "sh"
    if symbol.startswith(("000", "001", "002", "003", "300", "301", "200")):
        return "sz"
    if symbol.startswith(("430", "440", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "874", "875", "876", "877", "878", "879")):
        return "bj"
    return "unknown"
