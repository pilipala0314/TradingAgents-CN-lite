# A-Share-Research-Agent · P1 高级功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** P1 6 项 — Watchlist / 多 run 横向对比 / 告警规则在线编辑 / 报告导出 Markdown / 板块桑基图 / 北交所+创业板 K 线回填

**Architecture:**
- 后端：新增 ORM 模型 + API；扩展现有 router
- 前端：保持 hero + section 风格；新增 section 或新 tab
- 数据：复用 ETL pipeline，加新 provider

**Tech Stack:** FastAPI + SQLAlchemy 2 + Chart.js 4.4 + chartjs-chart-sankey + 现有 data_router

---

## Global Constraints

- 设计一致性：所有新 UI 元素必须用 `.signal-hero` / `.signal-eyebrow` / `.signal-section-head` 组件类
- 数据诚实性：禁止 Mock；provider 失败 → `DataFetchError`
- 租户：所有新数据自动带 `tenant_id`
- 测试：TDD，先写测试再写实现
- 审计：禁止 `try/except: pass` / `if False` 调试残留

---

## Task 1: Watchlist ORM + API

**Files:**
- Create: `models/watchlist.py` — `DimWatchlist` / `MapWatchlistItem` ORM
- Create: `alembic/versions/20260708_0006_fact_watchlist.py`
- Create: `apps/api/routers/watchlist.py` — `GET/POST/DELETE /api/watchlist` + `/api/watchlist/{name}/items`
- Create: `tests/test_watchlist.py`

**ORM Schema:**
```python
class DimWatchlist(Base):
    __tablename__ = "dim_watchlist"
    id = BigInteger PK
    name = String(64)  # 用户自定义
    tenant_id = String(64)
    created_at = DateTime

class MapWatchlistItem(Base):
    __tablename__ = "map_watchlist_item"
    id = BigInteger PK
    watchlist_id = BigInteger FK
    symbol = String(32)
    added_at = DateTime
    note = String(256)
```

**接口：**
- `GET /api/watchlist` — 当前租户所有 list
- `POST /api/watchlist` — 新建 list
- `POST /api/watchlist/{id}/items` — 加 symbol
- `DELETE /api/watchlist/{id}/items/{symbol}` — 移除

**Tests:**
- [ ] **Step 1: 写测试** — 创建 list + 加 600519 + 000001，GET 返回 2 个 items
- [ ] **Step 2-3**: 跑失败 → 实现
- [ ] **Step 4-5**: 通过 + commit

---

## Task 2: Watchlist 前端 sidebar + 跨 tab 共享

**Files:**
- Modify: `index.html` — sidebar 新增 "⭐ 自选股" tab
- Modify: `style.css` — `.watchlist-hero` 渐变（橙黄调）
- Modify: `dashboard.js` — `loadWatchlist()` + 跨 tab 共享 localStorage

**结构：**
- 头图：MY WATCHLIST
- ① 默认 list（用户第一个 list）
- ② 列表管理（新建 / 重命名 / 删除）
- ③ 个股项（每行：代码 / 名称 / 最新价 / 涨跌幅 / +按钮"加到选股"/ "去详情"）
- 跨 tab：把 watchlist 第一项作为 stock tab 默认值

**Tests:**
- [ ] **Step 1-5**: 端到端验证 sidebar 显示 + 跨 tab 联动

---

## Task 3: 多 run 横向对比后端

**Files:**
- Modify: `services/backtest_persister.py` — 加 `compare_runs(job_ids: list[str])` 函数
- Modify: `apps/api/routers/backtest.py` — `GET /api/backtest/runs/compare?ids=a,b,c`
- Create: `tests/test_backtest_persister.py::test_compare_runs`

**接口：**
- 入参：`ids=uuid1,uuid2,uuid3`（最多 5）
- 返回：
```json
{
  "runs": [
    {
      "job_id": "...",
      "strategy": "momentum_20d",
      "universe": "hs300",
      "alpha": 0.12,
      "beta": 0.85,
      "sharpe": 1.8,
      "turnover": 0.45,
      "max_drawdown": -0.08,
      "nav_curve": [
        {"date": "2025-07-01", "nav": 1.0},
        ...
      ]
    }
  ]
}
```

