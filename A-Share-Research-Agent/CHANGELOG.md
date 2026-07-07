## [0.6.0] - 2026-07-03

### Added - 回测引擎
- 新增 `services/strategies.py` 抽象基类及 4 个内置策略（20 日动量、5 日反转、MA250 价值、多因子动量波动率）
- 新增 `services/backtest_metrics.py` 纯函数计算核心回测指标（年化、夏普、回撤、胜率、盈亏比等）
- 新增 `services/backtest_engine.py` 核心引擎，支持动态选取调仓日（daily/weekly/monthly）与组合构建
- 新增 `/v1/backtest/*` 系列 API，并对 Dashboard 进行页面展示（配置区、指标摘要、净值曲线、调仓记录、跳过记录）
- 页面完成 UI 美化与健壮性提升（中英文字段翻译、按钮状态防抖、卡片阴影与响应式布局）

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added - P2 阶段（回测增强 + 页面丰富）

> 阶段定位：在最小可运行版（v0.6.0）基础上，把"回测"做成可对外交付的子系统。

#### P2.2 成本模型 + 基准
- `BacktestConfig` 扩展：commission_rate / stamp_tax_rate / slippage_rate / benchmark（"hs300"/"csi500"/.../None）
- `BacktestResult` 新增 `cost_summary`（commission / stamp_tax / slippage 各自累计金额 + 占初始资金比）+ `benchmark_metrics` + `alpha_beta`
- 修复 P2.7 实施时漏掉 `alpha_beta` 函数的真 bug（实现完整线性回归 + 协方差计算）
- 修复 `trading_dates` 字段为空列表的真 bug（从 market_bar 重算）

#### P2.3 异步回测 + 9 端点
- `BackgroundTasks` 模式：POST `/v1/backtest/run/async` 立即返 job_id，后台跑完可查
- 9 个端点全集：`GET /strategies` `GET /universes` `GET /pool/{preset}` `GET /default-config` `POST /run` `POST /run/async` `GET /runs/{job_id}` `GET /runs/{job_id}/attribution` `GET /runs`

#### P2.5 持久化
- `FactBacktestRun` ORM：状态机 pending/running/completed/failed/cancelled + 16 位 SHA256 `config_hash` 判同配置
- `services/backtest_persister.py`：compute_config_hash / status state machine / upsert / fetch / list
- 终态保护：completed/failed 后禁止更新 config_hash
- 修复 SQLAlchemy 2.x 解析编译语句时 `Update._values` key 是 Column / value 是 BindParameter 的兼容

#### P2.6 股票池
- 6 个 preset：hs300 / csi500 / csi1000 / gem / star / all_a
- 24h 进程内缓存 + 抽样机制（避免重复打外网）
- `data_pipeline/universes.py` + 异步 router 注入
- 修复 `resolve_universe` 同步调 async router 没 await 的真 bug → 改 async def

#### P2.7 基准
- 接 sina 指数 K 线 + 真实 alpha/beta 线性回归
- 修复 `_compute_benchmark` 依赖循环 import 的设计问题（用 TYPE_CHECKING 隔离）

#### P2.8 归因
- `services/backtest_attribution.py`：α/β 回归 + Score IC + 持仓偏离度
- 4×30 大规模对比脚本 `scripts/run_4x30_backtest.py`

#### 前端丰富
- 多 tab 导航（市场/个股/对比/数据源/回测/信号…）
- 回测 tab：universe 下拉 + benchmark 选择 + 成本明细面板 + 基准对比图 + 归因面板
- 4×30 对比视图 + 历史 run 列表 + 异步轮询

#### 测试
- `test_universes.py` 15 个 + `test_backtest_persister.py` 30 个 + `test_backtest_api.py` 异步端点 +10 个
- 全量 pytest **488 → 657 通过**

### Added - P3 阶段（工程硬化 + 监控）

#### P3.2 Alembic 迁移
- `alembic/versions/20260707_0003_fact_decision_trace_and_backtest_run.py` — 升级 fact_decision_trace（15 字段 + unique + 2 索引）+ fact_backtest_run（15 字段 + 2 unique + 3 索引）

#### P3.3 Redis 缓存
- `data_pipeline/cache/redis_cache.py` 真正实现：url=None 降级 / 启动期 ping / 运行时 ping / 同步 redis-py 8.x / pickle 序列化 / SCAN+MATCH 删 prefix / health() 输出 dbsize + used_memory_human
- 12 个单测覆盖：url=None 降级 / 不可达 url / TTL 路径 / pickle 复杂对象 / 运行时断开降级 / health 两种状态 / key_prefix 自定义 / fallback 计数

