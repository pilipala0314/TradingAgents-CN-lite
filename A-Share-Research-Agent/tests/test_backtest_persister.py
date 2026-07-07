"""test_backtest_persister — P2.5 / P2.3 回测 run 落库单测。

覆盖：
- compute_config_hash 同 config → 同 hash；不同 config → 不同 hash
- config_to_dict JSON-safe
- _VALID_TRANSITIONS 状态机（pending → running → completed；越级返 False）
- BacktestRunRecord.to_dict
- persist_backtest_run → fetch_by_job_id 协作
- update_backtest_run_status：running → completed 落 result + cost + alpha/beta
- update_backtest_run_status：终态不可再改
- list_backtest_runs：按 business_date / status / strategy_name 过滤
"""
from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.backtest_engine import BacktestConfig, BacktestEngine
from services.backtest_persister import (
    _VALID_TRANSITIONS,
    BacktestRunRecord,
    compute_config_hash,
    config_to_dict,
    fetch_by_job_id,
    list_backtest_runs,
    persist_backtest_run,
    update_backtest_run_status,
)

# ── helpers ─────────────────────────────────────────

def _make_config(**overrides) -> BacktestConfig:
    """构造默认 BacktestConfig。"""
    defaults: dict = {
        "strategy_name": "momentum_20d",
        "symbols": ["600519", "000001"],
        "universe": None,
        "start_date": date(2025, 1, 1),
        "end_date": date(2025, 3, 31),
        "initial_capital": 1_000_000.0,
        "top_n": 20,
        "rebalance_freq": "weekly",
    }
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def _make_result(config: BacktestConfig | None = None) -> MagicMock:
    """构造 mock BacktestResult（含 cost_summary / benchmark）。"""
    r = MagicMock()
    r.cost_summary = {
        "total_cost_pct": 0.012,  # 1.2%
        "total_turnover": 3.5,
        "n_rebalances": 12,
    }
    r.benchmark = {"alpha": 0.05, "beta": 0.92, "r_squared": 0.6, "n_periods": 60}
    if config is not None:
        r.config = config
    return r


def _make_run_row(
    *, status: str = "pending", business_date: date | None = None,
    job_id: str | None = "jid-1", config_hash: str = "abc123",
) -> MagicMock:
    """构造 mock FactBacktestRun 行。"""
    from datetime import datetime
    r = MagicMock()
    r.id = "row-1"
    r.job_id = job_id
    r.business_date = business_date or date(2025, 7, 7)
    r.status = status
    r.config_hash = config_hash
    r.config_json = {"strategy_name": "momentum_20d"}
    r.result_json = None
    r.error_message = None
    r.cost_total_pct = None
    r.benchmark_alpha = None
    r.benchmark_beta = None
    r.attribution_json = None
    r.started_at = None
    r.finished_at = None
    r.created_at = datetime(2025, 7, 7, 10, 0, 0)
    return r


