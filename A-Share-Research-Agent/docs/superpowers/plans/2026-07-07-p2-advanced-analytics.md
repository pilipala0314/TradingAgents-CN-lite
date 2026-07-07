# A-Share-Research-Agent · P2 高级分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** P2 6 项 — 资金流向 / 财务报表 / 新闻舆情 / A/B 测试框架 / 模型热更新 / 回测→线上衔接

**Architecture:**
- 后端：新增 ORM + provider + 路由；model_registry 服务
- 前端：watchlist / stock / signals 三个 tab 扩展
- LLM：舆情打分 + 财报解读

**Tech Stack:** FastAPI + SQLAlchemy 2 + model_registry 抽象 + 现有 LLM 客户端

---

## Global Constraints

- 设计一致性：所有新 UI 元素沿用 `.signal-hero` / `.signal-eyebrow` / `.signal-section-head`
- 数据诚实性：禁止 Mock；provider 失败 → `DataFetchError`
- 租户：所有新数据自动带 `tenant_id`
- 测试：TDD
- 审计：禁止 `try/except: pass` / `if False`

---

## Task 1: 资金流向 FactCapitalFlow 入库

**Files:**
- Create: `data_pipeline/etl/capital_flow_etl.py` — 调 `data_pipeline/providers/capital.py`（已存在）
- Create: `tests/test_capital_flow_etl.py`
- Modify: `data_pipeline/etl/pipeline.py` — close pipeline 加 capital_flow 步骤

**注意**：`FactCapitalFlow` ORM 已存在（models/facts.py:53），不要重复创建

**接口：** `get_capital_flow("600519", date_range=5)` — 返回主力/散户/游资分项

**Tests:**
- [ ] **Step 1-5**: TDD 验证 600519 资金流入库到 fact_capital_flow

---

## Task 2: 资金流前端（stock 详情）

**Files:**
- Modify: `apps/api/routers/dashboard.py` — `GET /api/dashboard/stock/{code}/capital_flow?days=5`
- Modify: `index.html` — stock tab 加 `③ 资金流向` section
- Modify: `dashboard.js` — `loadCapitalFlow(code)` + Chart.js stacked bar

**结构（stock tab）：**
- ① K 线走势（现有）
- ② 委员会决策依据（来自 P0）
- ③ 资金流向（5 日）🆕 — 堆叠柱状图：超大单 / 大单 / 中单 / 小单
- ④ 财务摘要（来自 Task 4）
- ⑤ 新闻舆情（来自 Task 6）

**Tests:**
- [ ] **Step 1-5**: 端到端验证 600519 资金流渲染

---

## Task 3: 财务报表 ETL

**Files:**
- Create: `data_pipeline/etl/financial_report_etl.py` — 调 `data_pipeline/providers/financial_report_provider.py`（已存在）
- Create: `models/financials.py` — `FactFinancialReport` ORM
- Create: `alembic/versions/20260709_0007_fact_financial_report.py`
- Create: `tests/test_financial_report_etl.py`

**ORM Schema:**
```python
class FactFinancialReport(Base):
    __tablename__ = "fact_financial_report"
    id = BigInteger PK
    symbol = String(32)
    period = String(16)  # "2025Q4" / "2025"
    revenue = Numeric(18, 2)
    net_profit = Numeric(18, 2)
    gross_margin = Numeric(8, 4)
    debt_ratio = Numeric(8, 4)
    roe = Numeric(8, 4)
    raw_payload = JSON
    tenant_id = String(64)
    source = String(32)  # "eastmoney" / "sina"
    published_at = DateTime
```

**Tests:**
- [ ] **Step 1-5**: TDD 验证 600519 季度报告入库

---

## Task 4: 财务报表前端（stock 详情）

**Files:**
- Modify: `apps/api/routers/dashboard.py` — `GET /api/dashboard/stock/{code}/financials?periods=8`
- Modify: `index.html` — stock tab 加 `④ 财务摘要` section
- Modify: `dashboard.js` — `loadFinancials(code)` 渲染

**结构：**
- 顶部：4 张关键指标卡（营收 / 净利润 / 毛利率 / ROE，最近 8 季）
- 营收 / 净利润 双线图（8 季趋势）
- LLM 增强：每段末尾"🤖 财报解读"（200-300 字分析）

**Tests:**
- [ ] **Step 1-5**: 端到端验证 600519 财报渲染 + LLM 解读

---

## Task 5: 新闻舆情 ETL + 打分

