# A-Share Research Agent

> **可路由、可插拔、零降级** 的 A 股多源数据 + 多 Agent 协同研究平台

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-green.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Tests](https://img.shields.io/badge/tests-100%2B%20passing-brightgreen.svg)](#测试)
[![Status: Beta](https://img.shields.io/badge/status-beta-yellow.svg)](#项目状态)

[English](#english) · [中文](#中文)

---

## 中文

### 这是什么

**A-Share Research Agent** 是一个生产级 A 股 AI 投研中台。它把"真数据 → 真 LLM 推理 → 委员会裁决 → 报告渲染"做成一条可审查、可扩展、可单测的工程流水线。

**核心理念**：

- **必须真数据**：所有数据源直连 `akshare` / `mootdx` / 腾讯财经 / 东财 datacenter / 巨潮 / 财联社，**不允许任何降级到 mock**。
- **路由是策略，Provider 是实现**：`DataRouter` 只负责"用哪个 provider / 怎么 fallback"，每个数据源都是一个 `Provider` 实现，横向扩展加一行。
- **零硬编码**：所有数据方法注册到 `method_table`，新增端点不改 router。
- **失败语义显式**：任何 provider 失败都抛 `DataFetchError`，由调用方决定是否 strict，**绝不静默降级**。
- **多 Agent 协同**：LangGraph 6 节点（市场 → 板块并行 → 板块排序 → 个股 → 委员会 → 报告），委员会做最终准入/排序，**单一裁决者**。

### 核心亮点

| 维度 | 实现 |
|------|------|
| **数据源** | 28 个 akshare + 10 个 a_stock_data 端点，全部经过 `DataRouter` 路由 |
| **路由策略** | `method: {provider: callable}` 横向扩展 · fallback 链 · TTL 缓存 · 端点健康度统计 |
| **缓存层** | `CacheBackend` Protocol 抽象 · `InMemoryCache`（LRU + TTL + 线程安全）· `RedisCache` 接口预留 |
| **状态机** | 市场（6 态）· 板块生命周期（6 态）· 个股战术（5 档）· 委员会裁决 |
| **规则引擎** | 3 层硬门禁（市场 → 板块 → 个股），所有规则可单测 |
| **调度** | Celery + Redis + APScheduler 双轨（盘后 ETL + 盘前报告 + 实时个股） |
| **可观测** | `/health` 三模式（summary / full / endpoints）· 端点 p95 · fallback 触发率 · 缓存命中率 |
| **测试** | 100+ pytest（unit + integration）· e2e 真数据案例 `000636` |

### 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                       L4 业务层 (apps/)                              │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐        │
│  │ FastAPI    │ │ Celery     │ │ APScheduler│ │ CLI/Batch  │        │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘        │
└────────┼──────────────┼──────────────┼──────────────┼────────────────┘
         │              │              │              │
┌────────▼──────────────▼──────────────▼──────────────▼────────────────┐
│                L3 编排层 (graph/ + agents/)                          │
│   LangGraph 6 节点: market → sector(s) → synth → stock(s) →        │
│                   committee → renderer                              │
│   - 状态机: market_regime / sector_stage / stock_tactical           │
│   - 委员会: 单一裁决者 (CommitteeSynthesizer)                       │
└────────┬─────────────────────────────────────────────────────────────┘
         │
┌────────▼─────────────────────────────────────────────────────────────┐
│             L2 路由层 (data_pipeline/data_router.py)                │
│  DataRouter — 策略层                                                 │
│  ┌──────────┐  ┌────────────┐  ┌──────────┐  ┌──────────────┐      │
│  │ method_  │  │ fallback   │  │ TTL      │  │ EndpointStats│      │
│  │ table    │  │ chain      │  │ cache    │  │ (p95/ok/err) │      │
│  └──────────┘  └────────────┘  └──────────┘  └──────────────┘      │
│                                                                      │
│  CORE_METHOD_TABLE  (28 端点, 默认)                                │
│  EXTENDED_METHOD_TABLE (8 端点, include_extended=True)              │
└────────┬─────────────────────────────────────────────────────────────┘
         │
┌────────▼─────────────────────────────────────────────────────────────┐
│              L1 Provider 层 (data_pipeline/providers/)               │
│  ┌─────────────────────┐  ┌──────────────────────┐                  │
│  │  AkshareProvider    │  │  AStockDataProvider  │                  │
│  │  - 28 端点          │  │  - 10 端点           │                  │
│  │  - DataFrame 返回   │  │  - list[dict] 返回   │                  │
│  │  - 重试 + 反爬补丁  │  │  - 细粒度 (mootdx/   │                  │
│  │                     │  │    腾讯/东财)        │                  │
│  └─────────────────────┘  └──────────────────────┘                  │
│                                                                      │
│  未来可加:  TushareProvider / BaoStockProvider / WindProvider ...    │
└────────┬─────────────────────────────────────────────────────────────┘
         │
┌────────▼─────────────────────────────────────────────────────────────┐
│              L0 协议层 (core/ + models/)                              │
│   Pydantic 契约 / SQLAlchemy 2.0 async / Alembic 迁移 /              │
│   UUID5 标识 / LLM 客户端工厂                                         │
└──────────────────────────────────────────────────────────────────────┘
```

### Quick Start

#### 1. 准备环境

```bash
git clone <repo-url>
cd A-Share-Research-Agent

# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# 安装依赖（按需选择）
pip install -e ".[dev]"              # 开发态
pip install -e ".[all]"              # 全功能（含 Celery/Redis/curl_cffi/mootdx）
```

#### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env：
#   DEEPSEEK_API_KEY=sk-...   # 或 OPENAI_API_KEY / QWEN_API_KEY
#   DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ashare
#   REDIS_URL=redis://localhost:6379/0
```

#### 3. 启动（Docker 一键化）

```bash
make docker-up        # 构建 + 启动 5 服务（postgres/redis/api/worker/flower）
make alembic-up       # 跑数据库迁移
```

#### 4. 验证

```bash
# 健康检查（轻量版）
curl 'http://localhost:8765/health'

# 健康检查（详情版）
curl 'http://localhost:8765/health?mode=full'

# 健康检查（端点视图）
curl 'http://localhost:8765/health?mode=endpoints'

# 实时个股分析
curl -X POST 'http://localhost:8765/v1/research/analyze_stock_live' \
     -H 'Content-Type: application/json' \
     -d '{"symbol": "000636"}'

# Flower (Celery 监控)
open http://localhost:5555   # 默认 admin/admin
```

#### 5. 跑测试

```bash
make test             # 全量 (~100 测试, 5-10s)
make test-fast        # 排除 e2e 集成测试
make test-cov         # 带覆盖率
```

### 数据源接入

#### 当前 provider 矩阵

| Provider | 端点数 | 数据源 | 返回值 | 适用场景 |
|----------|-------|--------|--------|----------|
| `AkshareProvider` | 28 | AkShare 聚合层 | DataFrame（ETL）/ list[dict]（聚合）| 批量入库 / 高级事件 |
| `AStockDataProvider` | 10 | mootdx + 腾讯 + 东财 | `list[dict]` / `dict` | 实时个股 facade |

#### 接入新数据源（3 步）

**步骤 1**：实现 `Provider` 类

```python
# data_pipeline/providers/tushare_provider.py
from typing import Any

class TushareProvider:
    """Tushare 数据源 — DataRouter 的 'tushare' provider。"""

    def __init__(self, token: str) -> None:
        import tushare as ts
        self._pro = ts.pro_api(token)

    async def get_kline(self, code: str, **kwargs) -> list[dict[str, Any]]:
        """Tushare 日线（与 a_stock_data 同 method 同语义）。"""
        import asyncio
        df = await asyncio.to_thread(
            self._pro.daily, ts_code=_to_tushare_code(code), **kwargs
        )
        return df.to_dict(orient="records")

    # ... 其他端点
```

**步骤 2**：注册到 `DataRouter`

```python
from data_pipeline.data_router import DataRouter
from data_pipeline.providers.tushare_provider import TushareProvider

router = DataRouter(
    providers={
        "akshare": AkshareProvider(),
        "a_stock_data": AStockDataProvider(),
        "tushare": TushareProvider(token="..."),
    },
    # method_table 可选：未指定时用 CORE；自定义时按你给的注册
    method_table={
        "get_kline": {
            "a_stock_data": "get_kline",   # 首选
            "tushare": "get_kline",          # fallback
        },
    },
)
```

**步骤 3**：验证健康度

```bash
curl 'http://localhost:8765/health?mode=endpoints'
# by_provider.tushare[0]  →  {fallback_count, success_rate, p95_latency_ms, ...}
```

#### 运行时动态注册

无需重启即可加端点（适合实验 / 多租户）：

```python
router = DataRouter(providers={"custom": MyProvider()}, method_table={})
router.register_method(
    "my_custom_method", "custom", attr="fetch", category="experimental"
)
# 立即可路由
result = await router.route("my_custom_method", ...)
```

### API 端点

| 端点 | 用途 | 模式 |
|------|------|------|
| `GET /health` | 健康检查 | `summary` (默认) / `full` / `endpoints` |
| `POST /v1/research/analyze_stock_live` | 实时个股分析 | 单只/批量 |
| `POST /v1/research/report` | 触发 6 节点研究流 | 按日期/板块 |
| `POST /v1/etl/close` | 收盘 ETL | 按日期 + 股票列表 |
| `POST /v1/rules/evaluate` | 规则引擎准入 | 单 symbol / 批量 |

### 端点分层（method_table）

```python
# CORE — 默认注册（28 端点）
DataRouter.CORE_METHOD_TABLE  # 实时 + ETL 高频

# EXTENDED — 按需启用（8 端点）
DataRouter.EXTENDED_METHOD_TABLE  # 财报三表 / 分钟线 / 高级事件 / 个股龙虎榜

# 用法
router = DataRouter(include_extended=True)
```

### 项目结构

```
A-Share-Research-Agent/
├── apps/                   # L4 业务层：API / worker / scheduler
├── agents/                 # L3+ 推理层：6 类 Agent
├── graph/                  # L3 编排：LangGraph
├── data_pipeline/          # L1/L2 数据层
│   ├── data_router.py      # ← 路由层
│   ├── cache/              # ← CacheBackend Protocol + 实现
│   └── providers/          # ← Provider 实现
│       ├── akshare_provider.py
│       ├── a_stock_data_provider.py
│       └── facade.py       # ← 统一入口 stock_live_context
├── features_engine/        # 状态机 / 特征计算
├── rule_engine/            # 3 层门禁
├── core/                   # L0 协议：Pydantic / DB / LLM
├── models/                 # SQLAlchemy ORM
├── tests/                  # pytest 套件（100+）
├── pyproject.toml          # ruff / mypy / pytest / 依赖分组
├── Makefile                # 一键化
└── docker-compose.yml      # 5 服务编排
```

### 测试与质量

```bash
make test         # 全量（~100 项）
make test-cov     # 覆盖率报告（htmlcov/index.html）
make lint         # ruff check
make fmt          # ruff format
```

测试矩阵：
- `test_data_router.py`  — 路由 / fallback / 缓存 / 端点统计（26 项）
- `test_cache.py`        — CacheBackend Protocol / InMemoryCache（12 项）
- `test_health.py`       — /health 三模式投影（5 项）
- `test_facade_via_router.py` — facade 集成（5 项）
- `test_assembler.py` / `test_scorer.py` — 状态推断（20+ 项）
- `test_committee_and_renderer.py` — 委员会 + 报告（8 项）
- `test_graph_e2e.py`    — 6 节点 e2e（2 项，需要外网）
- 其他 30+ 项

### 部署

```bash
# 本地开发
make run-api        # 单独跑 API（无 DB 也能起，但 /health 标记 degraded）
make run-worker     # 单独跑 Celery worker
make run-flower     # Celery 监控

# Docker
make docker-up      # 5 服务（postgres + redis + api + worker + flower）
make docker-down
make docker-logs

# 一次性
make etl-close DATE=2026-07-01 SYMS="000636,600519"
make research-live SYM=000636
```

### 项目状态

- **Beta**：核心功能完成，DataRouter / Cache / Health / Provider 均已抽象化。
- **生产就绪度**：单进程/单租户生产可用；多租户/分布式部署需接 RedisCache。
- **Roadmap**：见 [CHANGELOG.md](CHANGELOG.md) Unreleased 段。

### 贡献

参见 [CONTRIBUTING.md](CONTRIBUTING.md)。核心原则：

1. **数据真实**：禁止 mock 降级；端点失败必须显式抛错。
2. **零硬编码**：新端点走 `register_method()` 或 `method_table`，不改 router。
3. **测试先行**：新功能必带单测 + 集成测试；覆盖率不低于 80%。
4. **类型注解**：核心模块用 `list[dict[str, Any]]` 显式声明；公私边界清晰。

### License

[MIT](LICENSE) © 2026 A-Share Research Agent Contributors

---

## English

**A-Share Research Agent** — a production-grade A-share (China stock market) AI research platform.
Real data, multi-agent collaboration, and committee-based decisioning — engineered to be auditable, extensible, and unit-testable.

### Why this exists

- **No fake data**: every endpoint hits a real source (akshare, mootdx, Tencent, EastMoney, etc.).
- **Provider is the unit of extension**: add a new data source by implementing one class and registering it with `DataRouter`.
- **Explicit failure semantics**: failures raise `DataFetchError`; no silent fallback to mocks.
- **LangGraph 6-node pipeline**: market → sectors (parallel) → synthesis → stocks → committee → report.
- **Single decision-maker per layer**: the committee decides entry, not individual agents.

### Stack

Python 3.10+ · FastAPI · Pydantic v2 · SQLAlchemy 2.0 async · LangGraph · AkShare · mootdx · Celery · Redis · PostgreSQL 16

### License

[MIT](LICENSE)
