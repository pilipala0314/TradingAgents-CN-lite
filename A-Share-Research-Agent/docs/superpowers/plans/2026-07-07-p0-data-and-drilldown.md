# A-Share-Research-Agent · P0 数据补齐 + 决策钻取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 P0 7 项任务全部完成 — 多市场真实数据 / 行业指数 K 线 / 宏观数据 / cron 监控 / 自动降级 / 决策钻取 / 板块热力图，**前后端都做**，保持 hero/eyebrow/section 风格。

**Architecture:**
- 后端：扩展 `data_pipeline/providers/` 真实端点 → 走 `DataRouter` → 写新 ORM 模型 → 暴露新 API
- 前端：在 11 个 tab 内新增 sections 或新建 tab（macro 新增 tab；sector 在 signals tab 内升级）
- 数据：APScheduler cron + 失败重试 + dashboard 可视化
- 风格：每页 hero + eyebrow + section 编号 + collapsible

**Tech Stack:** FastAPI + SQLAlchemy 2 async + asyncpg + APScheduler + Chart.js 4.4 + Chart.js matrix plugin + Python 3.10+

---

## Global Constraints

- **设计一致性**：所有新 tab/section 必须用 `.signal-hero` / `.signal-eyebrow` / `.signal-section-head` 组件类
- **数据诚实性**：禁止 Mock / 静默 fallback；provider 失败 → `DataFetchError` + 语义化 logger.error
- **后端解耦**：新数据走 `DataRouter` 抽象，不直接调 provider
- **租户**：所有新数据自动带 `tenant_id`（用现有 `tenant.py`）
- **测试**：每个新模块必须有 `tests/test_*.py`，先 TDD
- **审计**：禁止 `try/except: pass` / `if False` 调试残留
- **每次 commit 前**：跑 `pytest tests/test_*.py` 全绿 + `ruff check --fix` clean

---

## Task 1: 港股 K 线真实数据入库

**Files:**
- Create: `data_pipeline/providers/hk_full_provider.py`（已有 stub，需补真实 K 线逻辑）
- Create: `data_pipeline/etl/hk_kline_etl.py`
- Create: `tests/test_hk_kline_etl.py`
- Modify: `apps/worker/tasks.py`（注册 `run_hk_close_etl`）

**接口：** `await data_router.route("get_hk_kline", "0700.HK", datalen=120)`

**Tests:**
- [ ] **Step 1: 写失败测试** — `tests/test_hk_kline_etl.py` 验证 ETL 把 `0700.HK` 30 日 K 写入 `fact_market_bar`，且字段含 `symbol='0700.HK'`
- [ ] **Step 2: 跑测试** — 失败（HK ETL 未实现）
- [ ] **Step 3: 实现 ETL** — 调 `ak.stock_hk_hist(symbol='0700', period='daily', adjust='qfq')`，normalize → `FactMarketBar`
- [ ] **Step 4: 跑测试** — 通过
- [ ] **Step 5: Commit** — `feat(etl): add HK stock daily K-line ingestion`

---

## Task 2: 美股 + 期货真实数据入库

**Files:**
- Create: `data_pipeline/etl/us_kline_etl.py`
- Create: `data_pipeline/etl/futures_kline_etl.py`
- Create: `tests/test_us_kline_etl.py`
- Create: `tests/test_futures_kline_etl.py`

**接口：** `get_us_kline("AAPL", 30)` / `get_futures_kline("AU0", 30)`

**Tests:**
- [ ] **Step 1-5**: 同 Task 1 模式，验证 `AAPL` / `AU0`（沪金主力）入库
- [ ] **数据源**：`ak.stock_us_hist(symbol='AAPL')` / `ak.futures_main_sina(symbol='AU0')`
- [ ] **Commit**: `feat(etl): add US stock + futures K-line ingestion`

---

## Task 3: 全球指数 tab 数据接入

