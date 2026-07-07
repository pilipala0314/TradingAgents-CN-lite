# P0/P1/P2 设计复核与执行路线

> 日期：2026-07-07
> 目标：基于已落地的 P3/P4/P5/P6 现状，复核三阶段计划的真实性、识别已存在的可复用资产、明确前后端落地边界、给出推荐执行模式。

---

## 0. 核心目标对齐（用户诉求 vs 系统现状）

### 用户原诉求
1. 完成 P0 / P1 / P2 全部任务
2. 前后端都要做
3. 保持现有 hero/eyebrow/section 设计语言
4. **理解目标和达到的效果，结合 数据 + LLM + Agent 完成数据分析**

### "结合 数据 + LLM + Agent" 的落地语义（关键）
P0/P1/P2 的"增强点"不是简单的多接一个接口，而是 **让数据流经 LLM 产生决策级解读**：

| 层 | 角色 | 落地形式 |
|---|---|---|
| **数据层** | 拉取 + 归一化 + 入库 | ETL pipeline、Provider 链、Resilience（circuit_breaker / rate_limit / retry）|
| **Agent 层** | 结构化推理 + 兜底 | LangGraph 6 节点 → AgentOutputEnvelope → 决策溯源（FactDecisionTrace）|
| **LLM 层** | 生成自然语言解读 / 摘要 / 异常归因 | `core/llm/client.py` → 受 schema 约束的 JSON 输出 |
| **呈现层** | 头图 + 折叠 + 钻取 | hero/eyebrow/section 风格 + Chart.js 矩阵/桑基 |

**核心理念**：每个新增数据 section 必须有"LLM 解读"或"Agent 决策依据"出口，禁止数据孤岛。

---

## 1. 现状 vs 计划：可复用资产识别（避免返工）

| Plan 任务 | 文档原文 | 实际现状 | 处置 |
|---|---|---|---|
| **P0-T1 港股 K 线** | "hk_full_provider.py 已有 stub，需补真实 K 线逻辑" | **HKFullProvider 已完整**（get_stock_list / get_daily_bars / get_index_daily_bars + normalize_symbol），已带 `_run_with_retry` | ✅ Provider 跳过，只做 **ETL**（hk_kline_etl.py）|
| **P0-T2 美股 K 线** | "新建 us_kline_etl.py / futures_kline_etl.py" | **USStockProvider 已完整**（get_daily_bars / get_spot_quote）；**futures_provider.py 已存在** | ✅ Provider 跳过，只做 ETL |
| **P0-T3 全球指数 tab** | "扩展 overview 返回 global_indices" | **GlobalQuoteProvider 已完整**（8 个默认指数：sh/sz/hk/us），已被 `/api/dashboard/overview` 调过 | ✅ 改前端渲染层（4 张子表分组），后端**可能零改动** |
| **P0-T4 行业指数 K 线** | "新建 sw_industry_provider.py" | **未存在** | 🆕 全做 |
| **P0-T5 宏观数据** | "新建 macro_provider.py / macro_etl.py" | **macro_service 已完整**（4 端点：PMI/CPI/M2/Bond + regime hint）；**`/v1/macro/snapshot` 已存在** | ✅ 后端**大部分跳过**，只补 **ETL 入库**（fact_macro_indicator）+ 解读服务 |
| **P0-T6 宏观 Macro Tab** | "新增 macro tab + LLM 解读" | **未做**（前端无 macro tab）| 🆕 全做 |
| **P0-T7 cron 监控** | "GET /api/admin/scheduler/status" | **未做** | 🆕 全做 |
| **P0-T8 自动降级** | "新建 provider_failover.py" | **未做** | 🆕 全做 |
| **P0-T9 降级历史前端** | "health tab 加 ③ section" | **未做** | 🆕 全做 |
| **P0-T10 决策钻取 API** | "/api/dashboard/stock/{code}/decisions" | **未做** | 🆕 全做 |
| **P0-T11 决策钻取前端** | "stock tab ② section" | **未做** | 🆕 全做 |
| **P0-T12 板块热力图** | "/api/dashboard/sector/heatmap" | **未做** | 🆕 全做 |
| **P1-T1 自选股 ORM** | "DimWatchlist / MapWatchlistItem" | **未做** | 🆕 全做 |
| **P1-T3 多 run 对比** | "/api/backtest/runs/compare" | **未做** | 🆕 全做 |
| **P1-T5 告警在线编辑** | "PUT/DELETE /api/health/alerts/rules/{id}" | **未做**（只有 list / add）| 🆕 全做 |
| **P1-T7 报告导出 MD** | "/api/dashboard/signals/report?format=md" | **未做** | 🆕 全做 |
| **P1-T9 板块桑基图** | "/api/dashboard/sector/sankey" | **未做** | 🆕 全做 |
| **P1-T11/12 北交所 + 创业板 K** | "bse_kline_etl / gem_kline_etl" | **未做** | 🆕 全做 |
| **P2-T1 资金流 ETL** | "fact_capital_flow 入库" | **已实现**（CapitalFlowSyncJob.run_close_flows）+ pipeline 步骤 | ✅ 跳过，**直接复用** |
| **P2-T3 财务报表 ETL** | "FactFinancialReport ORM + ETL" | **ORM 未做**，ETL 未做 | 🆕 全做 |
| **P2-T5 新闻舆情 ETL** | "FactNewsSentiment + LLM 打分" | **未做** | 🆕 全做 |
| **P2-T7 Model Registry** | "core/model_registry.py" | **未做** | 🆕 全做 |
| **P2-T11/13 回测衔接** | "signal_strategy_selector.py" | **未做** | 🆕 全做 |

