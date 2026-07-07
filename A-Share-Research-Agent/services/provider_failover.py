"""Provider Failover Service — 数据源自动降级 / 恢复（P0-T8）。

目的：
- 监控 DataRouter._stats 的 method:provider 健康度
- 当 primary provider 在最近窗口内 success_rate < threshold 且 total >= min_total
  → 自动将 chain 调整为 [backup, primary, ...]
- 当 backup provider 表现稳定 → 自动恢复
- 维护状态机 + append-only 历史，前端可可视化

设计原则：
- 单实例 + threading.Lock（与 scheduler_status_service 一致）
- 不修改 DataRouter._method_table（永久存储），只通过临时重排 chain 实现
- 状态检查是可观察、可重入的纯函数（输入 router，输出 FailoverState 列表）
- 不依赖异步（check_and_failover 是同步的，方便后台定时任务 / 端点触发）

关键不变量：
- 同一 method 同一时刻只能有 1 个 active failover（primary → backup 唯一）
- 历史 entries append-only，capacity=200（FIFO trim）
- FailoverState.active=True 时，下一次 _dispatch_to_chain 会感知并重排 chain
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── 触发阈值（可在 check_and_failover 调用时覆盖）────────
DEFAULT_FAILOVER_THRESHOLD = 0.5  # primary success_rate < 0.5 触发降级
DEFAULT_RECOVERY_THRESHOLD = 0.85  # backup success_rate >= 0.85 触发恢复
DEFAULT_MIN_TOTAL = 10  # 累计调用次数 < 10 不评估
MIN_PROVIDERS_FOR_FAILOVER = 2  # 链上 < 2 provider 无法降级
HISTORY_MAX = 200

# ── 状态机（线程安全）───────────────────────────────
_lock = threading.Lock()
_state: dict[str, FailoverState] = {}  # key = method
_history: list[dict[str, Any]] = []  # append-only


@dataclass
class FailoverState:
    """单 method 的 failover 状态。

    Attributes:
        method          路由方法名
        primary         主 provider（当前被降级的）
        backup          备用 provider
        active          True = 已降级（chain 已被重排为 [backup, primary]）
        since_ts        状态变更时间戳（time.time() 秒）
        reason          触发 / 恢复原因（最近一次变更）
        primary_stats   触发时 primary 的 stats 快照（诊断用）
        backup_stats    触发时 backup 的 stats 快照
    """

    method: str
    primary: str
    backup: str
    active: bool
    since_ts: float
    reason: str
    primary_stats: dict[str, Any] = field(default_factory=dict)
    backup_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "primary": self.primary,
            "backup": self.backup,
            "active": self.active,
            "since_ts": self.since_ts,
            "reason": self.reason,
            "primary_stats": self.primary_stats,
            "backup_stats": self.backup_stats,
        }


# ── 内部 helper ──────────────────────────────────


def _stats_to_dict(stat: Any) -> dict[str, Any]:
    """把 EndpointStats 序列化为 dict（容错：非 dataclass 走 getattr）。"""
    if stat is None:
        return {}
    if hasattr(stat, "to_dict") and callable(stat.to_dict):
        try:
            return stat.to_dict()
        except Exception as exc:
            logger.debug("[provider_failover] stats.to_dict 失败, fallback: %s", exc)
    return {
        "total": getattr(stat, "total", 0),
        "success": getattr(stat, "success", 0),
        "failure": getattr(stat, "failure", 0),
        "success_rate": round(getattr(stat, "success", 0) / max(1, getattr(stat, "total", 0)), 3),
        "p95_latency_ms": getattr(stat, "p95_latency_ms", 0.0),
        "last_error": (getattr(stat, "last_error", "") or "")[:160],
        "last_status": getattr(stat, "last_status", "idle"),
    }


def _build_history_entry(
    *,
    method: str,
    primary: str,
    backup: str,
    action: str,  # "failover" | "recover" | "force_recover"
    reason: str,
    primary_stats: dict[str, Any] | None = None,
    backup_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ts": time.time(),
        "method": method,
        "primary": primary,
        "backup": backup,
        "action": action,
        "reason": reason,
        "primary_stats": primary_stats or {},
        "backup_stats": backup_stats or {},
    }


def _append_history(entry: dict[str, Any]) -> None:
    """append-only，FIFO trim 防止内存爆炸。"""
    _history.append(entry)
    if len(_history) > HISTORY_MAX:
        # 保留后 HISTORY_MAX 条
        del _history[: len(_history) - HISTORY_MAX]


# ── 公开 API ─────────────────────────────────────


def check_and_failover(
    router: Any,
    *,
    failover_threshold: float = DEFAULT_FAILOVER_THRESHOLD,
    recovery_threshold: float = DEFAULT_RECOVERY_THRESHOLD,
    min_total: int = DEFAULT_MIN_TOTAL,
) -> list[dict[str, Any]]:
    """扫描 router 全部 (method, provider) 状态，按规则触发降级 / 恢复。

    Args:
        router                DataRouter 实例（需要 ._stats / ._method_table）
        failover_threshold    primary success_rate < threshold → 降级
        recovery_threshold    backup success_rate >= threshold → 恢复
        min_total             单端点累计 < min_total 不评估（避免冷启动误判）

    Returns:
        本次变更的 history entries 列表（用于日志 / 调试）
    """
    method_table = getattr(router, "_method_table", {}) or {}
    stats = getattr(router, "_stats", {}) or {}
    changes: list[dict[str, Any]] = []

    now = time.time()
    for method, provs in method_table.items():
        prov_names = list(provs.keys())
        if len(prov_names) < MIN_PROVIDERS_FOR_FAILOVER:
            # 单 provider 链 → 无法 failover
            continue

        primary, backup = prov_names[0], prov_names[1]
        primary_stat = stats.get(f"{method}:{primary}")
        backup_stat = stats.get(f"{method}:{backup}")
        primary_total = getattr(primary_stat, "total", 0) or 0
        backup_total = getattr(backup_stat, "total", 0) or 0

        with _lock:
            current = _state.get(method)
            current_active = current is not None and current.active

            if not current_active:
                # ── 检查是否需要降级 ─────────────────
                if primary_total < min_total:
                    continue
                primary_rate = (
                    getattr(primary_stat, "success", 0) / primary_total
                    if primary_total
                    else 0.0
                )
                if primary_rate < failover_threshold:
                    entry = _build_history_entry(
                        method=method,
                        primary=primary,
                        backup=backup,
                        action="failover",
                        reason=(
                            f"primary success_rate={primary_rate:.3f} "
                            f"< {failover_threshold} (total={primary_total})"
                        ),
                        primary_stats=_stats_to_dict(primary_stat),
                        backup_stats=_stats_to_dict(backup_stat),
                    )
                    _state[method] = FailoverState(
                        method=method,
                        primary=primary,
                        backup=backup,
                        active=True,
                        since_ts=now,
                        reason=entry["reason"],
                        primary_stats=entry["primary_stats"],
                        backup_stats=entry["backup_stats"],
                    )
                    _append_history(entry)
                    changes.append(entry)
                    logger.warning(
                        "[provider_failover] 触发降级: method=%s primary=%s → backup=%s reason=%s",
                        method, primary, backup, entry["reason"],
                    )
            else:
                # ── 已降级，检查是否可恢复 ─────────────
                if backup_total < min_total:
                    continue
                backup_rate = (
                    getattr(backup_stat, "success", 0) / backup_total
                    if backup_total
                    else 0.0
                )
                # primary 也至少要回血到一定水位才考虑恢复
                primary_rate_now = (
                    getattr(primary_stat, "success", 0) / primary_total
                    if primary_total
                    else 0.0
                )
                if backup_rate >= recovery_threshold and primary_rate_now >= recovery_threshold:
                    entry = _build_history_entry(
                        method=method,
                        primary=current.primary if current else primary,
                        backup=current.backup if current else backup,
                        action="recover",
                        reason=(
                            f"primary 恢复: rate={primary_rate_now:.3f}, "
                            f"backup 稳定: rate={backup_rate:.3f} >= {recovery_threshold}"
                        ),
                        primary_stats=_stats_to_dict(primary_stat),
                        backup_stats=_stats_to_dict(backup_stat),
                    )
                    # 保留原 state 记录但 active=False（用于审计）
                    if current is not None:
                        current.active = False
                        current.since_ts = now
                        current.reason = entry["reason"]
                        current.primary_stats = entry["primary_stats"]
                        current.backup_stats = entry["backup_stats"]
                    _append_history(entry)
                    changes.append(entry)
                    logger.info(
                        "[provider_failover] 自动恢复: method=%s primary=%s reason=%s",
                        method, current.primary if current else primary, entry["reason"],
                    )
    return changes


def get_state(method: str) -> FailoverState | None:
    with _lock:
        return _state.get(method)


def get_active_failovers() -> list[FailoverState]:
    with _lock:
        return [s for s in _state.values() if s.active]


def get_all_states() -> list[FailoverState]:
    with _lock:
        return list(_state.values())


def get_history(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        n = max(0, min(limit, len(_history)))
        return list(_history[-n:])


def force_recover(method: str, *, reason: str = "manual") -> bool:
    """强制退出降级（运维手动操作）。返回是否真的有状态被改。"""
    with _lock:
        current = _state.get(method)
        if current is None or not current.active:
            return False
        entry = _build_history_entry(
            method=method,
            primary=current.primary,
            backup=current.backup,
            action="force_recover",
            reason=reason,
            primary_stats=current.primary_stats,
            backup_stats=current.backup_stats,
        )
        current.active = False
        current.since_ts = time.time()
        current.reason = reason
        _append_history(entry)
        logger.info(
            "[provider_failover] 强制恢复: method=%s reason=%s", method, reason,
        )
        return True


def get_failover_overview() -> dict[str, Any]:
    """前端 health tab ③ section 消费的概览。"""
    with _lock:
        all_states = list(_state.values())
    active = [s for s in all_states if s.active]
    return {
        "active_count": len(active),
        "tracked_methods": len(all_states),
        "active": [s.to_dict() for s in active],
        "all": [s.to_dict() for s in all_states],
    }


def reset() -> None:
    """测试用：清空 state + history。"""
    with _lock:
        _state.clear()
        _history.clear()


def apply_failover_to_chain(method: str, chain: list[str]) -> list[str]:
    """根据当前 failover state 重排 chain（不动 _method_table）。

    - 若 method 无 active failover → 返回原 chain
    - 若有 active failover → [backup, primary, ...其余] 顺序
    - chain 中若不含 primary / backup（异常情况）→ 不动
    """
    state = get_state(method)
    if state is None or not state.active:
        return chain
    if state.backup not in chain or state.primary not in chain:
        return chain
    if chain[0] == state.backup:
        # 已经是重排后的 chain（route 重入场景）
        return chain
    others = [p for p in chain if p != state.primary and p != state.backup]
    new_chain = [state.backup, state.primary, *others]
    logger.debug(
        "[provider_failover] chain 重排: method=%s %s → %s",
        method, chain, new_chain,
    )
    return new_chain