**Tests:**
- [ ] **Step 1-5**: TDD 验证 3 个 run nav 对齐到同一日期轴

---

## Task 4: 多 run 对比前端

**Files:**
- Modify: `index.html` — backtest tab 加 `⑨ 多 run 对比` section
- Modify: `dashboard.js` — `loadRunComparison()` + chart 多线叠加

**结构：**
- 顶部：run 选择器（多选下拉，最多 5）
- ① 关键指标对比表（横排 metric × run）
- ② nav 曲线叠加图（每 run 一条线）
- ③ alpha / beta / sharpe 柱状对比图

**Tests:**
- [ ] **Step 1-5**: TDD 验证 select 2 run 后 nav 叠加图渲染

---

## Task 5: 告警规则在线编辑 API

**Files:**
- Modify: `services/health_alert.py` — 加 `update_rule(rule_id, body)` 函数
- Modify: `apps/api/routers/health.py` — `PUT /api/health/alerts/rules/{id}` + `DELETE`
- Create: `tests/test_health_alert.py::test_update_rule_changes_threshold`

**接口：**
- `PUT /api/health/alerts/rules/{id}` body: `{"threshold": 0.6, "comparator": "<"}`
- `DELETE /api/health/alerts/rules/{id}` 软删（is_active=False）
- 验证：编辑后下次评估用新阈值

**Tests:**
- [ ] **Step 1-5**: TDD

---

## Task 6: 告警规则在线编辑前端

**Files:**
- Modify: `index.html` — alerts tab 规则表加"编辑"按钮（每行）+ 编辑 modal
- Modify: `dashboard.js` — `editAlertRule(id)` + `updateAlertRule(id, body)`

**结构：**
- 规则表格新增"操作"列：✏️ 编辑 / 🗑️ 删除
- 点击编辑 → 弹出 inline form：threshold / comparator / labels
- 保存 → PUT → 刷新列表

**Tests:**
- [ ] **Step 1-5**: 端到端验证编辑 threshold 后告警行为变化

---

## Task 7: 报告导出 Markdown 后端

**Files:**
- Create: `services/report_exporter.py` — `export_daily_report(business_date, tenant_id) -> str`
- Modify: `apps/api/routers/dashboard.py` — `GET /api/dashboard/signals/report?date=...&format=md`

**报告结构（Markdown）：**
```markdown
# A 股研究日报 · 2026-07-01

## 市场概览
- 市场判定：RISK_ON_EXPANSION
- 风险模式：aggressive
- 风格提示：growth

## 板块轮动
1. 农林牧渔  +3.2%
2. 电子      +2.8%
...

## 准入个股（Top 10）
| 代码 | 名称 | 板块 | 模型评分 | 模型版本 | 委员会 |
|------|------|------|----------|----------|--------|
| 600519 | 贵州茅台 | 白酒 | 0.92 | v3.2.1 | ✓ |
...

## 拦截个股（Top 10）
...

## 委员会决策依据
...

## 归因分析
- alpha = 0.12
- beta = 0.85
- sharpe = 1.8
```

**LLM 增强**（关键）：每个 section 末尾加 LLM 生成的"🤖 AI 解读"段落
- 输入：当日数据 + LLM 调用
- 输出：200-300 字解读

**Tests:**
- [ ] **Step 1-5**: TDD 验证返回 markdown 含所有 section

---

## Task 8: 报告导出前端按钮

**Files:**
- Modify: `index.html` — signals tab hero 加"📄 导出报告"按钮
- Modify: `dashboard.js` — `exportDailyReport(date, format)` → 浏览器下载 .md 文件

**结构：**
- 头图操作区：[↻ 刷新] [展开/折叠] [仅看异常] [📄 导出报告]
- 点击导出 → 调接口 → 触发浏览器下载

**Tests:**
- [ ] **Step 1-5**: 端到端验证 .md 文件下载

---

## Task 9: 板块桑基图后端

**Files:**
- Modify: `services/sector_service.py`（可能需要新建） — `get_sector_flow(date)` 返回桑基数据
- Modify: `apps/api/routers/dashboard.py` — `GET /api/dashboard/sector/sankey?date=...`