#### P3.4 监控（OTel 风格 + Prometheus）
- `core/metrics.py` 集中式指标：HTTP / Cache / DataRouter / Backtest / Alert / LLM / Decision
- `core/telemetry.py` 轻量级 Tracer（无 opentelemetry-sdk 依赖）：ContextVar 隔离 / span 父子关系 / trace_id 16 hex / LLM span 自动写 LLM_LATENCY
- `apps/api/middleware.py` TracingMiddleware + MetricsMiddleware：path label 归一化（UUID/hex → `{id}`）防高基数
- `apps/api/routers/metrics.py`：`GET /metrics` Prometheus 文本 + `GET /metrics/traces` 最近 span + `GET /metrics/info` App 元信息
- 15 个单测：normalize_path / tracer 单例 / 父子关系 / 属性记录 / 异常 mark error / current_span / registry / generate_latest / FastAPI 集成

#### P3.5 Webhook 告警
- `services/health_alert.py`：AlertRule（threshold + comparator 5 种）+ AlertChannel（Log / Webhook / Noop）+ AlertEvent + HealthAlertService（evaluate_now + start_periodic 60s + 60s 去抖 + 历史 cap 1000）
- 内置规则：data_router_fallback_rate (>= 0.20 warn) / cache_hit_ratio (< 0.30 warn)
- `apps/api/routers/admin.py`：`/admin/alert/rules` `/alert/channels` `/alert/channel`（POST 新增）/ `/alert/test`（手动触发）/ `/alert/dispatch`（自定义事件）/ `/alert/history?limit=50`
- 17 个单测：rule 5 种 comparator / 3 channel 类（noop / log / webhook 200/5xx/网络错）/ service 评估 / dedup / history cap / 周期启停

#### P3.6 DataRouter 拆分
- `data_pipeline/router/registry.py`：TOOL_CATEGORIES（15 类）/ CORE_METHOD_TABLE（21）/ EXTENDED_METHOD_TABLE（35）/ merged_method_table / list_methods / list_categories / find_category
- `data_pipeline/router/health.py`：EndpointStats（threading.Lock + total/success/failure/fallback/latency deque）+ health_score（成功率 × 延迟惩罚）
- `data_pipeline/router/__init__.py` re-export
- 主体 DataRouter 类保持原样（兼容老 API），新增模块提供"新接口"（list / categorize / find）

### Added - P5 阶段（高级分析 + 多租户 + 文档）

#### P5.2 Walk-forward + 蒙特卡洛
- `services/walkforward.py`：anchored / rolling 两模式窗口切分 + train_pct + OOS 指标汇总
- `services/montecarlo.py`：block bootstrap（block_size 5）+ 5 维分布（final_return / sharpe / max_drawdown / VaR95 / CVaR95）+ 可选 seed 复现
- 端点 `POST /v1/backtest/wf/run` + `POST /v1/backtest/mc/run`
- 16 个单测：split_folds 5 / run_walkforward 2 / _percentile / _dist_stats / 6 个 run_montecarlo 边界

#### P5.3 多租户 + 数据保留
- `tenant_id` 列加到 9 张 fact_* 表（nullable + indexed）— alembic 0004 迁移
- `services/tenant.py`：current_tenant_id（env + arg 优先级）/ list_tenants_with_usage / get_tenant_usage
- `services/retention.py`：9 表策略表（retain_days + min_rows）+ env 覆盖（`RETENTION_<TABLE>_DAYS`）+ dry_run 默认 + DB 失败降级
- `apps/api/routers/admin.py` 扩展：`/admin/retention/status` `/admin/retention/run?dry_run=true` `/admin/tenants` `/admin/tenant/{tenant_id}/usage`
- 12 个单测：current_tenant_id 5 / get_policy 3 / retention 状态 + dry_run + 错误聚合 4

#### P5.4 文档
- `docs/CHANGELOG.md`（本文档更新到 P3/P5 完整版）
- `docs/ARCHITECTURE.md`（架构图 + 数据流 + 模块依赖）
- `docs/OPERATIONS.md`（部署 + 运维 + 告警 + 监控面板说明）

### 累计
- 全量 pytest 突破 **800 项**（P2 657 + P3 13+15+17=45 + P5 12+16=28 + 其它新增）
- ruff **0 error**

## [0.6.0] - 2026-07-03

### Added - 回测引擎
- 新增 `services/strategies.py` 抽象基类及 4 个内置策略（20 日动量、5 日反转、MA250 价值、多因子动量波动率）
- 新增 `services/backtest_metrics.py` 纯函数计算核心回测指标（年化、夏普、回撤、胜率、盈亏比等）
- 新增 `services/backtest_engine.py` 核心引擎，支持动态选取调仓日（daily/weekly/monthly）与组合构建
- 新增 `/v1/backtest/*` 系列 API，并对 Dashboard 进行页面展示（配置区、指标摘要、净值曲线、调仓记录、跳过记录）
- 页面完成 UI 美化与健壮性提升（中英文字段翻译、按钮状态防抖、卡片阴影与响应式布局）