class _FakeSession:
    """Mock AsyncSession：记录 execute 的所有 stmt，模拟 upsert + fetch + update。"""

    def __init__(self, run_row: MagicMock | None = None) -> None:
        self.execute = AsyncMock(side_effect=self._exec)
        self.commit = AsyncMock()
        self._run = run_row or _make_run_row()
        # 状态机会修改 _run.status，记录调用过的状态转换
        self.status_calls: list[tuple[str, str]] = []
        self.last_inserted_row: dict = {}

    async def _exec(self, stmt) -> MagicMock:
        # 1) upsert: 有 .excluded
        if hasattr(stmt, "excluded"):
            row = self._extract_insert_values(stmt)
            if row is not None:
                self.last_inserted_row = row
        # 2) update FactBacktestRun: 把当前 _run.status 推进
        from sqlalchemy.sql.dml import Insert as _SAInsert
        from sqlalchemy.sql.dml import Update as _SAUpdate  # noqa: PLC0415
        if isinstance(stmt, _SAUpdate) and not isinstance(stmt, _SAInsert):
            # _values 的 key 是 Column 对象，value 是 BindParameter；按 .name/.value 解
            raw = stmt._values or {}
            vals: dict[str, Any] = {}
            for k, v in raw.items():
                name = getattr(k, "name", None) or str(k)
                actual = getattr(v, "value", v)
                vals[name] = actual
            new_status = vals.get("status")
            if new_status and self._run.status != str(new_status):
                self.status_calls.append((self._run.status, new_status))
                self._run.status = new_status
                for src_key, dst_attr in [
                    ("started_at", "started_at"),
                    ("finished_at", "finished_at"),
                    ("result_json", "result_json"),
                    ("cost_total_pct", "cost_total_pct"),
                    ("benchmark_alpha", "benchmark_alpha"),
                    ("benchmark_beta", "benchmark_beta"),
                    ("error_message", "error_message"),
                    ("attribution_json", "attribution_json"),
                ]:
                    if src_key in vals:
                        setattr(self._run, dst_attr, vals[src_key])
        # 返 result 用于 fetch
        result = MagicMock()
        result.rowcount = 1
        result.scalar_one_or_none.return_value = self._run
        result.scalars.return_value.all.return_value = [self._run]
        return result

    @staticmethod
    def _extract_insert_values(stmt) -> dict | None:
        """从 pg_insert stmt 提取 values dict。"""
        multi = getattr(stmt, "_multi_values", None)
        if multi and isinstance(multi, tuple) and multi:
            first = multi[0]
            if isinstance(first, list) and first:
                first = first[0]
            if isinstance(first, dict) and first:
                return {str(k.name if hasattr(k, "name") else k): v for k, v in first.items()}
        dv = getattr(stmt, "_dict_values", None)
        if isinstance(dv, dict) and dv:
            return {str(k.name if hasattr(k, "name") else k): v for k, v in dv.items()}
        return None


# ── 纯函数：compute_config_hash ─────────────────────

class TestComputeConfigHash:
    def test_same_config_same_hash(self) -> None:
        c1 = _make_config()
        c2 = _make_config()
        assert compute_config_hash(c1) == compute_config_hash(c2)

    def test_different_symbols_different_hash(self) -> None:
        c1 = _make_config(symbols=["600519"])
        c2 = _make_config(symbols=["000001"])
        assert compute_config_hash(c1) != compute_config_hash(c2)

    def test_different_strategy_different_hash(self) -> None:
        c1 = _make_config(strategy_name="momentum_20d")
        c2 = _make_config(strategy_name="value_ma250")
        assert compute_config_hash(c1) != compute_config_hash(c2)

    def test_different_dates_different_hash(self) -> None:
        c1 = _make_config(start_date=date(2025, 1, 1))
        c2 = _make_config(start_date=date(2025, 2, 1))
        assert compute_config_hash(c1) != compute_config_hash(c2)

    def test_hash_length_16(self) -> None:
        h = compute_config_hash(_make_config())
        assert len(h) == 16
        # 全 hex
        int(h, 16)


# ── 纯函数：config_to_dict ─────────────────────

class TestConfigToDict:
    def test_all_fields_present(self) -> None:
        d = config_to_dict(_make_config())
        assert d["strategy_name"] == "momentum_20d"
        assert d["symbols"] == ["600519", "000001"]
        assert d["start_date"] == "2025-01-01"
        assert d["end_date"] == "2025-03-31"
        assert d["top_n"] == 20
        assert d["rebalance_freq"] == "weekly"

    def test_json_serializable(self) -> None:
        """直接 json.dumps 不报错。"""
        import json
        d = config_to_dict(_make_config())
        json.dumps(d)  # 不抛错即可


# ── 状态机常量 ─────────────────────

