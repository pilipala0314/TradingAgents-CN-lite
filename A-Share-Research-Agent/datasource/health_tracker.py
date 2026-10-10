"""数据源健康追踪 — 按 源/方法/日 聚合成功率，支撑「连续运行」验收。

设计目标（对齐用户验收标准「连续运行成功」）：
1. 每次 route() 调用后记录一行，含 source / method / status / latency
2. 按日聚合：每源成功率、每方法成功率、失败原因分布
3. 轻量：内存 + 可选 JSONL 落盘（复用 datasource.call_logger 的本地文件模式）
4. router 在成功/失败/质量校验三类路径以 try/except 方式写入（记录失败不影响主流程）
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


class SourceHealthTracker:
    """进程内数据源健康追踪器（线程安全）。

    Usage::

        tracker = SourceHealthTracker(log_dir=".health_logs")
        tracker.record(source="eastmoney", method="get_industry_ranking",
                       status="ok", latency_ms=120)
        report = tracker.daily_report(trade_date="2026-10-08")
    """

    def __init__(self, log_dir: str | None = None) -> None:
        self._lock = threading.Lock()
        # day -> {source: {total, ok, failed, errors:{type:count}}}
        self._by_source: dict[str, dict[str, dict[str, Any]]] = defaultdict(
            lambda: defaultdict(lambda: {"total": 0, "ok": 0, "failed": 0, "errors": defaultdict(int)})
        )
        # day -> {method: {total, ok, failed}}
        self._by_method: dict[str, dict[str, dict[str, Any]]] = defaultdict(
            lambda: defaultdict(lambda: {"total": 0, "ok": 0, "failed": 0})
        )
        self._log_dir = Path(log_dir) if log_dir else None

    def record(
        self,
        *,
        source: str,
        method: str,
        status: str,  # "ok" | "failed"
        latency_ms: float = 0.0,
        error_type: str = "",
        trade_date: str | None = None,
    ) -> None:
        day = trade_date or date.today().isoformat()
        with self._lock:
            s = self._by_source[day][source]
            m = self._by_method[day][method]
            s["total"] += 1
            m["total"] += 1
            if status == "ok":
                s["ok"] += 1
                m["ok"] += 1
            else:
                s["failed"] += 1
                m["failed"] += 1
                if error_type:
                    s["errors"][error_type] += 1
        self._maybe_flush(day, source, method, status, error_type)

    # ── 查询 ──────────────────────────────────────────────────────

    def source_success_rate(self, trade_date: str) -> dict[str, float]:
        """某日各源成功率。"""
        with self._lock:
            stats = self._by_source.get(trade_date, {})
            return {
                src: round(s["ok"] / s["total"], 3) if s["total"] else 1.0
                for src, s in stats.items()
            }

    def daily_report(self, trade_date: str) -> dict[str, Any]:
        """某日健康报告（供连续运行验收）。

        全量构造在锁内完成：并发 record() 会向 defaultdict 增键，
        锁外迭代有 "dictionary changed size during iteration" 风险。
        """
        with self._lock:
            src = self._by_source.get(trade_date, {})
            mtd = self._by_method.get(trade_date, {})
            sources = [
                {
                    "source": src_name,
                    "total": s["total"],
                    "ok": s["ok"],
                    "failed": s["failed"],
                    "success_rate": round(s["ok"] / s["total"], 3) if s["total"] else 1.0,
                    "error_breakdown": dict(s["errors"]),
                }
                for src_name, s in sorted(src.items())
            ]
            methods = [
                {
                    "method": m_name,
                    "total": m["total"],
                    "ok": m["ok"],
                    "failed": m["failed"],
                    "success_rate": round(m["ok"] / m["total"], 3) if m["total"] else 1.0,
                }
                for m_name, m in sorted(mtd.items())
            ]
        return {"trade_date": trade_date, "sources": sources, "methods": methods}

    def overall_rate(self, trade_date: str) -> float:
        """当日整体成功率。"""
        with self._lock:
            stats = self._by_source.get(trade_date, {})
            total = sum(s["total"] for s in stats.values())
            ok = sum(s["ok"] for s in stats.values())
        return round(ok / total, 3) if total else 0.0

    # ── 落盘（可选）──────────────────────────────────────────────

    def _maybe_flush(self, day: str, source: str, method: str, status: str, error_type: str) -> None:
        if self._log_dir is None:
            return
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            path = self._log_dir / f"{day}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "source": source, "method": method,
                    "status": status, "error_type": error_type,
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 落盘失败不影响主流程


# 全局单例（供 router 挂载）
_tracker: SourceHealthTracker | None = None
_tracker_lock = threading.Lock()


def get_tracker() -> SourceHealthTracker:
    """获取全局追踪器单例。"""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = SourceHealthTracker()
    return _tracker


__all__ = ["SourceHealthTracker", "get_tracker"]
