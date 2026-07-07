# A股 AI 投研中台系统 - 实施拆解与落地规划 (V1.0)

本文档基于 `system_design_v1.md` 的架构设计，将庞大的系统拆解为可落地的代码模块、开发阶段和具体任务，作为后续编码工作的直接指导蓝图。

## 1. 总体实施路线图 (Roadmap)

为了避免“大爆炸”式开发导致项目失控，我们将系统落地拆解为 5 个具有独立交付价值的 Milestone (里程碑)：

- **M1: 结构化底座 (Data & DB)**：完成数据库表结构创建，打通 AkShare 数据源，实现数据清洗入库。
- **M2: 特征与风控引擎 (Feature & Rule)**：实现市场总开关、板块状态机、游资战术标签的预计算，以及硬风控规则判定。
- **M3: Agent 契约与单体测试 (Agent & Schema)**：定义所有 Pydantic Schema，组装 Prompt 模板，打通 DeepSeek API，跑通单个 Agent。
- **M4: 核心工作流编排 (LangGraph Workflow)**：使用 LangGraph 将所有 Agent 按照时序和条件边串联，跑通 T+1 盘后批处理。
- **M5: 调度与服务化 (Celery & FastAPI)**：实现多时间窗调度，暴露对外 API，生成最终 Markdown 报告。

---

## 2. 分层落地规划与技术选型

### 2.1 数据源层 (Data Source Layer)
**目标**：将 AkShare 等异构数据统一为内部标准格式并落库。
**核心模块**：
- `collectors/akshare_fetcher.py`：封装 AkShare 接口（带重试、并发控制、防爬虫机制）。
  - **基础行情**：日K、分钟K (`stock_zh_a_hist`, `stock_zh_a_hist_min_em`)
  - **资金流向**：北向、主力、ETF资金流
  - **短线生态**：涨停池、跌停池、炸板池 (`stock_zt_pool_em` 等)
  - **游资龙虎榜**：每日明细、活跃营业部 (`stock_lhb_detail_em` 等)
- `loaders/db_sync.py`：使用 SQLAlchemy 将清洗后的 `DataFrame` 批量 upsert 进 PostgreSQL。

### 2.1.1 除 AkShare 外的数据源采购/爬取矩阵

AkShare 可以承担 MVP 阶段的大量基础任务，但它不是全能入口。若系统要真正支撑 A 股投研、板块主线识别、游资战术信号和政策事件理解，至少还需要补齐以下数据源：

| 优先级 | 数据域 | 必要性 | 推荐来源 | 建议方式 | 用途 |
|---|---|---|---|---|---|
| P0 | 交易所公告/巨潮公告 | 必须 | 巨潮资讯、上交所、深交所、北交所 | 先爬取，后续可接商业公告API | 财报、业绩预告、减持、解禁、问询函、停复牌、异动公告 |
| P0 | 政策与权威新闻 | 必须 | 国务院、证监会、央行、发改委、工信部、新华社 | 官网抓取 + RSS/结构化清洗 | 宏观和产业政策解读、制度变化、监管口径 |
| P0 | 国际市场与大宗商品 | 必须 | 新浪财经、Investing、Alpha Vantage、Polygon、Wind/Choice | 先免费API/抓取，后补商业API | 美股、美元指数、原油、黄金、铜、港股、日韩市场联动 |
| P0 | 板块/概念/成分股映射 | 必须 | 同花顺、东方财富、申万行业、Wind 行业库 | 优先买库或稳定抓取 | 板块生命周期、概念纯度、卡位/补涨识别 |
| P1 | 资金流/L2增强 | 强烈建议 | 东方财富、同花顺、Wind、Choice、通联/聚源 | 优先买API | 北向、行业资金流、主力净流入、ETF净申赎、盘口增强 |
| P1 | 研报与券商观点 | 强烈建议 | 东方财富研报、慧博、券商官网 | 半结构化抓取 | 机构预期变化、卖方一致性、行业催化梳理 |
| P1 | 舆情与情绪 | 强烈建议 | 股吧、财联社、电报、微博、雪球 | 爬取 + 清洗 + 去重 | 题材热词、市场情绪、事件传播速度 |
| P1 | 龙虎榜席位知识库 | 强烈建议 | 东方财富龙虎榜、公开资料、社区整理 | 自建知识库 | 识别知名游资风格、一日游/锁仓/低吸/打板 |
| P2 | ETF 份额/申赎 | 建议 | 基金公司公告、天天基金、Wind、Choice | 优先买，辅以爬取 | 国家队代理、宽基托底、风格切换 |
| P2 | 基金持仓/公募季报 | 建议 | 基金定期报告、天天基金、Wind/Choice | 爬取公告或采购 | 机构抱团、隐形主线、中军验证 |
| P2 | 融资融券/股东户数 | 建议 | 交易所、东方财富、Wind/Choice | 采购优先 | 杠杆情绪、筹码松动、拥挤度辅助判断 |
| P3 | 逐笔成交/盘口委托 | 可选增强 | 通达信L2、同花顺iFinD、Wind L2 | 采购 | 若以后做盘中实时战术模型可接入 |