### Added - P1 财务三表（东财数据中心）
- **`FinancialReportProvider`**（新 provider，[data_pipeline/providers/financial_report_provider.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/data_pipeline/providers/financial_report_provider.py)）：3 个端点走**东财数据中心** `RPT_DMSK_FN_BALANCE / CASHFLOW / INCOME`（HTTP JSON）— `get_balance_sheet` / `get_cashflow_statement` / `get_income_statement`，每个端点返回 pd.DataFrame（与 AkshareProvider 旧实现同形）
- **DataRouter 注册**：[data_router.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/data_pipeline/data_router.py) `EXTENDED_METHOD_TABLE` 把 3 端点从 `akshare` 切到 `financial_report`；`include_extended=True` 时启用
- **AkshareProvider 减负**：[akshare_provider.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/data_pipeline/providers/akshare_provider.py) 删 `get_balance_sheet` / `get_cashflow_statement` / `get_income_statement` 3 个方法（akshare 1.18.64 财务 API 全部上游损坏：`soup.find(attrs={"id": "hidctype"})["value"]` 返 None → TypeError）。端点签名保持不变，调用方 0 改动
- **测试 +10**：[test_financial_report_provider.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/tests/test_financial_report_provider.py) — provider 3 方法结构 + 返回 DataFrame + 失败透传（3 类）+ DataRouter 路由切到 financial_report + 不再走 akshare（断言）
- **真网路 e2e**：[verify_financial_reports.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/scripts/verify_financial_reports.py) — 600519 / 000001 三表全过：balance 102/118 条、cashflow 98/103 条、income 102/121 条（2-6 秒/单端点），写 [results_real_net_financial_reports.json](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/results_real_net_financial_reports.json) 备查
- 全量 pytest **389/389 → 399/399 通过**（55.52s；新增 10 financial_report_provider test）

### Added - 真实信号流端到端
- **`services/signal_persister.py`** — 4 类信号 → `fact_strategy_output` upsert：market 顶层 / sector ranking（每 sector 一条） / approved 个股 / blocked 个股。Upsert 冲突键 `(entity_type, entity_id, business_date, window_type, horizon, strategy_type, output_version)`，同一日多次跑自动覆盖。`entity_id` 用 uuid5 派生（不依赖 security_master），market 顶层用固定 `market:aggregate` key。LLM 不可用时（fallback）也落库，带 `fallback_used=true` 标记
- **API 端点**：[research.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/apps/api/routers/research.py) `/v1/research/report` 跑完自动调 persister（best-effort，失败不阻断响应）；新增 3 个查询端点：
  - `GET /v1/research/signals/latest?business_date=YYYY-MM-DD` — 4 类分组（market / sector / approved / blocked），可指定日期或取最新
  - `GET /v1/research/signals/today` — 业务日期为今日（Asia/Shanghai）的 4 类
  - `GET /v1/research/signals/{strategy_id}` — 单条信号详情（含 supporting_evidence + output_payload）
- **APScheduler 自动跑**：[research_scheduler.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/apps/scheduler/research_scheduler.py) `register_research_jobs()` 在 lifespan 注册 `daily_research_flow` cron（17:30 工作日）。时序：A 股 15:00 收盘 → 17:00 ETL 收尾 → 17:30 跑 research 流（基于 ETL 落库的事实）。失败不可阻塞 scheduler（log + 返回 ok=False 不抛）
- **Dashboard 第 5 tab**：[index.html](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/apps/api/static/index.html) + [dashboard.js](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/apps/api/static/dashboard.js) "今日信号" — 4 section：① 顶层市场（verdict / risk_budget / market_regime / rationale） ② 板块排名（按 score desc） ③ approved 个股（按 confidence desc，含 entry_timing + trend_signal） ④ blocked 个股（带 reason）。含刷新按钮 + "无信号" 告警条
- **测试 +54**：[test_signal_persister.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/tests/test_signal_persister.py) 37 个（uuid5 稳定性 / 4 类 row 构造 / fallback 标记 / persist 入口 panic/全流程/空 3 种路径）+ [test_signals_api.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/tests/test_signals_api.py) 9 个（latest 4 类分组 / today / by_id 400/404/200 + report persist ok/fail/skipped 3 路径）+ [test_research_scheduler.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/tests/test_research_scheduler.py) 5 个（lookup err / 昨日推断 / 成功流程 / graph 异常 / register job）+ [test_dashboard.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/tests/test_dashboard.py) +3 个（tab 元素 / js handlers / switch 分支）
- **真链路 e2e**：[verify_real_signal_flow.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/scripts/verify_real_signal_flow.py) — 探测 LLM → 跑图 → 落库 → 读出，3 步全测，写 [results_real_signal_flow.json](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/results_real_signal_flow.json) 备查。LLM/DB 任一不可用时优雅降级（status=llm_unavailable / db_unavailable，不抛）
- **Bug 修复**：research.py `/report` 端点 committee_decision 取值错误（`.get("structured_output")` 当 dict 取 — 但 AgentOutputEnvelope 是 Pydantic 模型），改为 `final["committee_decision"].structured_output`
- 全量 pytest **335/335 → 389/389 通过**（55.18s；新增 37 signal_persister + 9 signals_api + 5 scheduler + 3 dashboard_signals = 54 test）

