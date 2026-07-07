# 运维手册（A-Share Research Agent）

> 版本：v0.7+（P3 监控 + P5 多租户）
> 范围：部署 / 监控 / 告警 / 数据保留 / 故障恢复

## 1. 启动

### 1.1 最小依赖
- Python 3.10+
- PostgreSQL 14+
- Redis 6+（可选，缺则自动 in-memory fallback）
- 浏览器（用 Dashboard）

### 1.2 环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `DATABASE_URL` | ✅ | `postgresql+asyncpg://localhost/ashare` | SQLAlchemy 异步 URL |
| `REDIS_URL` | ❌ | — | Redis 缓存；缺则 in-mem fallback |
| `DEEPSEEK_API_KEY` | ❌ | — | LLM API key；缺则走规则 baseline |
| `LLM_API_BASE` | ❌ | `https://api.deepseek.com` | LLM base URL |
| `ALERT_WEBHOOK_URL` | ❌ | — | 告警 webhook（多个用逗号分隔） |
| `ASHARE_TENANT` | ❌ | `default` | 当前进程默认租户 |
| `RETENTION_<TABLE>_DAYS` | ❌ | — | 单表保留天数（覆盖默认） |

### 1.3 启动命令
```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 数据库迁移
alembic upgrade head

# 3. 启动 API
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000

# 4. 打开 Dashboard
open http://localhost:8000/
```

## 2. 监控端点

| 端点 | 用途 | 消费者 |
|---|---|---|
| `GET /health` | 总体健康 | 业务方/load balancer |
| `GET /health/endpoints` | DataRouter 各端点统计 | 运维 |
| `GET /metrics` | Prometheus 文本 | Prometheus / Grafana |
| `GET /metrics/traces?limit=100` | 最近 span 列表 | 调试 |
| `GET /metrics/info` | App 元信息（version/uptime） | 调试 |

## 3. 告警管理

### 3.1 内置规则
| 规则 | 默认阈值 | 级别 |
|---|---|---|
| `data_router_fallback_rate` | >= 0.20 | warn |
| `cache_hit_ratio` | < 0.30 | warn |

### 3.2 手动触发
```bash
# 立即评估
curl -X POST http://localhost:8000/admin/alert/test

# 自定义告警事件
curl -X POST http://localhost:8000/admin/alert/dispatch \
  -H "Content-Type: application/json" \
  -d '{"rule":"manual_test","level":"warn","message":"手动测试告警","value":0,"threshold":0}'
```

### 3.3 新增 Webhook 通道
```bash
curl -X POST http://localhost:8000/admin/alert/channel \
  -H "Content-Type: application/json" \
  -d '{"name":"ops-feishu","url":"https://open.feishu.cn/hook/xxx"}'
```

### 3.4 去抖
- 60s 内同 rule 只触发一次（避免抖动风暴）

## 4. 数据保留

### 4.1 默认策略
| 表 | 保留天数 | 最少行数 |
|---|---|---|
| fact_market_bar | 730 | 1,000,000 |
| fact_event | 730 | 10,000 |
| fact_capital_flow | 365 | 100,000 |
| fact_rule_hit | 365 | 10,000 |
| fact_risk_alert | 365 | 1,000 |
| fact_strategy_output | 365 | 1,000 |
| fact_decision_trace | 365 | 1,000 |
| fact_backtest_run | 180 | 100 |
| fact_event_raw | 90 | 0 |

### 4.2 干跑
```bash
curl -X POST "http://localhost:8000/admin/retention/run?dry_run=true"
```

### 4.3 真删
```bash
curl -X POST "http://localhost:8000/admin/retention/run?dry_run=false"
```

## 5. 多租户

### 5.1 设置租户
```bash
export ASHARE_TENANT=acme
# 或请求头 X-Tenant-Id: acme
```

### 5.2 查所有租户
```bash
curl http://localhost:8000/admin/tenants
```

### 5.3 单租户使用量
```bash
curl http://localhost:8000/admin/tenant/acme/usage
```

## 6. 回测 / 高级分析

### 6.1 同步回测
```bash
curl -X POST http://localhost:8000/v1/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"strategy_name":"momentum_20","universe":"hs300","top_n":20,"rebalance_freq":"weekly"}'
```

### 6.2 异步回测
```bash
# 提交
curl -X POST http://localhost:8000/v1/backtest/run/async -d '{...}'
# → 返 {"job_id": "..."}

# 轮询
curl http://localhost:8000/v1/backtest/runs/{job_id}
```

### 6.3 Walk-forward
```bash
curl -X POST http://localhost:8000/v1/backtest/wf/run \
  -H "Content-Type: application/json" \
  -d '{"returns":[0.001,0.002,...], "n_folds":5, "train_pct":0.6, "anchored":true}'
```

### 6.4 蒙特卡洛
```bash
curl -X POST http://localhost:8000/v1/backtest/mc/run \
  -H "Content-Type: application/json" \
  -d '{"returns":[0.001,0.002,...], "n_sims":500, "horizon":252, "block_size":5}'
```

## 7. 故障恢复

### 7.1 DataRouter 全挂
- 现象：`/health/endpoints` 大量 `last_error`
- 措施：检查外网 → `ROUTE_DEFAULT_RESILIENCE=true` 开重试 → 切备用 provider
- 降级：所有 endpoint 走 cache，cache miss 返 stale（设置 5min TTL）

### 7.2 Redis 不可用
- 现象：日志 `RedisCache 连接失败`
- 措施：无需操作，RedisCache 自动降级到 InMemoryCache（不阻断调用方）

### 7.3 DB 不可用
- 现象：HTTP 5xx
- 措施：检查 DATABASE_URL + `pool_pre_ping` → 查 alembic 版本 → 查连接数

### 7.4 LLM 不可用
- 现象：committee 走 fallback（`fact_decision_trace.fallback_used=true`）
- 措施：检查 DEEPSEEK_API_KEY + LLM_API_BASE → 看 `/metrics/traces` 中 llm 节点 error

## 8. 升级流程

```bash
# 1. 拉代码
git pull

# 2. 装新依赖
pip install -e ".[dev]"

# 3. 跑迁移
alembic upgrade head

# 4. 跑测试
pytest -q
ruff check

# 5. 重启 API（先 graceful close scheduler）
systemctl restart ashare-api
```

## 9. 监控面板建议（Grafana）

- **HTTP QPS / 错误率**：panel from `ashare_http_requests_total` / `ashare_http_request_errors_total`
- **DataRouter 健康**：panel from `ashare_data_router_health_score`
- **Cache 命中率**：panel from `ashare_cache_hit_ratio`
- **Backtest 吞吐**：panel from `ashare_backtest_runs_total` rate
- **LLM 失败率**：panel from `ashare_llm_calls_total{status="error"} / ashare_llm_calls_total`
- **告警触发**：panel from `ashare_alerts_triggered_total` by rule
