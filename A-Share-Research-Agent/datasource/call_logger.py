"""datasource.call_logger — API 调用记录器（轻量本地文件写入，零外部依赖）。

两条写入路径：
- `enqueue_call_log()`：路由层 fire-and-forget 用，入队后由后台 writer 批量落盘
  （攒批 100 条或 1 秒，一次 IO 追加写入）；
- `log_api_call()`：需要同步语义的调用方直接等待写入完成。

落盘格式：`<cache_dir>/call_logs/YYYYMMDD.jsonl`，每行一条 JSON。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 批量写入队列 ────────────────────────────────────────────────────
_BATCH_MAX = 100
_BATCH_INTERVAL_SEC = 1.0
# 队列容量上限（背压保护：磁盘故障/高频调用时丢弃新记录而非无界增长）
_MAX_PENDING = 10_000
_pending: list[dict[str, Any]] = []
_dropped_count = 0
_writer_task: asyncio.Task | None = None

# 日志目录：默认 <datasource 包>/../.call_logs，可用 DATASOURCE_CALL_LOG_DIR 覆盖
_DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / ".call_logs"
_LOG_DIR = Path(os.getenv("DATASOURCE_CALL_LOG_DIR", str(_DEFAULT_LOG_DIR)))


def enqueue_call_log(
    *,
    source_key: str,
    method_name: str,
    caller: str = "system",
    params: dict[str, Any] | None = None,
    response_preview: Any = None,
    status_code: int = 200,
    latency_ms: float = 0.0,
    cache_hit: bool = False,
    cache_key: str | None = None,
    error_message: str | None = None,
) -> None:
    """非阻塞入队，由后台 writer 批量落盘。队列满时丢弃新记录并告警。"""
    global _writer_task, _dropped_count
    if len(_pending) >= _MAX_PENDING:
        _dropped_count += 1
        if _dropped_count == 1 or _dropped_count % 1000 == 0:
            logger.warning("调用记录队列已满(%d)，累计丢弃 %d 条", _MAX_PENDING, _dropped_count)
        return
    _pending.append({
        "source_key": source_key, "method_name": method_name, "caller": caller,
        "params": params, "response_preview": response_preview,
        "status_code": status_code, "latency_ms": latency_ms,
        "cache_hit": cache_hit, "cache_key": cache_key,
        "error_message": error_message,
    })
    if _writer_task is None or _writer_task.done():
        try:
            _writer_task = asyncio.create_task(_writer_loop())
        except RuntimeError:
            _writer_task = None  # 无事件循环：放弃写入（不应发生）


async def _writer_loop() -> None:
    """后台批量写入循环：攒批或定时刷新。"""
    while True:
        await asyncio.sleep(_BATCH_INTERVAL_SEC)
        if _pending:
            await _flush_batch()


async def flush_call_logs() -> None:
    """立即刷新未写入的队列（优雅退出时调用）。"""
    if _pending:
        await _flush_batch()


async def _flush_batch() -> None:
    """批量落盘（最多 _BATCH_MAX 条）。"""
    records = _pending[:_BATCH_MAX]
    del _pending[:_BATCH_MAX]
    try:
        await _write_records(records)
    except Exception as exc:
        # 记录已从队列移除，此处丢失是既成事实：用 warning 留痕便于监控发现
        logger.warning("调用记录批量写入失败，本批 %d 条丢失(不影响主流程): %s", len(records), exc)


async def log_api_call(
    *,
    source_key: str,
    method_name: str,
    caller: str = "system",
    params: dict[str, Any] | None = None,
    response_preview: Any = None,
    status_code: int = 200,
    latency_ms: float = 0.0,
    cache_hit: bool = False,
    cache_key: str | None = None,
    error_message: str | None = None,
) -> None:
    """异步写入 API 调用记录到本地文件（直接等待，同步语义）。

    失败仅 warning 不抛异常。高频系统调用请用 enqueue_call_log() 走批量路径。
    """
    record = {
        "source_key": source_key, "method_name": method_name, "caller": caller,
        "params": params, "response_preview": response_preview,
        "status_code": status_code, "latency_ms": latency_ms,
        "cache_hit": cache_hit, "cache_key": cache_key,
        "error_message": error_message,
    }
    try:
        await _write_records([record])
    except Exception as exc:
        logger.debug("调用记录写入失败(不影响主流程): %s", exc)


async def _write_records(records: list[dict[str, Any]]) -> None:
    """批量追加写入本地 JSONL 文件（一次 IO）。"""
    if not records:
        return
    log_dir = _LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("调用记录目录创建失败: %s", exc)
        return
    path = log_dir / f"{date.today().isoformat()}.jsonl"
    lines = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in records:
        entry = {
            "log_id": str(uuid.uuid4()),
            "called_at": now,
            "source_key": r["source_key"],
            "method_name": r["method_name"],
            "caller": r["caller"],
            "params": _json_safe(r["params"]),
            "response_preview": _build_preview(r["response_preview"]),
            "status_code": r["status_code"],
            "latency_ms": r["latency_ms"],
            "cache_hit": r["cache_hit"],
            "cache_key": r["cache_key"],
            "error_message": r["error_message"],
        }
        lines.append(json.dumps(entry, ensure_ascii=False, default=str))
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as exc:
        logger.warning("调用记录写入失败: %s", exc)
        raise


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    try:
        import pandas as pd

        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if value is pd.NaT:
            return None
    except Exception:
        pass
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return _json_safe(value.item())
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _build_preview(response: Any) -> dict[str, Any] | None:
    """构建出参摘要（不存全量，只存前几条/前500字符）。"""
    if response is None:
        return None
    try:
        import pandas as pd
        if isinstance(response, pd.DataFrame):
            return {
                "type": "DataFrame",
                "rows": len(response),
                "columns": list(response.columns)[:10],
                "sample": _json_safe(response.head(3).to_dict(orient="records")),
            }
        if isinstance(response, list):
            return {
                "type": "list",
                "count": len(response),
                "sample": _json_safe(response[:3]),
            }
        if isinstance(response, dict):
            return {
                "type": "dict",
                "keys": list(response.keys())[:10],
                "preview": _json_safe({k: str(v)[:100] for k, v in list(response.items())[:5]}),
            }
        return {"type": type(response).__name__, "preview": _json_safe(str(response)[:500])}
    except Exception:
        return {"type": "unknown"}