### Added - Dashboard 交互深化
- 多 tab 导航: [index.html](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/apps/api/static/index.html) 加 4 个 tab（市场/个股/对比/数据源），`switchTab()` 切换显示 + chart resize
- 多股对比: [compare 端点](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/apps/api/routers/dashboard.py) `GET /api/dashboard/compare?codes=600519,000001` 批量拉最多 5 只股票 60 日 K 线；前端 `renderCompare()` 同一 chart 画多折线 + 5 色循环 + 日期轴对齐 + 归一化起点 toggle
- K 线指标切换: 个股 tab 加 2 个 select（`kline-metric`: 收盘价/涨跌幅/成交量；`kline-days`: 30/60/120），切换时重画 chart
- chart 交互: [dashboard.js](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/apps/api/static/dashboard.js) 集成 chartjs-plugin-zoom@2.2.0 via CDN，K 线 + 对比图都支持滚轮缩放 + 拖动平移
- 测试 +4: [test_dashboard.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/tests/test_dashboard.py) 4 个新 test：compare 正常返回 / 超过 5 个截断 / 非法 code 报错 / 空 codes 报错

### Added - 数据源健康告警 + Dashboard 显指标
- 后端新端点: `GET /api/dashboard/health` ([router](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/apps/api/routers/dashboard.py)) — 聚合 DataRouter 健康度，返每个 endpoint × provider 的 success/total/failure/success_rate/p95_latency_ms/fallback_count/last_error + 3 类告警（`low_success_rate` / `last_error` / `high_latency`）。告警规则：`success_rate < 0.5` → low_success_rate；`last_error` 非空 → last_error；`p95_latency_ms > 3000` → high_latency
- 前端新 section: Section 4 数据源健康监控 — Dashboard 第 4 个 card，含：告警总览条（绿/黄/红）+ Provider 过滤下拉 + "仅告警"复选 + 9 列详细表
- CSS 告警样式: [style.css](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/apps/api/static/style.css) — `.alert-bar`（绿/黄/红三色）、`.alert-row`（行背景黄/红）、`.badge`（3 类告警徽章）、`.rate-low/mid/high`（成功率三色）、`.p95-slow`（高延迟标蓝）、`.error-cell`（错误信息 monospace 红字）
- 新测试 +2: [test_dashboard.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/tests/test_dashboard.py) 2 个 health detail test：字段完整性 + 告警规则一致性

### Added - Web Dashboard
- `apps/api/routers/dashboard.py` — Dashboard API: `GET /api/dashboard/overview` 返 health 摘要 + 全球指数（best-effort，失败降级空 dict 不抛 500）；`GET /api/dashboard/stock/{code}` 返单股 quote + 30 根 K 线 + 14 日公告 + errors 字段（局部失败不阻断其他字段）
- `apps/api/static/index.html` + `dashboard.js` + `style.css` — 零构建单页 Dashboard，Chart.js 4.4 via CDN（无 npm 依赖）。3 个 section：系统健康度 / 全球指数 / 个股详情（含 K 线 + 公告）
- `apps/api/main.py` — 挂 `StaticFiles` + `dashboard.router`；`GET /` 直返 index.html
- `tests/test_dashboard.py` — 7 个新 test：根 HTML / 静态 3 资源 / overview / stock detail / 非法 code 容错 / health 字段完整性 / 告警规则一致性

### Added — P0 数据源扩展
- **`CninfoProvider`**（巨潮 P0）：[data_pipeline/providers/cninfo.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/data_pipeline/providers/cninfo.py) 加 `CninfoProvider` 类，包装已有 `fetch_cninfo_announcements` 函数。端点 `get_cninfo_announcements(code, recent_days=7, category="")` → list[{announcement_id, title, published_at, url, type, type_code, orgid}]。底层走 `http://www.cninfo.com.cn/new/data/szse_stock.json` 拿 code→orgid 映射（带模块级缓存），再 POST `hisAnnouncement/query` 拉公告列表
- **`GlobalQuoteProvider`**（国际 P0）：[data_pipeline/providers/global_quote_provider.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/data_pipeline/providers/global_quote_provider.py) 全新 provider。端点 `get_global_quote(codes=None)` → dict[code: {name, price, change_pct, pe_ttm, mcap_yi, ...}]。底层走 `https://qt.gtimg.cn/q=`（腾讯财经 GBK 编码，不封 IP），支持 A 股 sh/sz/bj + 港股 hk + 美股 us + 指数
- **`_parse_quote_response` 拆出**：[data_pipeline/providers/_shared/tencent.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/data_pipeline/providers/_shared/tencent.py) 把 qt.gtimg.cn 多行响应解析拆为独立函数，前缀白名单扩到 `sh/sz/bj/hk/us`（原仅 A 股 3 前缀），`GlobalQuoteProvider` 复用解析逻辑避免重复造轮子。`tencent_quote()` 仍仅 A 股（行为不变，向后兼容）
- **DataRouter 注册**：[data_pipeline/data_router.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/data_pipeline/data_router.py) `EXTENDED_METHOD_TABLE` 加 2 个端点 + 默认 providers dict 加 2 个 provider。`include_extended=True` 时启用
- **facade_normalizer 归一化**：[data_pipeline/facade_normalizer.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/data_pipeline/facade_normalizer.py) `EXPECT_LIST_OF_DICT_METHODS` 加 `get_cninfo_announcements`（list[dict] 形态归一化）
- **测试**：[tests/test_p0_extensions.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/tests/test_p0_extensions.py) 13 个新单测（_parse_quote_response × 6 / CninfoProvider × 3 / GlobalQuoteProvider × 4）
- **真网路端到端**：[scripts/verify_real_network_p0_extensions.py](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/scripts/verify_real_network_p0_extensions.py) 跑通：cninfo 000001 返 3 条公告（深振业/万科/国药一致等），cninfo 600519 协议成功但 30 天无公告（"协议成功无数据"语义生效），global_quote 8/8 全球指数全过（5 A 股指数 + 恒生 + 道指 + 纳指），写 [results_real_net_p0_extensions.json](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/results_real_net_p0_extensions.json) 备查