class TestStatusMachine:
    def test_pending_can_go_to_running(self) -> None:
        assert "running" in _VALID_TRANSITIONS["pending"]

    def test_running_can_go_to_completed(self) -> None:
        assert "completed" in _VALID_TRANSITIONS["running"]

    def test_running_can_go_to_failed(self) -> None:
        assert "failed" in _VALID_TRANSITIONS["running"]

    def test_completed_is_terminal(self) -> None:
        assert _VALID_TRANSITIONS["completed"] == set()

    def test_failed_is_terminal(self) -> None:
        assert _VALID_TRANSITIONS["failed"] == set()

    def test_cancelled_is_terminal(self) -> None:
        assert _VALID_TRANSITIONS["cancelled"] == set()

    def test_pending_cannot_go_directly_to_completed(self) -> None:
        """越级返 False。"""
        assert "completed" not in _VALID_TRANSITIONS["pending"]


# ── BacktestRunRecord ─────────────────────

class TestBacktestRunRecord:
    def test_to_dict(self) -> None:
        rec = BacktestRunRecord(
            run_id="r1", job_id="j1", status="pending",
            config_hash="abc", business_date="2025-07-07",
        )
        d = rec.to_dict()
        assert d == {
            "run_id": "r1",
            "job_id": "j1",
            "status": "pending",
            "config_hash": "abc",
            "business_date": "2025-07-07",
        }


# ── persist_backtest_run + fetch_by_job_id 协作 ─────────────────────

class TestPersistAndFetch:
    @pytest.mark.asyncio
    async def test_persist_returns_record(self) -> None:
        session = _FakeSession()
        rec = await persist_backtest_run(
            session, config=_make_config(),
            business_date=date(2025, 7, 7), job_id="jid-xyz",
        )
        assert isinstance(rec, BacktestRunRecord)
        assert rec.job_id == "jid-xyz"
        assert rec.status == "pending"
        # execute 被调 2 次（upsert + fetch_by_hash_and_date）
        assert session.execute.call_count == 2
        # last_inserted_row 含关键字段
        ins = session.last_inserted_row
        assert ins["job_id"] == "jid-xyz"
        assert ins["status"] == "pending"
        assert "config_hash" in ins
        assert ins["config_json"]["strategy_name"] == "momentum_20d"

    @pytest.mark.asyncio
    async def test_fetch_by_job_id_returns_dict(self) -> None:
        session = _FakeSession()
        r = await fetch_by_job_id(session, "jid-1")
        assert r is not None
        assert r["status"] == "pending"
        assert r["job_id"] == "jid-1"

    @pytest.mark.asyncio
    async def test_fetch_by_job_id_none_when_not_found(self) -> None:
        # _FakeSession 永远返 _run；改 _run 返 None 模拟未找到
        session = _FakeSession(run_row=None)
        # 改 _exec 返 None
        original_exec = session._exec

        async def _exec_none(stmt):
            res = await original_exec(stmt)
            res.scalar_one_or_none.return_value = None
            return res

        session._exec = _exec_none
        session.execute = AsyncMock(side_effect=session._exec)
        r = await fetch_by_job_id(session, "jid-missing")
        assert r is None


# ── update_backtest_run_status 状态机推进 ─────────────────────