**Files:**
- Modify: `apps/api/routers/dashboard.py` — 扩展 `overview` endpoint 返回 `global_indices` 数组
- Modify: `apps/api/static/index.html` — overview tab 加 `③ 全球指数（港股/美股/期货/指数）` section
- Create: `tests/test_dashboard.py::test_overview_includes_global_indices`

**前端结构：**
- 头图：MARKET OVERVIEW（沿用现有 overview-hero）
- ① 系统健康度（现有）
- ② 全球指数（升级）— 4 张子表：港股 / 美股 / 期货 / 主流指数
- 每个子表：代码 / 名称 / 最新价 / 涨跌额 / 涨跌幅 / 成交额

**Tests:**
- [ ] **Step 1: 写测试** — `/api/dashboard/overview` 返回 `global_indices` 含 4 个子集
- [ ] **Step 2-3**: 实现
- [ ] **Step 4: 跑** — 通过
- [ ] **Step 5: Commit**

---

## Task 4: 行业指数 K 线（申万一级 31 个）

**Files:**
- Create: `data_pipeline/providers/sw_industry_provider.py` — `ak.stock_board_industry_index_em(symbol='申万一级'列表)`
- Create: `data_pipeline/etl/sw_industry_etl.py` — 全 31 个行业日 K 入 `fact_market_bar`，`security_type='industry'`
- Create: `tests/test_sw_industry_etl.py`
- Modify: `data_pipeline/etl/pipeline.py` — `run_close_pipeline` 加 industry 步骤

**接口：** `get_sw_industry_kline("801010", 60)` — 农林牧渔 60 日 K

**Tests:**
- [ ] **Step 1: 写测试** — 31 个行业 K 全部入库，security_type='industry'
- [ ] **Step 2-3**: 实现
- [ ] **Step 4-5**: 通过 + commit

---

## Task 5: 宏观数据接入（CPI/PPI/PMI/M2/国债）

**Files:**
- Create: `data_pipeline/providers/macro_provider.py` — `ak.macro_china_cpi_yearly()` 等
- Create: `data_pipeline/etl/macro_etl.py`
- Create: `models/macro.py` — `FactMacroIndicator` ORM
- Create: `alembic/versions/20260708_0005_fact_macro_indicator.py` — 新表迁移
- Create: `tests/test_macro_etl.py`
- Modify: `services/macro_service.py`（已有 stub，扩展读 ORM）

**ORM Schema:**
```python
class FactMacroIndicator(Base):
    __tablename__ = "fact_macro_indicator"
    id = BigInteger PK
    indicator_code = String(32)  # "CPI_YOY" / "PMI_MFG" / "M2_YOY" / "CN10Y_YIELD"
    period = String(16)  # "2025-12"
    value = Numeric(18, 4)
    published_at = DateTime
    source = String(32)  # "eastmoney" / "akshare"
    tenant_id = String(64)
```

**Tests:**
- [ ] **Step 1-5**: TDD 验证 5 个指标入库，DTO 暴露 `/api/macro/{code}?periods=12`

---

## Task 6: 宏观数据 Macro Tab（前端）

**Files:**
- Modify: `index.html` — 新增 `<div class="tab-panel" data-tab="macro">` + sidebar 加 nav
- Modify: `style.css` — `.macro-hero` 渐变（紫色调）
- Create: `apps/api/routers/macro.py` — `GET /api/macro/{code}?periods=12`（如已存在则扩展）
- Modify: `dashboard.js` — 加 `loadMacro()` 函数 + 切 tab 触发

**结构：**
- 头图：MACRO INDICATORS（紫色 hero）
- ① 通胀 — CPI / PPI 双卡 + 12 期趋势图
- ② 景气 — PMI 制造业 + 非制造业 双卡 + 趋势图
- ③ 货币 — M2 同比 + M1 同比 双卡
- ④ 利率 — 10 年期国债 + 1 年期国债