### Fixed
- **akshare 依赖补齐**（P0-5）：`pip install akshare` 安装 1.18.64，附带 `pip install 'fastapi>=0.110.0' 'pydantic-settings>=2.2.0' 'prometheus-fastapi-instrumentator>=7.1.0'` 修复 pip 依赖解析器自动降级造成的版本冲突。修复后 28 个 akshare 端点全部实活，akshare 替代 sina 成为 K 线 / 行情 / 龙虎榜 / 资金流主路径
- **真网路验证 6/7 → 6/7 端点成功**：akshare 主路径下，3 个 quote (sh600519/sz000636/000001) + 3 个 daily_bars (各 9 根 K 线，shape=(9, 12)) 全成功；1 个 `get_stock_list` 走 akshare `stock_zh_a_spot_em` 上游返空（akshare 数据源本身问题，非 code bug，符合"协议成功无数据"语义）
- **Committee LLM 真链路闭环**（真 bug）：`agents/committee.py:62` 调 `create_llm_client()` 缺 `api_key` / `base_url` 参数，导致 committee 永远走规则 baseline 而非真 LLM。补 `base_url=settings.LLM_API_BASE` + `api_key=settings.LLM_API_KEY` 后，DeepSeek 真可调
- 全量 pytest **307/307 → 322/322 通过**（107.75s；新增 13 个 P0 扩展 test + 2 个真 LLM e2e test）

### Added
- **`scripts/verify_llm_call.py`** — 真 LLM smoke：调一次 DeepSeek，验 API key / base_url / model 真可调。跑后写 `results_llm_smoke.json` 备查（4.61s 返真响应，链路闭环）
- **`scripts/verify_compose_config.py`** — docker-compose 5 服务静态检查：不依赖 docker daemon，纯 YAML 配置 + healthcheck 字段 + depends_on 链路 + ports 绑定 + volumes 段 + worker celery command。`0 error, 3 warning`（api/worker/flower 缺 healthcheck 是设计内，由 depends_on 串接上游）
- **`tests/test_graph_e2e_real.py`** — LangGraph 6 节点真 LLM e2e：2 个 test 跑真 LLM（不 mock `create_llm_client`），`_LLM_OK` 自动探测 api_key 决定 skip。snapshot 补 `continuation_score` / `flow_inflow_score` / `event_confirmation_score` 让 fallback 也能让 SectorRankingSynthesizer 拿到 stage

## [0.5.0] - 2026-07-03

### Added — 数据层与韧性底座
- DataRouter 支持 `cache=` 自定义注入
- DataRouter 支持 `include_extended=True` 启用 EXTENDED 端点
- DataRouter `register_method()` 动态注册新端点
- `EndpointStats` 新增 `fallback_count` / `fallback_rate` 字段（首选失败 → 兜底计数）
- `/health` 三模式：`summary`（默认）/ `full` / `endpoints`
- `pyproject.toml` 完善：dev/scheduler/tdx/stealth 依赖分组 + ruff/mypy/coverage 工具配置
- 测试覆盖：test_data_router 26 / test_cache 12 / test_health 5
- **韧性层 `data_pipeline/resilience/`** — 5 大策略（Retry / RateLimit / CircuitBreaker / IpPool / Bulkhead）+ `ResiliencePolicy` 装饰器链编排
  - `RetryPolicy` 指数退避 + 抖动 + 异常白名单
  - `RateLimiter` 令牌桶 rps + burst
  - `CircuitBreaker` 滑动窗口 + CLOSED/OPEN/HALF_OPEN 3 状态机
  - `IpPool` Protocol + `ProxyEnvIpPool`（HTTP_PROXY 轮询）+ `CffiImpersonateIpPool`（curl_cffi 指纹轮换）
  - `Bulkhead` 信号量并发隔离
  - `ResilienceError` 统一失败语义，被 DataRouter 视作 provider 失败 → 走 fallback
