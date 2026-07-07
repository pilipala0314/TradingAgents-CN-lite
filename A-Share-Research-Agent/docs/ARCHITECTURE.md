# 架构设计（A-Share Research Agent）

> 版本：v0.7+（P3 监控 + P5 多租户）
> 范围：数据层 / Agent 层 / 服务层 / API 层 / 运维层

## 1. 系统全景

```
                                ┌──────────────────────────────────────┐
                                │           Web Dashboard              │
                                │  (HTML + JS + Chart.js, 无构建)      │
                                └──────────────┬───────────────────────┘
                                               │ HTTP / JSON
                                ┌──────────────▼───────────────────────┐
                                │   FastAPI (apps/api/main.py)         │
                                │  ┌─ Router: research/backtest/...    │
                                │  ├─ Middleware: Tracing/Metrics      │
                                │  ├─ Lifespan: scheduler + APP_INFO   │
                                │  └─ Static mount                     │
                                └──────────────┬───────────────────────┘
                                               │
        ┌──────────────────────┬──────────────┼─────────────┬──────────────┐
        │                      │              │             │              │
        ▼                      ▼              ▼             ▼              ▼
┌───────────────┐  ┌────────────────────┐ ┌────────────┐ ┌─────────┐ ┌──────────────┐
│ Data Pipeline │  │  Services           │ │  Graph     │ │ Rule    │ │  Scheduler   │
│ ┌──────────┐  │  │  backtest_engine    │ │ research_  │ │ Engine  │ │  etl_        │
│ │DataRouter│◄─┼──┤  backtest_attr     │ │ graph      │ │ (3层    │ │  scheduler   │
│ │(central) │  │  │  backtest_metrics   │ │ (6 nodes)  │ │ gates)  │ │  research_   │
│ ├──────────┤  │  │  backtest_persist   │ └────────────┘ └─────────┘ │  scheduler   │
│ │Provider  │  │  │  walkforward        │                            └──────────────┘
│ │× 9       │  │  │  montecarlo         │
│ │+ Resilience│ │  │  health_alert       │
│ │(retry/R  │  │  │  retention/tenant   │
│ │ circuitB)│  │  └─────────┬──────────┘
│ └──────────┘  │            │
│ ┌──────────┐  │            ▼
│ │ Redis    │  │  ┌────────────────────┐
│ │Cache     │  │  │  PostgreSQL 16     │
│ │(or in-mem│  │  │  (SQLAlchemy 2.x)  │
│ │ fallback)│  │  │  9 fact_* + 2 dim  │
│ └──────────┘  │  │  + 4 outputs      │
│ ┌──────────┐  │  └────────────────────┘
│ │ Universes│  │
│ │(6 preset)│  │
│ └──────────┘  │
└───────────────┘
```

## 2. 模块依赖

| 层级 | 模块 | 责任 |
|---|---|---|
| 入口 | `apps/api/main.py` | FastAPI 装配 + 中间件 + lifespan |
| 路由 | `apps/api/routers/*.py` | 9 个 domain router（research/backtest/admin/...） |
| 中间件 | `apps/api/middleware.py` | Tracing + Metrics |
| 业务 | `services/*.py` | 回测引擎/归因/持久化/wf/mc/告警/保留/租户 |
| 数据 | `data_pipeline/data_router.py` | 中央路由 + 9 providers + 韧性 |
| 数据 | `data_pipeline/cache/*` | InMemory / Redis（自动降级） |
| 数据 | `data_pipeline/universes.py` | 6 预设股票池 |
| 数据 | `data_pipeline/resilience/*` | Retry/RateLimit/CircuitBreaker/IpPool/Bulkhead |
| Agent | `agents/*.py` | market/sector/stock/committee + report_renderer |
| Graph | `graph/research_graph.py` | LangGraph 6 节点编排 |
| 模型 | `models/*` | SQLAlchemy 2.x ORM（dim_/fact_/feature_/outputs） |
| 工具 | `core/{metrics,telemetry,config,llm,logger,database}.py` | 指标/trace/配置/LLM/日志/DB |

## 3. 关键数据流

### 3.1 研究流（每日 17:30 自动）
```
research_scheduler.cron(17:30)
  → graph.research_graph.invoke({business_date, window_type})
    → Node1 MarketAgent       (LLM/规则)
    → Node2 SectorAgents × N  (并行)
    → Node3 SectorRanking     (LLM/规则)
    → Node4 StockAgents × M  (并行)
    → Node5 Committee        (LLM/规则 + trace 落库)
    → Node6 ReportRenderer
  → services/signal_persister.upsert()  (4 类 → fact_strategy_output)
  → services/backtest_persister.upsert() (committee decision → fact_decision_trace)
```