**LLM 增强**（关键差异）：每节下面用 LLM 生成"宏观解读"，如：
- "PMI 连续 3 月 > 50，制造业扩张加快；M2 增速 8.5% 创年内新高，建议 risk-on 偏积极仓位"
- 输入：5 个指标 + LLM 调用 `core/llm/client.py`

**Tests:**
- [ ] **Step 1: 测试后端** — `/api/macro/CPI_YOY` 返回 12 期
- [ ] **Step 2-3**: 前后端
- [ ] **Step 4: 测试 LLM 解读** — `services/macro_service.py::get_macro_commentary(code)` 返回非空
- [ ] **Step 5: Commit**

---

## Task 7: 每日信号 cron 监控面板

**Files:**
- Modify: `apps/api/routers/admin.py` — `GET /api/admin/scheduler/status` 返回 jobs + 下次执行时间
- Create: `tests/test_admin.py::test_scheduler_status_returns_etl_and_research_jobs`
- Modify: `index.html` — 在 signals tab hero 加 scheduler 状态卡
- Modify: `dashboard.js` — `loadSchedulerStatus()` 轮询 30s

**结构（signals tab hero 区）：**
```
┌──────────────────────────────────────────────┐
│ 今日信号 · 2026-07-01      [刷新][展开][异常] │
│ 6 节点研究链路 · ...                          │
├──────────────────────────────────────────────┤
│ 🕐 每日信号 cron:                            │
│   ✓ ETL 17:00 → 上次成功 (2 天前)            │
│   ✓ Research 17:30 → 上次成功 (2 天前)       │
│   ⏱ 下次执行: 今天 17:00                      │
└──────────────────────────────────────────────┘
```

**Tests:**
- [ ] **Step 1-5**: TDD 验证 scheduler status 接口 + 前端显示

---

## Task 8: 数据健康自动降级

**Files:**
- Create: `services/provider_failover.py` — `check_and_failover()` 每 60s 检查
- Modify: `data_pipeline/data_router.py` — 集成 failover 逻辑
- Create: `tests/test_provider_failover.py`
- Modify: `apps/api/routers/health.py` — `GET /api/health/failover/history`

**降级逻辑：**
```python
async def check_and_failover():
    for provider in providers:
        # 看最近 5 min 失败率
        stats = health.get_provider_recent(provider, window_sec=300)
        if stats.success_rate < 0.5 and stats.total >= 10:
            # 切换 primary → backup
            await data_router.set_backup_active(provider)
            logger.error("FAILOVER: provider=%s 失败率 %.2f，切换", provider, stats.success_rate)
            # 写告警
            await alert_engine.fire("provider_failover", provider=provider, ...)
```

**Tests:**
- [ ] **Step 1: 测试 failover 触发** — 注入 6 次失败 → 调用 `check_and_failover()` → 验证 backup 激活
- [ ] **Step 2: 测试告警** — failover 触发后有 alert event
- [ ] **Step 3-5**: 实现 + commit

---

## Task 9: 健康 tab 显示自动降级历史（前端）

**Files:**
- Modify: `index.html` — health tab 加 `③ 自动降级历史` section
- Modify: `dashboard.js` — `loadFailoverHistory()` 调 `/api/health/failover/history`

**结构：**
- 头图：DATA SOURCE HEALTH（蓝绿色）
- ① 健康度摘要（现有）
- ② 端点详情（现有）
- ③ 自动降级历史（新增）— 表格：时间 / provider / from → to / 失败率 / 当前状态
- ④ I/O 样本（折叠，现有）

**Tests:**
- [ ] **Step 1-5**: API + 渲染

---

## Task 10: 个股 → 决策钻取接口

**Files:**
- Create: `apps/api/routers/dashboard.py::GET /api/dashboard/stock/{code}/decisions` — 查 `fact_decision_trace` + `fact_strategy_output`
- Create: `tests/test_dashboard.py::test_stock_decisions_returns_recent_traces`

