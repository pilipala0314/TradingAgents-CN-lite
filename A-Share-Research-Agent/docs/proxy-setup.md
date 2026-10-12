# IP 池 / 代理配置指南

## 背景

东财对单 IP 有请求阈值，触发后 IP 级封锁（实测 push2 三 host 全断、等待不恢复）。重试救不了 WAF 封锁，代理轮换出口 IP 是正解。

## 支持的两种形态

| 形态 | 适用 | 配置 |
|---|---|---|
| **隧道代理**（推荐） | 芝麻/快代理等付费隧道，单入口多出口 | `DATASOURCE_PROXY` |
| **代理列表** | 多个独立代理，客户端轮换 | `DATASOURCE_PROXY_LIST` |

## 环境变量

| 变量 | 说明 | 示例 |
|---|---|---|
| `DATASOURCE_PROXY` | 隧道代理单地址 | `http://user:pass@proxy.zhimaruanjian.com:8080` |
| `DATASOURCE_PROXY_LIST` | JSON 代理列表 | `[{"http":"http://ip1:8080"},{"http":"http://ip2:8080"}]` |
| `DATASOURCE_PROXY_HOSTS` | 走代理的域名（逗号分隔） | `eastmoney.com`（默认） |
| `DATASOURCE_PROXY_ENABLED` | 总开关 | `false` 关闭 |
| `DATASOURCE_PROXY_DIRECT_FALLBACK` | 代理失败后是否直连兜底（默认 `false`） | 见下方「成本守护」 |

## 使用示例

### 隧道代理（推荐）

```bash
# Linux/macOS
export DATASOURCE_PROXY="http://user:pass@proxy.zhimaruanjian.com:8080"

# PowerShell
$env:DATASOURCE_PROXY = "http://user:pass@proxy.zhimaruanjian.com:8080"
```

### 代理列表

```bash
export DATASOURCE_PROXY_LIST='[{"http":"http://ip1:8080"},{"http":"http://ip2:8080"}]'
```

### 自定义白名单

```bash
# 只对东财和财联社走代理
export DATASOURCE_PROXY_HOSTS="eastmoney.com,cls.cn"
```

## 代理池行为

1. **白名单路由**：只对 `DATASOURCE_PROXY_HOSTS` 内的域名走代理，其余直连（省流量）
2. **双 client 池**：httpx 直连池 + 代理池独立（连接池不互相污染）
3. **轮换**：隧道单地址（服务端轮换出口 IP）/ 列表模式（客户端轮换 + 失败摘除）
4. **热更新**：改环境变量后调 `get_proxy_pool().reload()`
5. **脱敏**：`describe()` 输出隐藏密码
6. **akshare 东财端点同样覆盖**：akshare 内部走 requests/curl_cffi，本不经过代理栈；
   已在其反爬补丁中注入代理（`Session.request` 级 + curl_cffi `proxies`），
   使 akshare 的东财系接口（成分股/涨停池/资金流/全市场列表）也走隧道换 IP。
   `DATASOURCE_EM_BYPASS_PROXY=true` 可强制其直连（企业代理环境下用）。

## 成本守护（按请求付费时必须知道）

付费代理下**每次重试 = 一次真实请求 = 一次计费**，调用机制已做以下约束：

1. **重试单一权威层**：HTTP 栈内一律不重试（`EM_SESSION` 的 urllib3 `max_retries=0`），
   重试统一由 `resilience.RetryPolicy` 负责 —— 避免 `urllib3(3) × resilience(3)` 叠加放大 9 倍。
2. **只重试瞬态故障**：`NonRetryableProviderError` 与 HTTP 4xx（403/404，408/429 除外）**不重试**，
   确定性失败不再白烧请求；判定沿异常因果链下钻。
3. **直连兜底默认关闭**（`DATASOURCE_PROXY_DIRECT_FALLBACK=false`）：走代理的域名正是直连被
   WAF 封锁的域名，兜底必失败白等超时；异常上抛由 resilience 重试（隧道重试即换出口 IP）。
4. **逐成分股循环早停**：`member_fund_flow` / `news` 逐只成分股调用，若某源「至今零响应且连续
   8 只失败」即判源级故障并跳过其余（60→~8），有任一成功则绝不停（避免丢数据）。
5. **请求计数**：`get_proxy_pool().routed_requests` / `describe()["routed_requests"]` 累计已路由到
   代理的请求数（含重试，≈ 付费请求数）；`reset_counter()` 可分段测量。

> 实测参考：`get_industry_ranking(limit=500)` 翻页取全 496 个板块 = **5 个请求**（含 0 重试）；
> 二次调用命中缓存 = **0 个请求**。

## 付费代理推荐

| 服务 | 特点 | 价格区间 |
|---|---|---|
| 芝麻代理 | 隧道代理、国内住宅 IP | 几十元/月 |
| 快代理 | 隧道代理、带宽可选 | 几十元/月 |
| 讯代理 | 按流量计费 | 低流量更便宜 |

> **免费代理不推荐**：可用率 <10%、多为数据中心 IP（被东财 WAF 一眼识破）、有中间人风险。

## 验证

```python
from datasource.resilience.ip_pool import get_proxy_pool

pool = get_proxy_pool()
print(pool.describe())
# {'enabled': True, 'tunnel': 'http://user:***@proxy.example.com:8080', 'list_size': 0, 'hosts': ['eastmoney.com']}
```

## 故障排查

| 问题 | 原因 | 解决 |
|---|---|---|
| 代理不生效 | 域名不在白名单 | 检查 `DATASOURCE_PROXY_HOSTS` |
| 连接超时 | 代理地址错 | `pool.describe()` 看脱敏地址 |
| 流量消耗大 | 白名单太宽 | 收窄到 `eastmoney.com` |