- **4 个新 Provider**（akshare 反爬时降级 + 数据源扩张）：
  - `SinaProvider`（hq.sinajs.cn，4 端点：实时行情 / 日 K / 分钟 K / 个股信息）
  - `NeteaseProvider`（api.money.126.net，3 端点：实时行情 / 日 K / 分钟 K）
  - `HKSouthboundProvider`（东方财富 push2 kamt 接口，2 端点：沪/深港通南向资金 + 领涨股）
  - `ResearchRAGProvider`（东财研报列表，2 端点：chunks + 关键词检索）
- **P0 端点补强**：`get_earnings_forecast` / `get_analyst_consensus` / `get_research_reports` / `get_regulatory_penalties`
- **宏观端点**：`get_macro_pmi` / `get_macro_cpi` / `get_macro_m2` / `get_macro_bond_yield`
- 测试覆盖：test_resilience 22 / test_data_router_resilience 9 / test_new_providers 30 / test_p0_endpoints 6 / test_macro_service 8

### Changed
- `DEFAULT_METHOD_TABLE` → `CORE_METHOD_TABLE`（28 个高频端点）
- 新增 `EXTENDED_METHOD_TABLE`（20 个低频端点：财报三表 / 分钟线 / 高级事件 / 个股龙虎榜 + P0 + 宏观 + 港股通 + 研报 RAG）
- AkshareProvider / AStockDataProvider class docstring 重写，明确端点契约 + 失败语义
- DataRouter 默认注入 6 个 Provider（akshare / a_stock_data / sina / netease / hk_southbound / research_rag）— 轻量级，构造时不发请求
- 4 个新 Provider 用 `httpx` 作 HTTP 客户端（已是 fastapi 依赖，零新增依赖）

### Fixed
- 修复 `_to_sina_symbol` / `_to_netease_symbol` 不处理 `sh600519` / `bj830799` 前缀的 bug
- 修复 netease CSV 列序解析错位（开盘/收盘/最高/最低）
- 修复 ResearchRAG 分词把中文单字过滤掉的 bug（保留 alnum 整体匹配 5G/AI）
- 修复 DataRouter 兜底链的 lambda 闭包 coroutine 复用问题

### Added — 签名契约层（v0.5.0）
- **`data_pipeline/adapters.py`** — 通用 Adapter 协议
  - `AdaptedProvider` 包装 provider 方法 + adapter
  - `adapt()` 工厂支持 4 种用法（裸字符串 / 字符串+provider / 字符串+adapter+provider / callable）
  - 内置 `make_kwarg_renamer` / `make_list_to_single_arg_adapter` / `make_single_to_list_arg_adapter` / `make_passthrough_adapter`
  - `check_signatures_compatible()` 签名 lint 工具
- **`data_pipeline/provider_adapters.py`** — 跨 provider 签名翻译
  - `get_daily_bars` / `get_minute_bars` 统一标准签名 → 各 provider 本地签名
  - 模块导入时自动注册到 `DataRouter._METHOD_ADAPTERS`
- **`data_pipeline/facade_normalizer.py`** — DataFrame → list[dict] 归一化
  - `EXPECT_LIST_OF_DICT_METHODS` 表（30 个 ETL 端点）
  - `normalize_response()` 单端点归一化
  - `normalize_facade_results()` 批量归一化
  - `dataframe_to_list_of_dict()` 优雅处理 NaN / datetime / numpy 类型
- **DataRouter 升级**：
  - `_METHOD_ADAPTERS` class-level dict，注入签名 adapter
  - `__init__` 自动用 `AdaptedProvider` 包装需要 adapter 的 method:provider
  - route() 调用前自动做参数签名转换（调用方零感知）
- **测试覆盖**：test_adapters 30+ / test_provider_adapters 20+ / test_facade_normalizer 33

### Notes
- **优雅原则贯彻**：adapter 是纯函数，DataRouter 0 硬编码；facade_normalizer 是装饰式归一化，调用方 0 改动
- **全量 285/285 pytest 通过**（29.71s）

## [Unreleased] - 2026-07-02 深度审计 + 全量修复

### Fixed (P0 必修 — 数据真实可靠)