### 3.2 数据流（DataRouter）
```
caller → DataRouter.call(endpoint, **kwargs)
  → CacheBackend.get(key)  (hit: return)
  → endpoint 解路由（method:provider table）
  → ResiliencePolicy.execute  (Retry → RateLimit → CircuitBreaker → Bulkhead)
  → provider.method(**kwargs)
  → result.normalized
  → CacheBackend.set(key, result, ttl)
  → DATA_ROUTER_CALLS / LATENCY 计数
```

### 3.3 回测流（同步/异步）
```
POST /v1/backtest/run(/async)
  → BacktestConfig 解析
  → BacktestEngine.run(config)
    → 拉 benchmark K 线（可选）
    → 调仓日序列
    → 每调仓日：拉成分股 K 线 → 调策略 → 算组合 → 算成本 → 算指标
  → result.to_dict()  +  cost_summary + benchmark_metrics
  → persist（async 模式走 BackgroundTasks）
  → ATTRIBUTION（可选 P4.5 α/β + Score IC）
```

### 3.4 告警流
```
HealthAlertService.start_periodic(60s)
  → collect_prometheus_samples()  (从 APP_REGISTRY)
  → rule.check(samples)  →  if should_alert + not dedup:
    → AlertEvent → ALERTS_TRIGGERED.inc
    → dispatch to channels:
      - LogChannel  (always)
      - NoopChannel (test)
      - WebhookChannel (POST env: ALERT_WEBHOOK_URL)
    → history.append (cap 1000)
```

## 4. 状态机

### 4.1 BacktestRun
```
pending → running → completed
              ↓
            failed
              ↓
          cancelled
```
- 终态保护：completed/failed 不可再修改 config_hash

### 4.2 DataRouter circuit
```
CLOSED ──失败率超阈值──► OPEN ──冷却──► HALF_OPEN
   ▲                          │           │
   └─────────试探成功──────────┴───────────┘
                试探失败 → 重新 OPEN
```

## 5. 多租户（P5.3）

- `tenant_id` 列加到 9 张 fact_* 表（nullable + indexed）
- 解析优先级：`ASHARE_TENANT` env > 显式参数 > `"default"`
- DB 失败降级：service 方法 try/except + warning log
- 跨租户数据物理隔离（按 tenant_id 过滤）

## 6. 监控（P3.4）

- **指标**：Prometheus 文本格式，`/metrics` 端点
- **Trace**：轻量级 OTel 风格（无 sdk 依赖），ContextVar 隔离
  - 每个 HTTP 请求 → root span
  - LLM/DataRouter 调用 → 子 span
  - 父→子 trace_id 一致
- **告警**：Prometheus samples → 阈值规则 → webhook 投递
- **Dashboard 调试**：`/metrics/traces` 看最近 100 个 span

## 7. 性能边界

| 模块 | 单进程 | 多进程 | 备注 |
|---|---|---|---|
| DataRouter | 100 rps | 100×N | 缓存命中率 > 50% 后续命 cache |
| Backtest | 1 跑 ≈ 5s（200 交易日 × 20 股） | 1 跑/进程 | async 模式后台 |
| Research | 1 跑 ≈ 30s（LLM 不可用走规则） | 1 跑/进程 | LLM 真调 ≈ 60-90s |
| Cache | 内存毫秒级 | Redis ms 级 | url=None 自动 in-mem |

## 8. 部署（最小可运行）

- 进程：`uvicorn apps.api.main:app --host 0.0.0.0 --port 8000`
- DB：`postgres://user:pass@host:5432/ashare` (DATABASE_URL)
- 缓存：`redis://host:6379/0` (REDIS_URL，可空)
- LLM：`DEEPSEEK_API_KEY` + `LLM_API_BASE=https://api.deepseek.com`
- Webhook（可选）：`ALERT_WEBHOOK_URL=https://hooks...`
- 租户（可选）：`ASHARE_TENANT=acme`

详细运维见 [OPERATIONS.md](file:///d:/my-project/take-it-controll-road2freedom/0-start-here/TradingAgents-CN-lite/A-Share-Research-Agent/docs/OPERATIONS.md)。