**返回结构：**
```json
{
  "nodes": [
    {"id": "sw_801010", "name": "农林牧渔"},
    {"id": "sw_801080", "name": "电子"},
    {"id": "stock_600519", "name": "贵州茅台"},
    ...
  ],
  "links": [
    {"source": "sw_801010", "target": "stock_600519", "value": 0.85},
    ...
  ]
}
```

**Tests:**
- [ ] **Step 1-5**: TDD

---

## Task 10: 板块桑基图前端

**Files:**
- Modify: `index.html` — signals tab 板块 section 升级：表 + 热力图 + 桑基图
- Modify: `dashboard.js` — `renderSectorSankey()` 用 `chartjs-chart-sankey`

**新增依赖：**
- `<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-sankey@0.10.0"></script>`

**结构：**
- ① 板块轮动表（现有）
- ② 板块热力图（5 日强度，来自 P0 Task 12）
- ③ 板块桑基图（行业 → 概念 → 资金流）🆕

**Tests:**
- [ ] **Step 1-5**: TDD 验证桑基数据 + 渲染

---

## Task 11: 北交所 K 线回填

**Files:**
- Create: `data_pipeline/etl/bse_kline_etl.py` — `ak.stock_bj_hist_em(symbol=...)`
- Create: `data_pipeline/etl/bse_listing_sync.py` — 拉北交所全 A 列表
- Create: `tests/test_bse_kline_etl.py`
- Modify: `data_pipeline/etl/pipeline.py` — close pipeline 加 bse 步骤

**接口：** `get_bse_kline("830xxx", 30)`

**Tests:**
- [ ] **Step 1-5**: TDD 验证北交所 5 只样本 K 入库

---

## Task 12: 创业板 K 线回填

**Files:**
- Create: `data_pipeline/etl/gem_kline_etl.py` — `ak.stock_zh_a_hist(symbol='300xxx', ...)`
- Create: `data_pipeline/etl/gem_listing_sync.py` — 拉创业板全 A 列表
- Create: `tests/test_gem_kline_etl.py`
- Modify: `data_pipeline/etl/pipeline.py` — close pipeline 加 gem 步骤

**接口：** `get_gem_kline("300xxx", 30)`

**Tests:**
- [ ] **Step 1-5**: TDD 验证创业板 5 只样本 K 入库

---

## Task 13: Watchlist 跨 tab 联动

**Files:**
- Modify: `dashboard.js` — `loadStock(code)` 自动检查 code 是否在 watchlist，加 ⭐ 标记
- Modify: `index.html` — stock tab hero 加 ⭐ 按钮（加入/移出当前 watchlist）

**结构：**
- 个股详情页右上：⭐（加入）/ ⭐（已在）
- 点击 → 调 POST/DELETE /api/watchlist/{id}/items

**Tests:**
- [ ] **Step 1-5**: 端到端验证加入 + 跨 tab 显示

---

## Task 14: 整页验证 + 集成测试

**Files:**
- Modify: `tests/test_dashboard.py` — 加 E2E P1 集成测试
- Modify: `scripts/e2e_smoke.py` — 加 P1 接口 smoke

**E2E 流程：**
1. 调 `POST /api/watchlist` 创建 list
2. 调 `POST /api/watchlist/{id}/items` 加 600519
3. 调 `GET /api/dashboard/signals/report?date=2026-07-01&format=md` 验证 markdown 含 watchlist 数据
4. 调 `GET /api/backtest/runs/compare?ids=...` 验证多 run 对比
5. 浏览器加载新 tab/新 section，验证渲染

**Tests:**
- [ ] **Step 1-5**: e2e_smoke.py 全绿

---

## Self-Review

- [x] Watchlist — Task 1, 2, 13
- [x] 多 run 对比 — Task 3, 4
- [x] 告警编辑 — Task 5, 6
- [x] 报告导出 — Task 7, 8
- [x] 板块桑基 — Task 9, 10
- [x] 数据补全 — Task 11, 12
- [x] 集成验证 — Task 14