**返回结构：**
```json
{
  "code": "600519",
  "decisions": [
    {
      "trace_id": "...",
      "business_date": "2026-07-01",
      "verdict": "approved",
      "model_version": "v3.2.1",
      "fallback_used": false,
      "rationale_summary": "...",
      "evidence_refs": ["market_regime=RISK_ON_EXPANSION", "sector_score=0.85"]
    }
  ]
}
```

**Tests:**
- [ ] **Step 1-5**: TDD

---

## Task 11: 个股 → 决策钻取前端

**Files:**
- Modify: `index.html` — stock tab 在 K 线 section 下加 `② 委员会决策依据` section
- Modify: `dashboard.js` — `loadStockDecisions(code)` 调新接口，渲染

**结构：**
```
K 线走势
② 委员会决策依据 [🆕]
  - 顶部：最近 10 个 trace 时间线
  - 点击 → 展开：verdict / model_version / fallback / rationale / evidence_refs
  - 跳转链接：去 signals 钻取详情
```

**Tests:**
- [ ] **Step 1-5**: TDD 验证 600519 决策 trace 渲染

---

## Task 12: 板块热力图（前端核心体验）

**Files:**
- Modify: `index.html` — signals tab 的 `② 板块轮动` section 升级：表 + 热力图
- Modify: `dashboard.js` — `renderSectorHeatmap()` 用 chartjs-chart-matrix
- Create: `apps/api/routers/dashboard.py::GET /api/dashboard/sector/heatmap` — 返回 31 个申万行业 × 5 日强度矩阵
- Create: `tests/test_dashboard.py::test_sector_heatmap_returns_industry_matrix`

**数据：**
- 矩阵：31 个行业（行）× 最近 5 个交易日（列）
- 颜色：-5% 红 → 0 灰 → +5% 绿
- 鼠标悬停：显示行业名 / 涨幅 / 成交额

**新增依赖：**
- `chartjs-chart-matrix@2.0.1`（npm）→ 静态资源
- `index.html` 加 `<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-matrix@2/dist/chartjs-chart-matrix.min.js"></script>`

**Tests:**
- [ ] **Step 1-5**: TDD 验证矩阵数据正确 + 渲染非空

---

## Task 13: 整页验证 + 集成测试

**Files:**
- Modify: `tests/test_dashboard.py` — 加 E2E 集成测试
- Modify: `scripts/e2e_smoke.py` — 加新接口 smoke

**E2E 流程：**
1. 启动后端 + worker
2. 调 `POST /api/admin/etl/run?date=2026-07-07` 触发 ETL（用现成 endpoint）
3. 调 `GET /api/dashboard/overview` 验证 `global_indices` 有港美股
4. 调 `GET /api/macro/CPI_YOY` 验证返回 12 期
5. 调 `GET /api/dashboard/stock/600519/decisions` 验证 decisions
6. 浏览器加载 11 个 tab，全部 hero 渲染正常

**Tests:**
- [ ] **Step 1-5**: 跑 e2e_smoke.py 全绿

---

## Task 14: 数据审计 + 性能验证

**Files:**
- Create: `tests/test_audit_clean.py::test_p0_no_mock` — 验证 P0 新代码无 Mock
- Create: `tests/test_audit_clean.py::test_p0_no_silent_fallback` — 验证无 `except: pass`

**Tests:**
- [ ] **Step 1-5**: ruff + pytest + audit 全绿

---

## Self-Review

### Spec coverage
- [x] 多市场真实数据 — Task 1, 2, 3
- [x] 行业指数 K 线 — Task 4
- [x] 宏观数据 — Task 5, 6
- [x] 每日信号 cron 监控 — Task 7
- [x] 数据健康自动降级 — Task 8, 9
- [x] 个股决策钻取 — Task 10, 11
- [x] 板块热力图 — Task 12
- [x] 集成验证 — Task 13
- [x] 审计锁 — Task 14

### Type consistency
- 所有 router 路径一致：`/api/...`
- 所有 ORM 字段一致：`tenant_id` 必带
- 所有 dashboard 函数：`loadXxx()`

### Plan complete