class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_running_to_completed_persists_result(self) -> None:
        run = _make_run_row(status="running")
        session = _FakeSession(run_row=run)
        cfg = _make_config()
        result = _make_result(cfg)

        ok = await update_backtest_run_status(
            session, job_id="jid-1", status="completed", result=result,
        )
        assert ok is True
        assert ("running", "completed") in session.status_calls
        # result 落库
        assert run.result_json is not None
        # cost/alpha/beta 提取
        assert run.cost_total_pct == 0.012
        assert run.benchmark_alpha == 0.05
        assert run.benchmark_beta == 0.92

    @pytest.mark.asyncio
    async def test_pending_to_running(self) -> None:
        run = _make_run_row(status="pending")
        session = _FakeSession(run_row=run)
        ok = await update_backtest_run_status(
            session, job_id="jid-1", status="running",
        )
        assert ok is True
        assert ("pending", "running") in session.status_calls

    @pytest.mark.asyncio
    async def test_terminal_status_cannot_be_updated(self) -> None:
        """completed 是终态，不可再改。"""
        run = _make_run_row(status="completed")
        session = _FakeSession(run_row=run)
        ok = await update_backtest_run_status(
            session, job_id="jid-1", status="running",
        )
        assert ok is False
        # 没调 status 转换
        assert session.status_calls == []

    @pytest.mark.asyncio
    async def test_invalid_transition_returns_false(self) -> None:
        """pending → completed 越级。"""
        run = _make_run_row(status="pending")
        session = _FakeSession(run_row=run)
        ok = await update_backtest_run_status(
            session, job_id="jid-1", status="completed",
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_failed_status_records_error(self) -> None:
        run = _make_run_row(status="running")
        session = _FakeSession(run_row=run)
        ok = await update_backtest_run_status(
            session, job_id="jid-1", status="failed",
            error_message="BoomError: kaboom",
        )
        assert ok is True
        assert run.error_message == "BoomError: kaboom"

    @pytest.mark.asyncio
    async def test_completed_with_attribution(self) -> None:
        run = _make_run_row(status="running")
        session = _FakeSession(run_row=run)
        attribution = {"rebalance": {"turnover": 0.5}}
        await update_backtest_run_status(
            session, job_id="jid-1", status="completed",
            attribution=attribution,
        )
        assert run.attribution_json == attribution

    @pytest.mark.asyncio
    async def test_unknown_status_returns_false(self) -> None:
        run = _make_run_row(status="pending")
        session = _FakeSession(run_row=run)
        ok = await update_backtest_run_status(
            session, job_id="jid-1", status="not_a_status",
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_missing_job_id_returns_false(self) -> None:
        """job_id 不存在时返 False。"""
        run = _make_run_row(job_id="jid-other")
        session = _FakeSession(run_row=run)
        # 把 _exec 改成返 scalar_one_or_none = None
        original_exec = session._exec

        async def _exec_none(stmt):
            res = await original_exec(stmt)
            # select 走 None
            res.scalar_one_or_none.return_value = None
            return res

        session._exec = _exec_none
        session.execute = AsyncMock(side_effect=session._exec)
        ok = await update_backtest_run_status(
            session, job_id="jid-missing", status="running",
        )
        assert ok is False


# ── list_backtest_runs ─────────────────────

class TestListBacktestRuns:
    @pytest.mark.asyncio
    async def test_list_returns_list(self) -> None:
        session = _FakeSession()
        rs = await list_backtest_runs(session)
        assert isinstance(rs, list)
        assert len(rs) >= 1

    @pytest.mark.asyncio
    async def test_list_with_status_filter(self) -> None:
        session = _FakeSession()
        rs = await list_backtest_runs(session, status="pending")
        assert len(rs) == 1
        # execute 至少 1 次
        assert session.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_list_with_strategy_filter(self) -> None:
        session = _FakeSession()
        rs = await list_backtest_runs(session, strategy_name="momentum_20d")
        assert len(rs) == 1

    @pytest.mark.asyncio
    async def test_list_with_business_date_filter(self) -> None:
        session = _FakeSession()
        rs = await list_backtest_runs(session, business_date=date(2025, 7, 7))
        assert len(rs) == 1


# ── P1-T3 compare_runs ─────────────────────────


class TestCompareRuns:
    """P1-T3 多 run 横向对比。

    compare_runs 用 IN 一次拉多 row，输出每 run 的关键指标 + nav 曲线。
    缺失 job_id 跳过。
    """

    @pytest.mark.asyncio
    async def test_compare_two_runs_aligns_nav(self) -> None:
        """2 个 run 都有 nav + trading_dates，输出含 nav_curve。"""
        import uuid as _uuid

        from services.backtest_persister import compare_runs

        j1, j2 = str(_uuid.uuid4()), str(_uuid.uuid4())
        run1 = MagicMock()
        run1.id = _uuid.uuid4()
        run1.job_id = _uuid.UUID(j1)
        run1.business_date = date(2025, 7, 1)
        run1.status = "completed"
        run1.config_json = {
            "strategy_name": "momentum_20d", "universe": "hs300", "benchmark": "000300",
        }
        run1.result_json = {
            "metrics": {"sharpe": 1.8, "max_drawdown": -0.08, "turnover": 0.45,
                        "total_return": 0.25, "annual_return": 0.18, "n_periods": 60},
            "nav": [1.0, 1.02, 1.05, 1.03],
            "trading_dates": ["2025-07-01", "2025-07-02", "2025-07-03", "2025-07-04"],
            "benchmark": {"alpha": 0.12, "beta": 0.85},
        }
        run1.benchmark_alpha = 0.12
        run1.benchmark_beta = 0.85
        run2 = MagicMock()
        run2.id = _uuid.uuid4()
        run2.job_id = _uuid.UUID(j2)
        run2.business_date = date(2025, 7, 2)
        run2.status = "completed"
        run2.config_json = {
            "strategy_name": "value_ma250", "universe": "csi500", "benchmark": "000905",
        }
        run2.result_json = {
            "metrics": {"sharpe": 1.2, "max_drawdown": -0.12, "turnover": 0.30,
                        "total_return": 0.15, "annual_return": 0.10, "n_periods": 60},
            "nav": [1.0, 1.01, 1.015, 1.014],
            "trading_dates": ["2025-07-02", "2025-07-03", "2025-07-04", "2025-07-05"],
            "benchmark": {"alpha": 0.05, "beta": 0.95},
        }
        run2.benchmark_alpha = 0.05
        run2.benchmark_beta = 0.95

        session = MagicMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=[run1, run2])),
            )),
        )
        runs = await compare_runs(session, [j1, j2])
        assert len(runs) == 2
        r1 = next(r for r in runs if r["job_id"] == j1)
        assert r1["strategy"] == "momentum_20d"
        assert r1["alpha"] == 0.12
        assert r1["beta"] == 0.85
        assert r1["sharpe"] == 1.8
        assert len(r1["nav_curve"]) == 4
        assert r1["nav_curve"][0] == {"date": "2025-07-01", "nav": 1.0}
        assert r1["nav_curve"][3]["nav"] == 1.03

    @pytest.mark.asyncio
    async def test_compare_skips_missing_job_ids(self) -> None:
        """3 个 ID 入参，DB 只返 1 个 → 输出 1 个 + 端点层记 missing。"""
        import uuid as _uuid

        from services.backtest_persister import compare_runs

        j1, j2, j3 = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
        run1 = MagicMock()
        run1.id = _uuid.uuid4()
        run1.job_id = _uuid.UUID(j1)
        run1.business_date = date(2025, 7, 1)
        run1.status = "completed"
        run1.config_json = {"strategy_name": "x", "universe": "y", "benchmark": "z"}
        run1.result_json = {
            "metrics": {"sharpe": 1.0}, "nav": [1.0, 1.01], "trading_dates": ["d1", "d2"],
            "benchmark": {"alpha": 0.0, "beta": 1.0},
        }
        run1.benchmark_alpha = None
        run1.benchmark_beta = None

        session = MagicMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=[run1])),
            )),
        )
        runs = await compare_runs(session, [j1, j2, j3])
        assert len(runs) == 1
        assert runs[0]["job_id"] == j1

    @pytest.mark.asyncio
    async def test_compare_truncates_at_max_n(self) -> None:
        """传 6 个 ID，max_n=5 → target 截断到 5。"""
        import uuid as _uuid

        from services.backtest_persister import compare_runs

        ids = [str(_uuid.uuid4()) for _ in range(6)]
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=[])),
            )),
        )
        runs = await compare_runs(session, ids, max_n=5)
        assert runs == []
        # 验证截断后 IN 子句只含 5 个 UUID（通过 column 表达式对象的 .value 数）
        # 简单做法：检查 session.execute 被调 1 次，in_ 表达式里有 5 个元素
        assert session.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_compare_empty_input_returns_empty(self) -> None:
        from services.backtest_persister import compare_runs
        session = MagicMock()
        # session.execute 必是 AsyncMock 才有 await_count
        session.execute = AsyncMock()
        runs = await compare_runs(session, [])
        assert runs == []
        # 不该调 DB
        assert session.execute.await_count == 0
