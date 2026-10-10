# A 股研究智能体系统 — 架构设计文档

- 日期：2026-08-27（2026-08-28 大方向深化：架构原则/韧性体系/标准规范/演进路径）
- 状态：设计稿（待评审，未开工）
- 基座：`datasource/`（24 provider / 89 API，已审核夯实）
- 提示词参考：`TradingAgents-CN`（`tradingagents/agents/` 角色设计）

---

## 1. 已确认的决策

| 决策点 | 结论 |
|---|---|
| 运行模式 | 批量报告式（定时/手动）+ 支持临时指定标的 |
| LLM 接入 | 多模型分层路由（ModelRouter：任务类型 → 模型档位） |
| 智能体编排 | Pi 为主（pi-ai 多模型 + Skills）+ Python 批处理流水线 |
| 提示词设计 | 参考 TradingAgents-CN 的角色范式（见 §7） |

## 2. 总体架构

### 2.1 六层架构

```
┌─────────────────────────────────────────────────────────────┐
│ L5 输出层 renderer/        md/html 渲染 + 图标体系 + 归档      │
├─────────────────────────────────────────────────────────────┤
│ L4 评测层 evaluator/       事实核查 + 质量评分 + 模型横评      │
├─────────────────────────────────────────────────────────────┤
│ L3 智能体层 agents/        大盘/板块/事件/大宗/个股 解读智能体   │
│    ├─ 独立解读智能体（并行扇出，互不依赖）                      │
│    └─ 红蓝对抗（多/空辩论 → 裁判合并）                         │
├─────────────────────────────────────────────────────────────┤
│ L2 指标层 indicators/      指标计算（纯代码）+ LLM 二次加工     │
├─────────────────────────────────────────────────────────────┤
│ L1 数据源层 datasource/    ✅ 已有：24 provider / 89 API      │
├─────────────────────────────────────────────────────────────┤
│ 横切基座 foundation/       ModelRouter + 编排器 + 产物存储     │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 数据流（批处理流水线）

```
触发（定时 / CLI 指定标的）
  → Orchestrator 按 DAG 调度
  → L1：route() 并发拉取原始数据（复用韧性/缓存/多源 fallback）
  → L2：纯代码计算指标 → 产出结构化「指标包」IndicatorPack(JSON)
  → L3：解读智能体并行消费指标包 → 红蓝对抗 → 裁判合并 → 结论包
  → L4：事实核查（结论数字溯源到指标包）+ 质量评分 → 修订或打回
  → L5：md + html 渲染 → reports/YYYY-MM-DD/ 归档