| ID | 文件 | 问题 | 修复 |
|---|---|---|---|
| P0-1 | `data_router.py` | `provider_adapters` 模块顶层未触发 import，导致 adapter 死代码 | `DataRouter.__init__` 延迟注册 adapter（避免循环导入） |
| P0-2 | `netease_provider.py` | URL 带 `,moneyHistory` 错后缀 | 改用 `api.money.126.net/data/feed/{symbol}` |
| P0-3 | `netease_provider.py` | `get_minute_bars` 是 stub | 改 raise `NotImplementedError`（DataRouter 走 fallback） |
| P0-4 | `netease_provider.py` | `get_tencent_quote` 接受 str / list 两种类型 | 统一只接受 list，无数据 raise |
| P0-5 | `sina_provider.py` | Sina GBK 编码未处理（name 乱码） | 强制 `resp.encoding = "gbk"` |
| P0-6 | `research_rag_provider.py` | URL 走 `datacenter-web` + 7 个字段名全错（API 实际不在数据中心） | 改 `reportapi.eastmoney.com/report/list`，字段对齐 `title` / `infoCode` / `orgSName` / `publishDate` / `emRatingName` / `industryName` |
| P0-7 | `hk_southbound_provider.py` | `out["ts"]` 循环外引用最后一个循环的 data（多市场时只反映最后一个） | 改循环内累加再 `max()` |
| A1 | `akshare_provider.py` | `get_stock_fundamentals` 调错接口（实际跑的是基础信息） | 改用 `ak.stock_zh_a_indicator`（真财务指标） |
| A3 | `akshare_provider.py` | `get_insider_transactions` 返伪数据（DataFrame 含"占位 message"） | 改 raise `NotImplementedError` + 移出 EXPECT 表 |
| B1 | `quote.py` | `fetch_quote_5level` 静默返 None | 改 raise |
| B2 | `capital.py` | `fetch_fund_flow_minute` 静默返 `[]` | 改 raise |
| B3 | `capital.py` | `fetch_dragon_tiger` 3 个子查询静默吞错 | 改 raise（防委员会准入误判） |

### Fixed (P1 补强 — 代码健壮)

| ID | 文件 | 修复 |
|---|---|---|
| P1-1 | `facade_normalizer.py` | EXPECT 表对齐：删 `get_stock_individual_info`（孤儿 method），加 7 个遗漏 method |
| P1-2 | `facade_normalizer.py` | 删含 `print` debug 的重复 `normalize_facade_results` 定义 |
| P1-3 | `data_router.py` | `register_method` 走 adapter 包装（与 `__init__` 行为对齐） |
| P1-4 | `data_router.py` + `core/config.py` | `ROUTE_DEFAULT_RESILIENCE` settings 开关：开启后自动注入"重试 3 + 限流 5 rps + 熔断 5 + bulkhead 10" |
| P1-5 | `data_router.py` | 6 个单点 method 显式标注 + `/health` 暴露 `single_point_methods` |
| A5 | `akshare_provider.py` | 5 个 sector/LHB method 改 raise，区分"网络错误（raise）"和"协议成功无数据（返空）" |
| A6 | `akshare_provider.py` | 启动期接口完整性检查：一次性扫描 28 个 `ak.xxx` 引用，缺则 warning 列出缺失项 + 用途 |
| A7 | `provider_adapters.py` | `_akshare_minute_bars_adapter` 加日期格式归一化（`YYYYMMDD` / `YYYYMMDDHHMMSS` / `YYYY-MM-DD` / `YYYY-MM-DD HH:MM:SS` 4 种自动转） |
| A8 | `akshare_provider.py` | `_run_with_retry` 改**真正指数退避**（1²/2²/3²=1s/4s/9s），命名与行为一致 |
| B6 | `news.py` | `fetch_stock_news` 改 raise |
| CacheFix | `data_router.py` | cache key 改 **sha256**（避免 `repr` 跨版本漂移） + **stampede 保护**（`threading.Lock` + per-key `asyncio.Lock`） |

### Fixed (代码风格 + 真 bug)

