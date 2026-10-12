"""IP 池 — 东财等 WAF 封锁源的代理接入。

为什么需要：东财对单 IP 有请求阈值，触发后 IP 级封锁（实测 push2 三 host 全断、等待不恢复）。
重试救不了 WAF 封锁，代理轮换出口 IP 是正解。

两种形态（付费代理通常用第一种）：
1. **隧道代理**（推荐）：单入口多出口，如 `http://user:pass@proxy.zhimaruanjian.com:8080`
   —— 一个地址，服务端自动轮换出口 IP，客户端无感知
2. **代理列表**：多个 `ip:port`，按请求轮换（_http 层按代理维护独立连接池）

配置（环境变量）：
- `DATASOURCE_PROXY`            隧道代理单地址（`http://user:pass@host:port`）
- `DATASOURCE_PROXY_LIST`       JSON 数组代理列表（`[{"http":"http://ip1:port"}, ...]`）
- `DATASOURCE_PROXY_HOSTS`      走代理的域名白名单（逗号分隔，默认 `eastmoney.com`）
- `DATASOURCE_PROXY_ENABLED`    总开关（默认 true，前提是配了代理地址）

用法::

    from datasource.resilience.ip_pool import proxy_for, should_proxy

    # 该 URL 是否走代理 / 取代理配置（None=直连）
    if should_proxy(url):
        proxy = proxy_for(url)   # {"http": ..., "https": ...}

设计：
- 惰性加载：未配置代理时零开销
- 白名单路由：只对 WAF 敏感域名走代理（域名后缀匹配），其余直连
- 热更新：reload() 重读配置并递增 generation，_http 层据此丢弃绑定旧代理的 client
- 失败兜底：传输层失败由 _http 层直连重试（见 providers/_http.py）
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 走代理的域名白名单（默认只对东财）
_DEFAULT_PROXY_HOSTS = ("eastmoney.com",)
_PROXY_ENABLED_ENV = "DATASOURCE_PROXY_ENABLED"
_PROXY_ENV = "DATASOURCE_PROXY"
_PROXY_LIST_ENV = "DATASOURCE_PROXY_LIST"
_PROXY_HOSTS_ENV = "DATASOURCE_PROXY_HOSTS"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ProxyPool:
    """代理池：隧道单地址 + 代理列表轮换。

    线程安全（代理配置在运行期可热更新，读多写少用读写锁）。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._enabled = _env_bool(_PROXY_ENABLED_ENV, True)
        self._tunnel: str | None = None          # 隧道单地址
        self._proxies: list[dict[str, str]] = []  # 代理列表（dict 带 http/https 键）
        self._index = 0                          # 轮换游标
        self._generation = 0                     # 配置代数（reload 后递增，供 _http 感知）
        self._routed = 0                         # 已路由到代理的请求数（= 付费请求数，含重试）
        self._hosts = self._load_hosts()
        self._load_proxies()

    # ── 加载 ──────────────────────────────────────────────────────

    def _load_hosts(self) -> tuple[str, ...]:
        raw = os.getenv(_PROXY_HOSTS_ENV, "").strip()
        if raw:
            return tuple(h.strip().lower() for h in raw.split(",") if h.strip())
        return _DEFAULT_PROXY_HOSTS

    def _load_proxies(self) -> None:
        with self._lock:
            # 隧道单地址优先
            tunnel = os.getenv(_PROXY_ENV, "").strip()
            if tunnel:
                self._tunnel = tunnel
                self._proxies = []
                logger.info("IP 池: 启用隧道代理 %s", _mask(tunnel))
                return
            # 代理列表
            raw_list = os.getenv(_PROXY_LIST_ENV, "").strip()
            if raw_list:
                try:
                    parsed = json.loads(raw_list)
                    if isinstance(parsed, list) and parsed:
                        self._proxies = [
                            {"http": p, "https": p} if isinstance(p, str) else dict(p)
                            for p in parsed
                        ]
                        self._tunnel = None
                        logger.info("IP 池: 启用代理列表 %d 条", len(self._proxies))
                        return
                except (ValueError, TypeError) as exc:
                    logger.warning("IP 池: DATASOURCE_PROXY_LIST 解析失败，忽略: %s", exc)
            # 未配置
            self._tunnel = None
            self._proxies = []

    # ── 对外接口 ──────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """是否配置了代理。"""
        with self._lock:
            return bool(self._tunnel or self._proxies)

    def should_proxy(self, url: str) -> bool:
        """该 URL 是否应走代理（白名单域名 + 已配置 + 开关开启）。"""
        if not self._enabled or not self.is_configured():
            return False
        host = _url_host(url)
        # 后缀匹配：eastmoney.com 命中 push2.eastmoney.com，但不会命中 eastmoney.com.evil.com
        return any(host == h or host.endswith("." + h) for h in self._hosts)

    def proxy_for(self, url: str) -> dict[str, str] | None:
        """取该 URL 的代理配置；不该走代理或未配置返回 None（=直连）。

        返回值非 None 即代表「本次请求将走代理」→ 计入 ``routed_requests``
        （含重试；用于观测付费请求消耗，避免无感放大）。
        """
        if not self.should_proxy(url):
            return None
        with self._lock:
            if self._tunnel:
                self._routed += 1
                return {"http": self._tunnel, "https": self._tunnel}
            if self._proxies:
                p = self._proxies[self._index % len(self._proxies)]
                self._index += 1  # 轮换
                self._routed += 1
                return p
            return None

    def reload(self) -> None:
        """热更新代理配置（改环境变量后调用）。

        重读 开关/白名单/代理，并递增 generation——_http 层据此
        丢弃绑定旧代理的 httpx client（否则热更新不生效）。
        """
        with self._lock:
            self._enabled = _env_bool(_PROXY_ENABLED_ENV, True)
            self._hosts = self._load_hosts()
            self._load_proxies()
            self._index = 0
            self._generation += 1

    @property
    def generation(self) -> int:
        """配置代数（reload 后递增）。"""
        with self._lock:
            return self._generation

    # ── 便捷属性（供测试/自检）────────────────────────────────────

    def describe(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "tunnel": _mask(self._tunnel) if self._tunnel else None,
                "list_size": len(self._proxies),
                "hosts": list(self._hosts),
                "routed_requests": self._routed,  # 累计已路由到代理的请求数（含重试）
            }

    @property
    def routed_requests(self) -> int:
        """累计已路由到代理的请求数（约等于付费请求数，含重试）。"""
        with self._lock:
            return self._routed

    def reset_counter(self) -> int:
        """清零请求计数并返回清零前的值（用于分段测量消耗）。"""
        with self._lock:
            prev = self._routed
            self._routed = 0
            return prev


# ── 模块级单例 ────────────────────────────────────────────────────
_pool: ProxyPool | None = None
_pool_lock = threading.Lock()


def get_proxy_pool() -> ProxyPool:
    """获取全局代理池单例。"""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ProxyPool()
    return _pool


def proxy_for(url: str) -> dict[str, str] | None:
    """便捷入口：该 URL 的代理配置（None=直连）。"""
    return get_proxy_pool().proxy_for(url)


def should_proxy(url: str) -> bool:
    """便捷入口：该 URL 是否应走代理。"""
    return get_proxy_pool().should_proxy(url)


# ── 工具 ──────────────────────────────────────────────────────────

def _url_host(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _mask(proxy: str) -> str:
    """脱敏代理地址（隐藏密码）。"""
    try:
        from urllib.parse import urlparse

        p = urlparse(proxy)
        if p.password:
            return proxy.replace(f":{p.password}@", ":***@")
    except Exception:
        pass
    return proxy


__all__ = [
    "ProxyPool",
    "get_proxy_pool",
    "proxy_for",
    "should_proxy",
]