**Files:**
- Create: `data_pipeline/etl/news_etl.py` — 调 `data_pipeline/providers/news.py`（已存在 cls + 财经新闻）
- Create: `services/sentiment_analyzer.py` — `analyze_sentiment(text) -> float (-1..1)` 调 LLM
- Create: `models/news.py` — `FactNewsSentiment` ORM
- Create: `alembic/versions/20260709_0008_fact_news_sentiment.py`
- Create: `tests/test_news_etl.py` + `tests/test_sentiment_analyzer.py`

**LLM 增强：** 用 `core/llm/client.py` 调 chat 模型，prompt：
```
你是一个金融情绪分析专家。给定新闻文本，返回 -1.0（极度负面）到 1.0（极度正面）的情绪分数 + 1-2 句中文解释。
输入：{text}
输出 JSON: {"score": 0.6, "explain": "..."}
```

**ORM Schema:**
```python
class FactNewsSentiment(Base):
    __tablename__ = "fact_news_sentiment"
    id = BigInteger PK
    symbol = String(32)  # 可空（市场新闻）
    published_at = DateTime
    title = String(256)
    url = String(512)
    source = String(32)
    score = Numeric(5, 2)
    explain = String(512)
    tenant_id = String(64)
```

**Tests:**
- [ ] **Step 1-5**: TDD 验证新闻入库 + LLM 打分

---

## Task 6: 新闻舆情前端（stock 详情）

**Files:**
- Modify: `apps/api/routers/dashboard.py` — `GET /api/dashboard/stock/{code}/news?limit=20`
- Modify: `index.html` — stock tab 加 `⑤ 新闻舆情` section
- Modify: `dashboard.js` — `loadNews(code)` 渲染新闻列表 + 情绪条

**结构：**
- 顶部：整体情绪条（0=负面，0.5=中性，1=正面）— 大色卡
- ① 最近 20 条新闻（每行：时间 / 标题 / 情绪色条 / 摘要）
- 颜色：<-0.3 红 / -0.3~0.3 灰 / >0.3 绿

**Tests:**
- [ ] **Step 1-5**: 端到端验证 600519 新闻渲染

---

## Task 7: Model Registry（配置驱动 A/B）

**Files:**
- Create: `core/model_registry.py` — `get_active_model() -> str` / `set_ratio(model_name, ratio)` / `pick_model(symbol) -> str`
- Create: `tests/test_model_registry.py`
- Modify: `core/config.py` — 加 `MODEL_REGISTRY` 字段（JSON 格式）

**配置格式：**
```python
MODEL_REGISTRY = {
    "default": "v3.2.1",
    "models": {
        "v3.2.1": {"ratio": 0.7, "status": "active"},
        "v3.3.0-beta": {"ratio": 0.2, "status": "active"},
        "v3.3.0-canary": {"ratio": 0.1, "status": "active"},
    }
}
```

**API：** `pick_model(symbol) -> str` 按 ratio 加权随机（保持可重现用 hash(symbol) % 100）

**Tests:**
- [ ] **Step 1-5**: TDD 验证 pick_model 10000 次分布符合 ratio

---

## Task 8: A/B 测试在 decision trace 中可视化

**Files:**
- Modify: `services/signal_persister.py` — 持久化时记录 `model_version`（已有字段，验证写入）
- Modify: `apps/api/routers/dashboard.py` — `GET /api/dashboard/signals/trace?group_by=model_version`
- Modify: `index.html` — signals tab 决策审计 section 升级加 "A/B 流量分布" 子图
- Modify: `dashboard.js` — `renderABDistribution()`

**结构（signals tab ⑥ 决策审计）：**
- 顶部 3 卡：v3.2.1 (70% / N traces) / v3.3.0-beta (20%) / v3.3.0-canary (10%)
- 柱状图：实际分布 vs 预期 ratio

**Tests:**
- [ ] **Step 1-5**: 端到端验证 group_by=model_version 返回正确数据

---

## Task 9: 模型热更新 API

**Files:**
- Modify: `apps/api/routers/admin.py` — `POST /api/admin/models/reload` 重新读 config
- Create: `tests/test_admin.py::test_model_reload`

**接口：**
- `GET /api/admin/models/registry` — 当前所有 model + ratio
- `POST /api/admin/models/reload` — 重新读 config
- `PUT /api/admin/models/{name}/ratio` — 修改 ratio（写 config + 触发 reload）
- 验证：修改 ratio 后 pick_model 立即生效

**Tests:**
- [ ] **Step 1-5**: TDD 验证 ratio 修改 + reload 后立即生效