- `policy.py` — F821 真 bug：`CffiImpersonateIpPool` 漏 import
- `data_router.py` — B023 lambda 闭包陷阱：显式绑定循环变量
- `tencent.py` — B023 `vals` 闭包陷阱：显式绑定默认参数
- `base.py` — B027 抽象方法缺 `@abstractmethod` 装饰器
- `scorer.py` — N806 `FAMOUS_BRANDS` 提到 module-level
- `circuit_breaker.py` / `macro_service.py` — SIM102 合并 nested if
- `apps/worker/tasks.py` — F821 真 bug：`AsyncSessionLocal` 未 import + 调用 `run_close_pipeline` 错传 session（pipeline 内部自管 session，外部不应传）
- `apps/api/routers/etl.py` — B904 `raise ... from exc` 显式链式异常
- `tests/test_cache.py` — F821 `Any` 缺 import
- `tests/test_facade_via_router.py` — F841 `r1`/`r2` 加等价性 assert 复用；B017 `pytest.raises(Exception)` 收窄为 `DataFetchError`
- `tests/test_copied_frameworks.py` — 给 `FakeAnalyst` 加 `build_fallback_structured`（适配 base.py 的抽象方法）
- `services/macro_service.py` — **修复 bond_inverted 设计缺陷**：从 score 减分项中移除（避免 PMI 正常时被独立减分 → 误判 contraction），仅作 `risk_off_external_shock` 分支 3 的"配套触发器"
- `data_pipeline/resilience/retry.py` — `raise ResilienceError(...) from last_exc` 显式链式异常（重试用尽时保留原始 traceback）
- `data_pipeline/data_router.py` — 全部失败路径 `raise DataFetchError(...) from last_error` 显式链式异常
- `data_pipeline/resilience/policy.py` — 删冗余内层 `from .ip_pool import CffiImpersonateIpPool`（模块顶部已 import）
- `data_pipeline/provider_adapters.py` — 删 `_sina_minute_bars_adapter` 中无意义的"表达式语句"（dead code）
- `apps/batch/run_fact_pipeline.py` — 修同款 session 错传 bug：与 celery task 对齐，pipeline 自管 session，外部不传 AsyncSessionLocal
- **`provider_adapters._netease_daily_bars_adapter`** — **真 bug**：之前 passthrough 透传 kwargs，但 netease `get_daily_bars(symbol, start, end, adjust)` 期望 `start`/`end`，标准调用方传 `start_date`/`end_date`，导致 netease 永远把 `start/end` 当空 → 返完整历史。修复：重命名 kwargs 后按位置参数传给 netease
- **`provider_adapters._sina_daily_bars_adapter` + `sina_provider.get_daily_bars`** — **真 bug**：sina API 只支持"返最近 N 根"，不支持 date range。原 adapter 把 date range 算成 datalen，但 sina 返的是最近 N 根 → 实际数据与请求范围不匹配。修复：adapter 把 `start_date`/`end_date` 透传为 kwargs，sina provider 内部用 `_filter_bars_by_date` 做 post-filter 截断
- **`sina_provider._filter_bars_by_date`** — 新增 helper：按 `[start, end]` 闭区间过滤 K 线，date 格式归一化（支持 `YYYYMMDD` / `YYYY-MM-DD` / `YYYY/MM/DD`）

### Added

- **测试**：`tests/test_macro_service.py` 新增 12 个单测覆盖 6 种 Market Regime + 边界 + 返回结构 + 参数化 dispatch
- **脚本**：`scripts/verify_real_network_600519.py` 真实网络端到端验证（sh600519 / sz000636 / 000001 抓取）
- **真网路验证结果**（写入 `results_real_net_600519.json`）：
  - `sina.get_tencent_quote(sh600519)` ✅ HTTP 200 — 贵州茅台，prev_close=1203.0
  - `sina.get_tencent_quote(sz000636)` ✅ HTTP 200 — 风华高科，prev_close=66.96
  - `sina.get_tencent_quote(000001)` ✅ HTTP 200 — 平安银行，prev_close=10.28
  - `sina.get_daily_bars(600519)` ✅ HTTP 200 — 31 条 K 线（akshare 未装走 sina fallback）
  - `sina.get_daily_bars(000636)` ✅ HTTP 200 — 31 条 K 线
  - `sina.get_daily_bars(000001)` ✅ HTTP 200 — 31 条 K 线
  - `akshare.get_stock_list` ❌ DataFetchError（依赖未装 — 已知）

### Notes

- **Ruff lint 跑过**（2302 → 60 → **0 error**；剔除 RUF001/002/003 中文误报 + per-file-ignores 覆盖 `tests/*` 一行 stub + 局部未用变量）
- **全量 307/307 pytest 通过**（40.86s，含 12 个 macro test + 1 个新增 test_pmi_normal_bond_inverted + 9 个新增 sina/netease date filter 测试）
- **真网路验证 6/7 端点成功**（akshare 依赖未装时走 sina fallback，零降级语义生效；`/health` 暴露 `single_point_methods` + 端点统计）
- **真网路 date filter 端到端验证通过**：sina `get_daily_bars(600519, start_date=20260613, end_date=20260628)` 返 9 根（sina 返 16 根最近 K 线，post-filter 截到 9 根落在请求范围）
- **已修原"未修设计缺陷"**：`services/macro_service.py:235` bond_inverted 独立触发 score 减分已移除。bond_inverted 现在仅作 `risk_off_external_shock` 分支 3 的配套触发器，PMI 正常时正确返回 `risk_on_neutral` / `risk_on_expansion` 而非误判 `risk_off_contraction`

## [0.1.0] - 2026-07-01

### Added
- 端点 28 个（akshare）+ 10 个（a_stock_data），通过 DataRouter 路由 + fallback
- LangGraph 6 节点研究流：市场 / 板块并行 / 板块排序 / 个股 / 委员会 / 报告
- 状态机：市场（6 态）+ 板块生命周期（6 态）+ 个股战术（5 档）
- 规则引擎：3 层硬门禁（市场 → 板块 → 个股）
- Celery + Redis + APScheduler 双轨调度（盘后 ETL + 盘前报告 + 实时个股）
- FastAPI 接口：`/health` `/research/analyze` `/etl/*` `/rules/*`
- 端到端真数据案例：000636（主力净流出 + 估值风险 → 反转战术结论）
- 单元测试 71 项 + 集成测试
- Docker + docker-compose + Makefile 一键化部署

[Unreleased]: https://github.com/example/a-share-research-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/example/a-share-research-agent/releases/tag/v0.1.0