### 总结
- **✅ 跳过 / 直接复用**：P0-T1/T2/T3（provider）、P0-T5（macro service）、P2-T1（capital_flow ETL）= **5 个任务**
- **🆕 全做**：P0-T4/T6/T7/T8/T9/T10/T11/T12、P1-T1~T14、P2-T3~T15 = **~33 个任务**

---

## 2. 目标效果 / 完成态指标

### P0 完成态（数据补齐 + 决策钻取）
- **首页 overview**：5 个子板块全活（健康度 + 全球指数 [港/美/期货/主流] + 行业指数 + 宏观 + 降级历史）
- **新增 macro tab**：4 个核心宏观指标 + LLM 解读（5 句话讲清当前宏观态）
- **signals tab hero**：显示 cron 状态卡（"今日 17:00 ETL ✓" / "上次失败原因"）
- **stock tab**：K 线 + 决策钻取 ② section（最近 10 个 trace + 点击展开）
- **板块热力图**：31 申万行业 × 5 日矩阵可视化（一屏看完热点）
- **health tab ③**：自动降级历史时间线

### P1 完成态（高级功能）
- **自选股 tab ⭐**：可建多个 list、跨 tab 共享、加 ⭐ 标记
- **backtest ⑨ 多 run 对比**：选 5 个 run → 3 张对比图叠加
- **alerts 规则编辑**：每行 inline edit，threshold 改完立即生效
- **signals hero 导出按钮**：.md 报告下载（含 LLM 解读）
- **板块桑基图**：行业 → 概念 → 资金流三层流向
- **北交所/创业板 K 线**：universe 池支持扩展

### P2 完成态（高级分析）
- **stock tab 资金流 ③**：5 日堆叠柱状图（超大/大/中/小单）
- **stock tab 财务摘要 ④**：4 卡 + 双线 + LLM 解读
- **stock tab 新闻舆情 ⑤**：整体情绪条 + 20 条新闻
- **model registry 后台**：A/B 流量分布 + 在线调 ratio
- **回测 → 线上衔接**：手动切换 + 自动选最优（按 sharpe）

---

## 3. LLM 增强点（贯穿 P0/P1/P2 的"灵魂"）

| 任务 | LLM 作用 | 输入 | 输出 |
|---|---|---|---|
| P0-T6 宏观解读 | 把 5 个指标讲成人话 | PMI/CPI/M2/Bond + 趋势 | 5-8 句"当前宏观态 = risk-on expansion" |
| P0-T11 决策钻取 | 把 agent 输出讲成一段 | verdict + rationale + evidence_refs | 1-2 句"为什么通过/拦截" |
| P1-T7 报告导出 | 把当日全市场讲成 md | 信号/板块/准入/拦截/归因 | 200-300 字"今日 A 股研究日报" |
| P2-T4 财务解读 | 把 8 季财务讲成趋势 | 营收/利润/毛利/ROE | 1 段"增长故事 + 风险点" |
| P2-T5 新闻舆情 | 把标题打分 + 解释 | 新闻文本 | score ∈ [-1, 1] + 1 句解释 |
| P2-T11/13 策略选择 | 把回测结果讲成推荐 | 多个 run 的 sharpe/alpha | "推荐 run #abc，因为 sharpe 1.8 > 其他" |