---

## Task 10: 模型管理前端（admin tab）

**Files:**
- Modify: `index.html` — admin tab（或新建 admin tab）加 "模型管理" section
- Modify: `dashboard.js` — `loadModelRegistry()` / `updateModelRatio(name, newRatio)`

**结构（admin tab 新增）：**
- ① 模型注册表 — 表格：模型名 / ratio / status / 启用停用 / 编辑 ratio
- ② 实时流量分布 — 来自 Task 8 接口
- ③ A/B 实验效果 — 表格：模型 / trace 数 / 胜率 / 平均分

**Tests:**
- [ ] **Step 1-5**: 端到端验证编辑 ratio 生效

---

## Task 11: 回测 → 线上信号衔接（v1 手动）

**Files:**
- Create: `services/signal_strategy_selector.py` — `pick_strategy_from_run(run_id) -> dict` 读回测配置
- Modify: `apps/api/routers/dashboard.py` — `GET /api/dashboard/signals/current_strategy` + `POST /api/dashboard/signals/set_strategy`
- Create: `tests/test_signal_strategy_selector.py`

**接口：**
- `GET /api/dashboard/signals/current_strategy` — 当前线上用的 strategy 配置
- `POST /api/dashboard/signals/set_strategy` body: `{"run_id": "..."}` — 切换
- 切换后写 fact_strategy_config，trace 引用 config_id

**Tests:**
- [ ] **Step 1-5**: TDD 验证 set_strategy 持久化

---

## Task 12: 回测衔接前端（signals tab）

**Files:**
- Modify: `index.html` — signals tab hero 加 "🔧 切换策略" 按钮
- Modify: `dashboard.js` — `showStrategySwitcher()` 弹层选 run

**结构（signals tab hero）：**
- 当前策略徽章：momentum_20d / hs300 / 2026-Q2 (run #abc)
- 点击切换 → 弹层列最近 10 run → 选 → POST

**Tests:**
- [ ] **Step 1-5**: 端到端验证切换后 trace 引用新 run

---

## Task 13: 回测衔接 v2（自动选最优）

**Files:**
- Modify: `services/signal_strategy_selector.py` — 加 `pick_best_strategy()` — 按 sharpe 排序取 top 1
- Create: `tests/test_signal_strategy_selector.py::test_pick_best`

**逻辑：**
```python
def pick_best_strategy(tenant_id) -> str:
    # 取最近 30 天 completed run
    runs = get_recent_runs(tenant_id, days=30, status='completed')
    # 按 sharpe desc 排
    runs.sort(key=lambda r: r.sharpe, reverse=True)
    return runs[0].job_id
```

**触发：**
- 每日 research cron 跑前自动调 `pick_best_strategy()`，把结果写到 fact_strategy_config
- signals tab 显示 "🤖 自动选择（按 sharpe 排序）"

**Tests:**
- [ ] **Step 1-5**: TDD 验证排序逻辑 + 自动选择

---

## Task 14: 整页验证 + 集成测试

**Files:**
- Modify: `tests/test_dashboard.py` — 加 E2E P2 集成测试
- Modify: `scripts/e2e_smoke.py` — 加 P2 接口 smoke

**E2E 流程：**
1. 调 `GET /api/dashboard/stock/600519/capital_flow?days=5` 验证资金流
2. 调 `GET /api/dashboard/stock/600519/financials?periods=8` 验证财报 + LLM
3. 调 `GET /api/dashboard/stock/600519/news?limit=20` 验证新闻 + 情绪
4. 调 `GET /api/admin/models/registry` 验证 A/B 配置
5. 调 `POST /api/dashboard/signals/set_strategy` 验证切换
6. 浏览器加载 stock 详情 / signals / admin tab 验证渲染

**Tests:**
- [ ] **Step 1-5**: e2e_smoke.py 全绿

---

## Task 15: 审计 + 性能验证

**Files:**
- Create: `tests/test_audit_clean.py::test_p0_p1_p2_no_mock_no_silent`

**Tests:**
- [ ] **Step 1-5**: ruff + pytest + audit 全绿

---

## Self-Review

- [x] 资金流向 — Task 1, 2
- [x] 财务报表 — Task 3, 4
- [x] 新闻舆情 — Task 5, 6
- [x] A/B 框架 — Task 7, 8, 9, 10
- [x] 模型热更新 — Task 9, 10
- [x] 回测衔接 — Task 11, 12, 13
- [x] 集成验证 — Task 14
- [x] 审计锁 — Task 15