### 2.1.2 推荐的采购策略

不是所有数据都值得一开始就花钱买。建议分三层推进：

1. **MVP 阶段（先开工）**
   - `AkShare + 巨潮/交易所公告抓取 + 政府官网/新华社政策抓取 + 免费国际行情API`
   - 这一层已经足够支撑：市场总开关、板块生命周期、涨停板生态、龙虎榜、基础事件分析。

2. **增强阶段（最值得花钱）**
   - 优先采购：`板块/概念成分映射`、`资金流/ETF申赎`、`稳定国际行情`
   - 原因：这三类最影响系统上限，尤其是板块归属准确性和国家队代理指标准确性。

3. **专业阶段（做深做强）**
   - 再采购：`研报库`、`基金持仓`、`L2逐笔`
   - 这些适合在前面主干跑通后再接，不然很容易陷入“数据很多但主流程没跑起来”。

### 2.1.3 我建议你优先去梳理的供应商清单

如果你现在就准备买或爬，我建议按下面这个顺序做供应商排查：

1. **公告和监管**
   - 巨潮资讯
   - 上交所、深交所、北交所官网
   - 证监会官网

2. **政策和权威新闻**
   - 国务院
   - 央行
   - 发改委
   - 工信部
   - 新华社
   - 财联社

3. **板块与概念**
   - 同花顺概念库
   - 东方财富概念/行业页
   - 申万行业分类

4. **资金和ETF**
   - 东方财富资金流
   - 天天基金
   - 基金公司官网公告
   - Wind / Choice / iFinD（如果预算允许）

5. **国际行情**
   - Alpha Vantage
   - Polygon
   - 新浪财经/Investing
   - Wind / Choice（如要稳定性）

### 2.1.4 采购判断标准

你在筛 API 或爬虫目标时，不要只看“有没有数据”，要看下面 6 个指标：

- `字段稳定性`：字段名和页面结构是否经常变。
- `历史可回补性`：能不能拉历史，不然回放和补数很痛苦。
- `时间戳可信度`：新闻发布时间、公告发布时间是否准确到分钟。
- `反爬强度`：验证码、频控、Cookie 依赖强不强。
- `版权/合规风险`：是否适合长期生产使用。
- `覆盖完整度`：是否包含北交所、ETF、转债、ST 等边角数据。

### 2.2 指标加工层 (Feature Engineering Layer)
**目标**：将 Raw Data 转化为 6.6 和 6.7 节定义的慢变量和快变量特征。
**核心模块**：
- `features/market_features.py`：计算 `feature_market_snapshot`（风险偏好、流动性、炸板率、国家队代理分数等）。
- `features/sector_features.py`：计算 `feature_sector_snapshot`（板块成交占比、梯队完整性、赚钱效应扩散等）。
- `features/stock_tactical.py`：计算游资战术标签（龙头首阴、反包、弱转强、地天板等）。
- `features/snapshot_builder.py`：组装特征，为 Agent 准备 JSON 格式的 Context。

### 2.3 规则引擎层 (Rule Engine Layer)
**目标**：在调用 LLM 前做硬逻辑阻断，避免幻觉。
**核心模块**：
- `rules/market_gates.py`：判断是否触发全市场熔断（`risk_off_panic`）。
- `rules/stock_gates.py`：ST 过滤、异常停牌过滤、减持解禁预警判定。
- `rules/engine.py`：统一的规则执行器，输出结构化的 `fact_rule_hit`。