```

### 2.3 目录布局

```
A-Share-Research-Agent/
├── datasource/                 # L1 已有
├── foundation/                 # 横切基座
│   ├── llm/                    #   ModelRouter + Provider 适配 + 结构化输出
│   ├── orchestrator/           #   DAG 编排器 + 断点续跑
│   └── store/                  #   每层产物落盘（JSON）
├── indicators/                 # L2 指标层
├── agents/                     # L3 智能体层
│   ├── prompts/                #   提示词模板（YAML/MD）
│   ├── interpreters/           #   大盘/板块/事件/大宗/个股
│   └── debate/                 #   红蓝对抗 + 裁判
├── evaluator/                  # L4 评测层
├── renderer/                   # L5 输出层
├── pipelines/                  # 流水线组装（daily_report / stock_report）
├── reports/                    # 输出归档（.gitignore）
├── skills/                     # 已有（+ 未来 pi skills）
└── docs/designs/               # 本文档
```

### 2.4 三条架构铁律

1. **指标包是智能体的唯一数据入口**：智能体不直接碰数据源层，只消费指标包。
   收益：智能体可离线回放测试（历史指标包重跑）、prompt 迭代不重拉数据。
2. **每层产物全部落盘**：`reports/<date>/artifacts/<layer>/<name>.json`。
   任何一层失败可从上一层产物续跑，天然形成审计链。
3. **双入口共享一套核心**：定时报告（Python 流水线）与临时问股（Pi 交互）
   共享同一套指标层、提示词与模型路由，结论口径一致。
   新鲜度约定：临时问股默认复用最近一个交易日的指标包；盘中需实时数据时，
   按需生成单标的迷你指标包（仅 stock 分组 + 实时行情），复用同一 schema。

### 2.5 架构设计原则（可推敲性）

1. **单向依赖，层间零回调**：依赖方向固定 L5→L4→L3→L2→L1，下层不得 import 上层；
   横切基座（foundation）被所有层依赖但不反向依赖任何层（唯一例外：
   foundation/registry 校验期读取 L1 路由表 method_table，用于 YAML 与实现一致性 fail-fast，
   方向仍单向：datasource 零 foundation 依赖）。层间唯一共享物是
   pydantic schema（指标包/结论包/ReportIR）。
2. **产物驱动解耦**：层与层之间不直接调用对方内部，只通过 store 落盘的结构化产物
   传递数据（见 §3.4）。收益：任一层可独立替换、独立测试、离线回放；
   断点续跑与审计链天然成立。
3. **受限局部循环**：流水线主结构是无环 DAG，仅允许一处受控回路
   （评测层打回 → 原智能体重跑，上限 1 轮）；打回次数记入产物，防止无限循环。
4. **幂等重放**：所有节点满足"同输入 + 同配置 → 同产物"（LLM 节点以
   temperature 固定 + 冻结指标包近似保证），重跑覆盖而不叠加——
   这是断点续跑与回放测试科学性的共同基础。
5. **schema 版本化**：指标包/结论包/ReportIR 均带 `schema_version` 字段；
   升级策略：新增字段向后兼容，破坏性变更升版本号并保留旧版本解析器。
6. **演进缝隙**：单机批处理为起点，但并发模型（信号量分池）、产物存储
   （目录即数据库）、配置（命名空间隔离）均按"未来可平滑迁移到常驻服务/队列"
   预留，不为此提前引入分布式组件（见 §14）。

---

## 3. 横切基座

### 3.1 ModelRouter（多模型分层路由）

```python
class ModelTier(str, Enum):
    FAST = "fast"        # 抽取/清洗/摘要（新闻条目归类、指标二次加工）
    ANALYST = "analyst"  # 解读/对抗论证（主力消耗）
    JUDGE = "judge"      # 裁判/汇总（最强推理，决定报告质量上限）
    EVAL = "eval"        # 评测层核查（便宜模型即可，规则为主）

class ModelRouter:
    async def chat(self, tier: ModelTier, messages, *,
                   schema: type[BaseModel] | None = None,   # pydantic 结构化输出
                   max_retries: int = 3) -> str | BaseModel: ...
