# datasource

轻量、可插拔、零外部依赖的多源金融数据路由库。

## 特性

- **24 个 Provider 自动注册**：akshare / tushare / baostock / eastmoney / sina / netease / news / sentiment / policy / global_media / geopolitics / cninfo / cninfo_news / fundamentals_ext / a_stock_data / bse / bse_index / futures / hk_full / hk_southbound / us_stock / global_quote / financial_report / research_rag
- **统一入口**：`from datasource import get_source`
- **韧性中间件**：重试（指数退避+抖动）/ 限流 / 熔断 / 并发隔离，每 provider 独立策略
- **轻量缓存**：进程内 LRU+TTL（默认）或 内存+本地文件 分层（`ROUTE_CACHE_PERSISTENT=true`），零外部服务
- **自适应 fallback**：按历史成功率/延迟自动排序 provider 链
- **本地调用日志**：批量写入 `.call_logs/YYYYMMDD.jsonl`（无数据库依赖）
- **可移植**：仅复制 `datasource/` 目录即可独立运行（零上层项目依赖）

## 快速开始

```bash
pip install -e ".[pro,tdx]"
```

```python
import asyncio
from datasource import get_source

async def main():
    source = get_source()
    df = await source.route("get_daily_bars", "600519", "20250101", "20250701")
    print(df.head())

asyncio.run(main())
```

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ROUTE_CACHE_TTL_SEC` | 30 | 缓存 TTL（秒） |
| `ROUTE_CACHE_PERSISTENT` | false | true=内存+本地文件分层缓存 |
| `ROUTE_DEFAULT_TIMEOUT` | 12 | 单端点超时（秒） |
| `ROUTE_DEFAULT_RESILIENCE` | true | 韧性策略开关 |
| `TUSHARE_TOKEN` | "" | Tushare Token |
| `DATASOURCE_AUTO_INSTALL` | false | 缺失依赖时允许自动 pip 安装 |
| `DATASOURCE_CALL_LOG_DIR` | `.call_logs` | 调用日志目录 |

## 新增 Provider（3 步）

1. 新建 `datasource/providers/my_source.py`，类上标 `@register_provider("my_source")`
2. 在 `datasource/providers/__init__.py` 的 `_PROVIDER_MODULES` 加入模块名
3. 在 `datasource/method_table.py` 注册方法路由

## L2 指标层（indicators）— 源级融合

在数据源层之上做**源级融合**：读因子注册表（`foundation/registry/sector_factors.yaml`，15 因子 / 22 源）
→ 锁定 provider 取数 → 按 `extract_kind` 提取 → 按 `fusion` 策略融合（含一致性标注 agree/single/conflict）
→ 产出可溯源的 `IndicatorPack`（智能体唯一数据入口）并原子落盘。

```bash
python -m indicators --sector 半导体 --trade-date 2026-09-30 --round 2 --print
# → reports/2026-09-30/artifacts/indicators/sector_半导体_g2.json
```

- 每个数字带溯源（source / method / status / as_of）与多源一致性标注；分歧超阈值 → `CONFLICT`（不可用）；
- **封锁诚实降级**：单源失败不中断因子；全源失败 `value=None`（绝不编造 0/假值）；整表类（WHOLE）因子不产生误导性标量；维度质量汇总进 `data_quality`；
- 时点闸门：仅对当日生效（`--force` 绕过；`--round 2` 跳过 `available_from`）；成分股一次解析、运行内复用；
- 离线单测：`tests/test_indicators_*.py`（FakeProvider 回放，无网络）。

## 智能体技能（Agent Skill）

项目内置数据源使用技能，供项目内智能体/开发代理直接加载：

- [`skills/using-datasource/SKILL.md`](skills/using-datasource/SKILL.md) — 核心用法（调用模式/选型/契约/异常/配置）
- [`skills/using-datasource/reference.md`](skills/using-datasource/reference.md) — 全部 89 个 API 完整参考（源码自动生成）
- [`skills/using-datasource/examples.md`](skills/using-datasource/examples.md) — 端到端示例集（含新 Provider 模板）
- 运行时自检：`python skills/using-datasource/scripts/list_apis.py [--providers | --chains]`