### 2.4 Agent 与工作流层 (Agent & Workflow Layer)
**目标**：Pydantic 驱动的结构化 LLM 推理。
**核心模块**：
- `core/schemas/`：根据 10.x 节，严格定义 `MarketOutlook`, `SectorOutlook`, `SecurityStrategy` 等 Pydantic Model。
- `prompts/`：按角色切分 jinja2 或 f-string 模板，强制注入特征与规则。
- `agents/nodes.py`：实现 20+ 个 Analyst 和 Synthesizer 函数，封装调用 DeepSeek 结构化输出的代码。
- `graph/workflow.py`：使用 `langgraph.graph.StateGraph` 定义边和条件路由（如遇到 `risk_off_panic` 直接跳过进攻节点）。

### 2.5 批量调度设计 (Batch Scheduling)
**目标**：实现 7.1 节定义的 5 个时间窗自动化运行。
**核心模块**：
- `apps/worker/tasks.py`：定义 Celery 任务。
- `apps/batch/scheduler.py`：配置 Celery Beat 定时触发：
  - `15:30` 触发收盘后快照与计算。
  - `20:00` 触发晚间公告与龙虎榜计算。
  - `08:30` 触发盘前策略生成。

---

## 3. 项目目录结构规划 (Project Layout)

在 `A-Share-Research-Agent/` 根目录下，建立以下标准 Python 工程结构：

```text
A-Share-Research-Agent/
├── pyproject.toml               # Poetry 或 pip 依赖管理
├── .env                         # 环境变量配置 (DB, LLM API Keys)
├── alembic.ini                  # 数据库迁移配置
├── apps/
│   ├── api/                     # FastAPI 接口层 (路由, 依赖注入)
│   │   └── main.py
│   ├── batch/                   # 调度器配置 (Celery Beat)
│   └── worker/                  # 异步任务处理 (Celery Worker)
├── core/
│   ├── config.py                # 全局配置中心 (Pydantic BaseSettings)
│   ├── database.py              # SQLAlchemy 引擎与 Session 管理
│   ├── llm_client.py            # 封装 DeepSeek 等 OpenAI 兼容接口
│   └── logger.py                # 结构化日志配置
├── models/                      # SQLAlchemy ORM 实体 (对应 15/16 章表结构)
│   ├── base.py
│   ├── dimensions.py            # dim_security, dim_sector
│   ├── facts.py                 # fact_market_bar, fact_event
│   ├── features.py              # feature_*_snapshot
│   └── outputs.py               # fact_strategy_output, fact_rule_hit
├── schemas/                     # Pydantic 契约 (对应 10 章与 Agent I/O)
│   ├── agent_io.py              # 各 Analyst 的输入输出结构
│   └── final_outputs.py         # MarketOutlook, SectorOutlook 等
├── data_pipeline/               # 数据采集与标准化 (ETL)
│   ├── akshare_client.py
│   ├── extractors/
│   └── sync_jobs.py
├── features_engine/             # 指标加工与特征组装
│   ├── calculators/             # 数学指标、战术标签计算逻辑
│   └── snapshot_mgr.py          # 快照生成器
├── rule_engine/                 # 硬风控与规则过滤
│   ├── gates/
│   └── evaluator.py
├── agents/                      # LLM Agent 节点实现
│   ├── market_agents.py
│   ├── sector_agents.py
│   ├── stock_agents.py
│   ├── debate_agents.py
│   └── pm_agents.py
├── prompts/                     # Prompt 模板库
│   ├── system_prompts/
│   └── templates/
├── graph/                       # LangGraph 编排
│   ├── state.py                 # ResearchWorkflowState 定义
│   └── research_graph.py        # 节点与边组装
└── tests/                       # 单元与集成测试
```

---

## 4. 第一阶段 (M1) 开发任务清单

我们首先要让地基立起来，具体任务如下：

1. **脚手架初始化**：
   - 创建目录结构。
   - 初始化 `pyproject.toml` (依赖: `sqlalchemy`, `asyncpg`, `fastapi`, `langgraph`, `langchain-openai`, `pydantic`, `akshare`, `pandas`)。
   - 初始化 `core/config.py` 和 `core/database.py`。
2. **核心 ORM 定义**：
   - 在 `models/` 下使用 SQLAlchemy 编写 `dim_security`, `feature_market_snapshot`, `feature_sector_snapshot`, `feature_stock_snapshot` 的表结构模型。
   - 配置 Alembic 生成初始迁移脚本。
3. **数据采集联通**：
   - 编写 `data_pipeline/akshare_client.py`，实现获取 A 股所有股票列表、当日涨停板池、龙虎榜的基础代码，并能将 `DataFrame` 映射写入数据库。