```

- 底层用 OpenAI 兼容 API（DeepSeek 官方端点原生兼容），`OPENAI_BASE_URL` 可切任意供应商。
- 每个 tier 独立配置 `{provider, model, temperature, max_tokens, timeout}`，
  配置走 `.env`（沿用 datasource 的 `ROUTE_*` 命名风格：`LLM_FAST_MODEL` 等）。
- 结构化输出：优先用模型原生 JSON mode / response_format，失败降级为
  "JSON 提取 + pydantic 校验 + 重试"。
- 成本护栏：单次流水线总 token 预算；**预算检查在每次 LLM 调用前基于累计已消耗
  token 执行**，超限后的首次调用即降级（JUDGE 档 → ANALYST 档）并告警。
- 调用留痕：每次调用记录 `{tier, model, tokens, latency, cost}` 到流水线产物目录，
  与 `.call_logs` 风格一致。

**默认档位建议**（可在 `.env` 覆盖）：

| Tier | 默认模型 | 理由 |
|---|---|---|
| FAST | deepseek-chat | 便宜、快，抽取类任务足够 |
| ANALYST | deepseek-chat | 中文金融解读主力 |
| JUDGE | deepseek-reasoner | 裁判需要强推理；成本可控（调用次数少） |
| EVAL | deepseek-chat | 规则核查为主，模型兜底 |

### 3.2 Orchestrator（编排器）

- 轻量自研（延续 datasource 风格）：`asyncio` + 声明式 DAG，不引入重框架。
- 节点契约：`async def node(ctx: PipelineContext) -> NodeResult`，
  每个节点声明 `depends_on`；无依赖节点并行执行（信号量限并发）。
- **断点续跑**：节点执行前查产物文件，存在且产物 `trade_date` 与本次运行一致则跳过
  （`--force` 强制重跑）。
- **失败策略**：节点级 `critical: bool`。数据拉取类单源失败可降级（数据源层已自动
  fallback）；解读智能体失败 → 该板块标注"数据不可用"继续出报告；汇总节点失败 → 整体失败。

### 3.3 指标包（IndicatorPack）

智能体的唯一输入。统一结构（pydantic 定义，每字段带 `as_of` 时间戳与来源标注）：

```json
{
  "meta": {"generated_at": "...", "trade_date": "...", "scope": "market|sector|stock", "symbols": []},
  "data_quality": [{"source": "eastmoney", "method": "get_market_fund_flow", "status": "ok|empty|failed"}],
  "market":    {"indices": {...}, "breadth": {...}, "limit_stats": {...}, "fund_flow": {...}, "northbound": {...}},
  "sectors":   [{"name": "...", "pct_change": ..., "fund_flow": ..., "lead_stock": "..."}],
  "events":    [{"category": "policy|macro|company|geopolitics", "title": "...", "source": "...", "ts": "..."}],
  "blocks":    [{"symbol": "...", "price": ..., "volume_ratio": ..., "buyer": "..."}],
  "stock":     {"bars": [...], "indicators": {...}, "fundamentals": {...}, "news": [...]}
}
```

`data_quality` 字段是关键：解读智能体必须知道哪些数据缺失，**禁止脑补缺失数据**。

### 3.4 产物存储（store）—— 层间解耦枢纽

- 目录即数据库：`reports/<date>/artifacts/<layer>/<name>.json`，
  每个产物带元信息头 `{schema_version, trade_date, produced_by, generated_at}`。
- 层间通信唯一通道：上游层写产物 → 下游层读产物，**不传内存对象、不互相 import**；
  编排器只负责调度与传递产物路径。
- 写入原子性：沿用 datasource FileCache 已验证模式（tmp + os.replace），杜绝半写产物。
- 生命周期：保留最近 N 个交易日（默认 30，配置项 `artifact_retention_days`），
  过期清理；回放测试用的 golden set 冻结产物另存 `tests/fixtures/`，不受清理影响。

---

## 4. L2 指标层

### 4.1 纯代码指标（零 LLM，确定性，可单测）

| 分组 | 指标 | 数据源方法 |
|---|---|---|
| 市场广度 | 涨跌家数、涨停/跌停/炸板数、连板梯队 | `get_limit_up_pool` / `get_limit_down_pool` / `get_bomb_board_pool` |
| 指数 | 上证/深成/创业板/沪深300 收盘与涨跌幅、均线位置 | `get_index_daily_bars` |
| 资金 | 主力净流入、北向资金、板块资金流 | `get_market_fund_flow` / `get_northbound_flow` / `get_sector_fund_flow` |
| 板块 | 行业/概念涨跌排名、领涨股、板块资金 | `get_industry_ranking` / `get_concept_board_list` |
| 技术 | MA5/10/20/60、MACD、RSI、布林带、量比（pandas 计算） | `get_daily_bars` |
| 大宗 | 折溢率、成交规模、买方席位聚合 | `get_dragon_tiger`（a_stock_data，单源无 fallback，失败标 `failed`） |
| 情绪 | 新闻条数分布、热点词频（规则统计） | `get_news_flash` / `get_stock_news` |

### 4.2 LLM 二次加工（FAST 档，可选开关）

- 新闻条目分类与打标（政策/宏观/公司/地缘 → `events` 分组）；
- 公告标题摘要与影响方向初判（利好/利空/中性，仅供事件智能体参考）。
- 设计原则：**只做归类与抽取，不做观点**。观点全部留给智能体层。

### 4.3 失败语义

沿用数据源层：事件类端点空结果是合法业务语义（如无龙虎榜），`data_quality` 标 `empty`；选择器类端点（行业排行/板块资金流等）空结果在数据源层视为失败并继续 fallback，全链空归入 `failed`；
全链失败标 `failed`，指标包照常产出（缺什么标什么），由智能体层声明"数据不可用"。

---

## 5. L3 智能体层

### 5.1 解读智能体清单（相互独立、并行扇出）

| 智能体 | 输入（指标包分组） | 输出 | 对抗 |
|---|---|---|---|
| 大盘解读 | market + sectors + events | 市场状态/风格判断/关键矛盾 | ✅ 红蓝 |
| 板块解读 | sectors + 板块资金 | 主线识别/轮动方向/风险板块 | ✅ 红蓝 |
| 事件解读 | events（LLM 已打标） | 事件影响链/受益受损方向 | ❌（陈述+推演） |
| 大宗解读 | blocks + 龙虎榜 | 资金意图/席位行为/折溢价信号 | ❌（陈述+推演） |
| 个股解读（指定标的时） | stock + sectors + events | 多维度结论 + 目标价情景 | ✅ 红蓝 |

**声明式智能体：AgentSpec + AgentRuntime（框架干净的核心约束）**

智能体本身是**纯声明**（AgentSpec），不含任何执行代码：

| AgentSpec 字段 | 说明 |
|---|---|
| `prompt` | YAML 模板引用（§7 规范） |
| `output_schema` | pydantic 结论包 schema |
| `inputs` | 消费的指标包分组清单 + 各分组裁剪上限 |
| `tier` | 声明所需 ModelTier；**不持有 ModelRouter**（依赖倒置，由 runtime 注入调用客户端，可 mock、可计费、可回放） |
| `debate` | 是否入对抗（入者以 stance 参数实例化红蓝两份，见 §5.2） |

全部执行逻辑收敛在共享运行时 AgentRuntime，**只写一次、所有智能体复用**：
`输入装配（按 inputs 裁剪）→ 提示词渲染 → LLM 调用（结构化输出）→ 自检 → 落盘`。
自检 = 规则级校验（pydantic 校验 + 必填字段检查），失败携带错误信息重试 1 次，
仍失败走 §9.6 矩阵。**新增一个解读智能体 = 新增一份 YAML + 一个 schema，零新代码**。

输入裁剪规则（token 控制的关键）：
- 原始 K 线**不整体进 prompt**：MA/MACD/RSI/布林等以指标层计算结果进包；
- 原始 bars 默认仅保留最近 20 根（配置项 `bars_in_prompt`），新闻/公告按条数上限截断；
- 各分组设 token 预算上限，超限先截断低优先级字段。

### 5.2 红蓝对抗协议（参考 TradingAgents-CN 辩论设计）

```
Round 1: Bull 立论（指标包 + 立场要求）
Round 2: Bear 立论 + 反驳（看到 Bull 论点）
Round 3: Bull 反驳（看到 Bear 论点）
Judge:   裁判合并（JUDGE 档，强制明确结论）
```

轮数语义（配置项 `debate_rounds`，范围 1-4）：
- 表示**总发言次数**，Bull 起手、红蓝交替；默认 3（Bull×2 + Bear×1）；
- `debate_rounds=1` 退化为 Bull 单方陈述，不构成对抗，直接走 §9"未经对抗"降级路径；
- 奇数轮 Bull 收尾、偶数轮 Bear 收尾；裁判提示词硬性约束
  **"不得因发言顺序（最后印象）加权裁决"**。

- 双方独立消费**同一份指标包**，保证事实基础一致，分歧只能来自解读。
- Bull/Bear **不设独立智能体**：由入对抗的解读智能体以 `stance=bull|bear`
  参数化实例化两份 prompt（论证骨架沿用 TradingAgents-CN 四段结构，
  消除复制品）；对抗轮换与 history 拼接逻辑属于 debate 编排器，不进智能体 prompt。
- 辩论历史拼接传递（沿用 `history` / `current_response` 模式）。
- 裁判输出结构化：`{stance: bullish|bearish|neutral, confidence: 0-1, key_arguments: [],
  risks: [], conclusion: str}` —— **禁止"双方都有道理"式骑墙**（裁判提示词硬性约束）。
  `neutral` 是合法结论但须满足触发条件：双方关键论据均未被对方推翻且信号相互抵消，
  并在 `key_arguments` 中列明依据；不得作为默认逃生舱。

### 5.3 智能体间协作原则

- 各解读智能体**互不依赖、不共享中间状态**——只共享指标包（事实层）。
- 汇总由评测层之后的"总编辑"步骤完成（见 §6），不在智能体层做跨智能体传话。

---

## 6. L4 评测层 与 总编辑

### 6.1 事实核查（规则为主，EVAL 档兜底）

- **数字溯源**（分两级，承诺与机制对齐）：
  - 结构化字段中的数字：逐一机检溯源，指标包中找不到对应字段 → 打回；
  - 自由文本结论中的数字：正则抽取 + 单位归一（亿/万/%）后与指标包模糊匹配，
    未命中 → 标记 `hallucination_risk` 强制修订；无法数值化的表述（如"创新高"）
    不参与机检，由质量评分的"事实准确性"维度覆盖。
- **结论完备性**：裁判必须给出 stance + confidence，缺失即打回。
- **格式合规**：pydantic schema 校验输出结构。

### 6.2 质量评分

五维打分（0-5）：事实准确性 / 论据充分性 / 结论明确性 / 可操作性 / 语言质量。
评分结果写入报告附录（透明化），并累积为模型横评数据集（同指标包多模型对比）。

### 6.3 总编辑（汇总层）

输入：全部解读智能体结论包 + 对抗裁决 + 质量评分。
输出：统一报告结构（执行摘要 → 大盘 → 板块 → 事件 → 大宗 → 个股 → 风险提示）。
职责：消解跨板块矛盾（如大盘看多但板块提示风险 → 明确表述分歧而非抹平）、
统一术语与语气、生成执行摘要。**不做新观点**，只综合与编辑。

---

## 7. 提示词设计（参考 TradingAgents-CN）

从 TradingAgents-CN 提炼并沿用的设计要素：

| 要素 | 来源 | 本系统的落法 |
|---|---|---|
| 角色五维专业领域定义 | `china_market_analyst.py`（A股独特性/政策/轮动/监管/情绪） | 每个解读智能体的 system 段 |
| 对抗四段论证结构 | `bull/bear_researcher.py`（潜力·优势·指标·反驳 + 对话式辩论） | 红蓝双方提示词骨架 |
| 裁判反骑墙约束 | `research_manager.py`（禁止默认持有、必须承诺立场） | Judge 提示词硬约束 |
| 强制具体数值 | `trader.py`（目标价禁止 null、置信度/风险分 0-1） | 结构化输出 schema 强制 |
| 历史反思注入 | `memory.get_memories(n_matches=2)` 模式 | v2 再引入（见 §11 分期） |
| 市场/货币单位提醒 | `⚠️ 重要提醒：...使用人民币（¥）` | 模板变量统一注入 |
| 协作谦逊声明 | "无法完全回答没关系，其他分析师会补充" | 保留，防单智能体硬编 |
| 末尾结构化总结 | "报告末尾附 Markdown 表格总结" | 升级为强制 JSON 结构化输出 |

**对 TradingAgents-CN 的改进**：
1. 原版提示词内嵌在代码中 → 本系统提示词外置为 `agents/prompts/*.yaml`
   （system/user 分段 + 变量声明 + 版本字段），可 diff、可 A/B、可回放。
2. 原版输出自由文本 → 本系统强制 pydantic 结构化输出（评测层可机检）。
3. 原版数据靠工具实时查 → 本系统只喂指标包（事实可溯源、可回放）。

**个股解读智能体提示词骨架（示例）**：

```yaml
# agents/prompts/stock_interpreter.yaml
version: 1
system: |
  你是一位专业的 A 股个股研究分析师。你的专业领域：
  1. 基本面：结合中国会计准则与财报特点（ROE/毛利率/现金流质量）
  2. 技术面：均线系统、量价关系、支撑阻力
  3. 资金面：主力/北向/龙虎榜席位行为
  4. 政策与事件：监管变化、行业政策对该股传导
  5. 市场情绪：板块热度、题材生命周期
  你只依据【指标包】中的事实分析，禁止编造数据；指标包缺失的部分明确声明"数据不可用"。
user: |
  分析日期：{trade_date}；标的：{symbol} {name}。
  【指标包】{indicator_pack}
  请输出结构化结论（schema: StockInterpretation）。
```

---

## 8. L5 输出层

### 8.1 Markdown 报告

- 结构：标题 → 执行摘要（≤300字）→ 大盘/板块/事件/大宗/个股分章 → 红蓝对抗摘要 →
  风险提示 → 附录（数据质量表 + 质量评分 + 免责声明）。
- 图标体系：emoji 内联（📈 涨 / 📉 跌 / ⚖️ 震荡 / 🔴 风险 / 🟢 机会 / 💰 资金 / 📰 事件 /
  ⚔️ 对抗 / 🏛️ 政策），无需外部资源，任何 md 渲染器可用。
- 涨跌配色在 md 中用 `+红/-绿` 约定文本标注（中国习惯：红涨绿跌）。

### 8.2 HTML 报告

- 模板引擎：Jinja2（单文件模板 + 内联 CSS，零 JS 依赖，离线可开）。
- 与 md 同源：共用同一份"报告中间结构"（ReportIR，pydantic），md/html 各自渲染。
- 增强：涨跌色渲染、分章折叠、指标表格样式、对抗双方左右分栏。

### 8.3 归档

`reports/YYYY-MM-DD/{daily_report.md, daily_report.html, stock_<code>.*, artifacts/}`，
`reports/` 加入 `.gitignore`。

---

## 9. 高可用与韧性体系

本系统定位为**单机批处理流水线**，高可用目标不是分布式容灾，而是：
单次运行尽力产出、失败可恢复、全程可审计。为此不引入消息队列/数据库/常驻服务
（YAGNI，取舍见 §14），复用数据源层已验证的韧性原语并向上延伸。

### 9.1 三级降级体系

| 级别 | 范围 | 机制 |
|---|---|---|
| L1 源级 | 单数据源 | 多源 fallback + 熔断/重试/限流（datasource 已有，已审核夯实） |
| L2 板块级 | 单个解读智能体 | 重试 2 次 → 该板块标注失败，其余照常 |
| L3 报告级 | 整份报告 | 任何 L2 降级都不阻塞出报告；仅汇总/渲染节点失败才整体失败 |

### 9.2 超时预算分配

整体 deadline（默认 30 分钟，配置项）→ 按层预分配：数据拉取 ≤40%、指标计算 ≤5%、
智能体层 ≤40%（含对抗）、评测+渲染 ≤15%；层级预算再下分到节点。
节点超时行为受 §9.6 矩阵约束，不无限重试。

### 9.3 资源隔离与并发控制（bulkhead 分池）

- 数据拉取、LLM-FAST、LLM-ANALYST、LLM-JUDGE 各自独立信号量池，互不挤占
  （JUDGE 裁判调用不会被 FAST 批量抽取饿死）；
- 流水线级全局并发上限（默认 8），防本机资源耗尽；
- LLM 供应商侧限流沿用 datasource rate_limit 令牌桶模式。

### 9.4 幂等与恢复

- 产物写入原子化（tmp + replace），重跑覆盖不叠加（见 §2.5-4）；
- `--resume` 按产物存在性跳过已完成节点；`--force` 强制全量重跑；
- 进程崩溃后重启无脏状态（内存态仅存在于单节点执行期内）。

### 9.5 可观测性

- **结构化日志**：每次运行一份 run log（JSONL），节点级
  `{node, status, start, end, tokens, artifact_path}`；
- **产物即留痕**：每层产物含 produced_by/generated_at；报告附录透出数据质量表
  与模型调用统计（与 `.call_logs` 风格一致）；
- **运行小结与告警**：批量运行结束输出 run summary（成功/降级/失败清单 + token 消耗）；
  失败时非零退出码，供定时任务感知。

### 9.6 失败降级矩阵

| 故障点 | 行为 |
|---|---|
| 单数据源失败 | 数据源层自动多源 fallback（已有能力） |
| 某类数据全链失败 | 指标包 `data_quality=failed`，智能体声明"数据不可用"，报告照常出 |
| 单个解读智能体失败/超时 | 重试 2 次 → 该板块标注失败，其余板块正常 |
| 红蓝对抗某方失败 | 降级为单方陈述 + 裁判注明"未经对抗" |
| 事实核查发现幻觉 | 由原智能体携带核查反馈重跑一轮 → 仍不通过则删除该论断并在报告标注 |
| LLM 预算耗尽 | 档位降级（JUDGE→ANALYST）+ 报告附录注明 |
| 流水线任意节点崩溃 | 产物已落盘，`--resume` 从断点续跑 |

---

## 10. 测试策略

1. **指标层单测**：纯代码指标用固定样本 DataFrame 断言（无网络）。
2. **回放测试**：冻结 3 个历史指标包（大涨日/大跌日/震荡日，存
   `tests/fixtures/indicator_packs/`）作为 golden set，
   prompt 每次修改跑回放，对比结论稳定性与格式合规率。
3. **事实核查自测**：构造含错误数字的结论样本（结构化字段 + 文本中精确出现的数字），
   验证核查器 100% 拦截。
4. **渲染快照**：ReportIR → md/html 输出做字符串快照测试。
5. **端到端冒烟**：`pipelines/daily_report.py --smoke`（小样本、跳过部分慢接口）。
6. **模型横评**：评测层累积的评分数据支持同指标包多模型对比（选模型依据）。

---

## 11. 实施分期（仅规划，未开工）

| 期 | 内容 | 产出 |
|---|---|---|
| P1 | foundation：ModelRouter + Orchestrator + 产物存储 + 配置 | 基座可跑通假节点 |
| P2 | indicators：指标计算 + 指标包 + 单测 | 每日自动产出指标包 |
| P3 | agents：大盘+板块解读（含红蓝对抗）+ 提示词框架 | 首份对抗式日报雏形 |
| P4 | evaluator + renderer + 事件/大宗智能体 | 完整日报（md+html） |
| P5 | 个股流水线（临时指定标的）+ Pi 交互入口（datasource 包 MCP / Skill；
     UI 形态：官方终端 TUI 或社区 Web UI，经 RPC/SDK 模式接入，P5 期定） | 双入口打通 |
| P6 | 记忆反思（TradingAgents-CN memory 模式）+ 模型横评面板 | 持续改进闭环 |

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 幻觉污染报告 | 指标包唯一数据入口 + 数字溯源核查 + 修订打回机制 |
| 对抗成本失控 | 轮数配置项 `debate_rounds`（默认 3、上限 4）、仅大盘/板块/个股启用、预算护栏降档 |
| dsh/生态波动影响 | 核心流水线在 Python，Pi 只作交互入口，可替换 |
| 数据源接口变动 | 数据源层 24 源冗余 + 已有韧性（已夯实） |
| 报告合规 | 全报告附免责声明；结论表述为"研究观点"非投资建议 |

---

## 13. 标准与规范

- **配置规范**：统一 pydantic settings，环境变量按命名空间分区——
  `ROUTE_*`（数据源层，已有）/ `LLM_*`（模型层）/ `PIPELINE_*`（流水线）/
  `REPORT_*`（输出层）；全部有默认值、全部可被 `.env` 覆盖；
  敏感项（API key）只走环境变量，不入库不落盘。
- **命名规范**：沿用 datasource 现行风格（snake_case 模块、PascalCase 类、
  `_` 前缀私有）；产物文件名 kebab-case。
- **提示词规范**：外置 YAML 且含 `version` 字段；改动提示词必须跑 §10.2 回放测试；
  prompt 与代码的唯一耦合点是变量名清单，在 YAML 头部显式声明。
- **依赖规范**：核心链路只依赖 pydantic/httpx/pandas/jinja2 等确定性库；
  引入新依赖须在 ADR 中记录理由。
- **决策记录（ADR）**：重大架构决策落 `docs/adr/NNNN-<topic>.md`
  （背景/备选/结论/后果），本文档 §1 决策表即为 ADR-0001~0004 的索引。

## 14. 演进路径（留缝不预建）

| 阶段 | 触发条件 | 动作 |
|---|---|---|
| 当前：单机批处理 | — | 本文档全部内容 |
| 多标的车道并行 | 个股报告需求增多 | stock 流水线按标的并行（信号量分池已预留） |
| 盘中事件监控 | 需要实时解读 | 事件源接入 + 常驻调度器（产物 schema 不变，只换触发器） |
| 多机/常驻服务 | 单机吞吐或稳定性不足 | store 换对象存储/DB，DAG 节点换任务队列（接口不变） |

演进原则：**只换触发器与存储实现，不动层间 schema 与协议**——这是 §2.5 解耦原则的直接回报。

---

## 附录 A：术语表

- **指标包（IndicatorPack）**：指标层产出的结构化中间产物，智能体唯一数据入口。
- **结论包**：单个解读智能体（或裁判）输出的结构化结论（pydantic），评测层核查与总编辑的输入。
- **AgentSpec / AgentRuntime**：声明式智能体定义（prompt/schema/输入/tier/对抗标记）
  与其共享执行运行时；新增智能体零新代码，智能体层无重复逻辑。
- **报告中间结构（ReportIR）**：总编辑产出的结构化报告模型，md/html 渲染共用。
- **红蓝对抗**：多头（Bull）/空头（Bear）多轮辩论 + 裁判（Judge）合并的结论生成协议。
- **ModelRouter 档位**：FAST / ANALYST / JUDGE / EVAL 四档，按任务类型路由不同模型。