---

## 4. 任务优先级与依赖关系

```
P0（先做，解锁后续）
├── T1+T2 (港美股 K ETL) ──┐
├── T3  (全球指数前端) ────┤  ←  几乎免费，借力现有 provider
├── T4  (行业指数 K) ──────┤
├── T5+T6 (宏观 ETL + tab) ┤
├── T7  (cron 监控) ───────┤
├── T8+T9 (降级) ──────────┤
├── T10+T11 (决策钻取) ────┤
└── T12 (热力图) ──────────┘

P1（功能扩展）
├── T1+T2+T13 (自选股) ──────┐
├── T3+T4  (多 run 对比) ────┤
├── T5+T6  (告警编辑) ───────┤
├── T7+T8  (报告导出) ───────┤  ← 强依赖 P0-T5/T11 的数据
├── T9+T10 (桑基) ───────────┤
└── T11+T12 (北交所/创业板) ─┘

P2（深度分析）
├── T1+T2  (资金流，已实现) ──┐
├── T3+T4  (财报) ────────────┤
├── T5+T6  (新闻舆情) ────────┤
├── T7~T10 (Model Registry) ──┤
└── T11~T13 (回测衔接) ──────┘
```

**关键依赖**：
- P0-T10（决策钻取）解锁 P1-T7（报告导出）
- P0-T12（板块热力图）解锁 P1-T9（桑基图）
- P0-T5（宏观 ETL）解锁 P0-T6（macro tab）
- P2-T3/5（财报/新闻）解锁 P2-T4/6（前端 + LLM 解读）

---

## 5. 执行模式选项

### 选项 A：Subagent-Driven（推荐）
- 每个 task 派一个 fresh subagent 执行
- **优点**：上下文隔离、出错回滚容易、并行提速
- **缺点**：subagent 间交接需要清晰的接口契约
- **预计工时**：~3-4 小时集中执行 + 期间 review

### 选项 B：Inline Execution
- 当前会话顺序执行，每 2-3 task 一个 checkpoint
- **优点**：交互更紧密
- **缺点**：上下文累积、容易飘移

### 选项 C：Hybrid
- 简单 ETL 任务（HK/US K 线）inline 做
- 复杂前端（macro tab / 板块热力图 / 桑基图）subagent 派
- LLM 解读 / 复杂 ETL（财报/新闻）subagent 派

**推荐**：选项 C（hybrid），原因：
- 简单 ETL 当前会话 inline 即可（10-15 分钟一个）
- 复杂前端需要完整的浏览器验证，subagent 自带 Chrome MCP 更好闭环

---

## 6. 风险与回滚

| 风险 | 触发条件 | 回滚 |
|---|---|---|
| DB migration 失败 | alembic upgrade 报 unique 冲突 | 备份 DB 后 0001~0004 全删，新写 0005 |
| LLM 调用超时 | 解读返回空 | 降级到"🤖 解读暂不可用"占位 |
| 数据源 429 | akshare 限流 | 退避 + circuit_breaker 已有 |
| chartjs 插件 CDN 失败 | matrix / sankey CDN 挂 | fallback 到 table 渲染 |
| 决策钻取扫表慢 | fact_decision_trace 大表 | 强制 LIMIT 50 + index 检查 |

---

## 7. 启动确认

请用户确认：

1. **执行模式**（A / B / C，推荐 C）
2. **从 P0-T1 开始**还是**先做 P0-T6 宏观 tab**（更直观，能立刻看 LLM 效果）？
3. **LLM 解读失败降级**策略：占位 vs 隐藏？（推荐：占位"🤖 解读暂不可用"）
4. **每个 task 完成后**，是否需要"前端打开浏览器看效果"再进下一个？

回复确认后，按 P0 → P1 → P2 顺序开干。
