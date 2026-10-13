"""IP 池 — 东财等 WAF 封锁源的代理接入。

为什么需要：东财对单 IP 有请求阈值，触发后 IP 级封锁（实测 push2 三 host 全断、等待不恢复）。
重试救不了 WAF 封锁，代理轮换出口 IP 是正解。

两种形态（付费代理通常用第一种）：
1. **隧道代理**（推荐）：单入口多出口，如 `http://user:pass@proxy.zhimaruanjian.com:8080`
   —— 一个地址，服务端自动轮换出口 IP，客户端无感知
2. **代理列表**：多个 `ip:port`，按请求轮换（_http 层按代理维护独立连接池）
3. **动态按量 IP（自动提取）**：配置提取 API（如巨量 getips 链接），IP 池在
   列表为空 / IP 到期 / IP 被判失效时**自动 HTTP 提取新 IP**——对外表现与
   隧道一致，按提取的 IP 数计费（一个 IP 时效内复用全部请求，最省）。

配置（环境变量）：
- `DATASOURCE_PROXY`            隧道代理单地址（`http://user:pass@host:port`）
- `DATASOURCE_PROXY_LIST`       JSON 数组代理列表（`[{"http":"http://ip1:port"}, ...]`）
- `DATASOURCE_PROXY_API`        动态 IP 提取链接（getips API；返回 ip:port 文本或 JSON）
- `DATASOURCE_PROXY_API_TTL`    提取 IP 的时效秒数（默认 55；3 分钟档配 170，留提前量）
- `DATASOURCE_PROXY_HOSTS`      走代理的域名白名单（逗号分隔，默认 `eastmoney.com`）
- `DATASOURCE_PROXY_ENABLED`    总开关（默认 true，前提是配了代理地址）

用法::

    from datasource.resilience.ip_pool import proxy_for, should_proxy

    # 该 URL 是否走代理 / 取代理配置（None=直连）
    if should_proxy(url):
        proxy = proxy_for(url)   # {"http": ..., "https": ...}

设计：
- 惰性加载：未配置代理时零开销；提取型只在有白名单请求且需要时才提取（空闲零消耗）
- 白名单路由：只对 WAF 敏感域名走代理（域名后缀匹配），其余直连
- 热更新：reload() 重读配置并递增 generation，_http 层据此丢弃绑定旧代理的 client
- 失效自愈：传输失败调 report_proxy_failure() 摘除坏 IP，下次请求自动提取新 IP
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# 走代理的域名白名单（默认只对东财）
_DEFAULT_PROXY_HOSTS = ("eastmoney.com",)
_PROXY_ENABLED_ENV = "DATASOURCE_PROXY_ENABLED"
_PROXY_ENV = "DATASOURCE_PROXY"
_PROXY_LIST_ENV = "DATASOURCE_PROXY_LIST"
_PROXY_API_ENV = "DATASOURCE_PROXY_API"
_PROXY_API_TTL_ENV = "DATASOURCE_PROXY_API_TTL"
_PROXY_HOSTS_ENV = "DATASOURCE_PROXY_HOSTS"

# 提取型默认提前量：IP 标称时效内、略提前续提，避免请求落在过期边界
_DEFAULT_EXTRACT_TTL_SEC = 55.0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _proxy_key(p: dict[str, str]) -> str:
    return p.get("https") or p.get("http") or ""


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
        self._generation = 0                     # 配置代数（reload/续提 后递增，供 _http 感知）
        self._routed = 0                         # 已路由到代理的请求数（= 付费请求数，含重试）
        self._extractions = 0                    # 动态 IP 提取次数（≈ 付费 IP 数）
        # 动态按量 IP 自动提取
        self._extract_api = os.getenv(_PROXY_API_ENV, "").strip()
        self._extract_ttl = _env_float(_PROXY_API_TTL_ENV, _DEFAULT_EXTRACT_TTL_SEC)
        self._proxy_acquired_at = 0.0            # 当前列表 IP 的提取时刻（0=无/待续提）
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
            # 静态代理列表（提取型不在此处理：_ensure_fresh 动态填充）
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
            # 未配置静态代理：可能是提取型（惰性，首次 proxy_for 才提取）或纯直连
            self._tunnel = None
            self._proxies = []

    # ── 动态 IP 自动提取 ───────────────────────────────────────────

    def _parse_extract_response(self, text: str) -> list[str]:
        """解析提取 API 响应为 ``ip:port`` 列表。

        兼容：
        - 纯文本（result_type=text&split=1）：``1.2.3.4:8080`` 每行一个；
        - JSON：常见于巨量 ``{"code":200,"data":{"proxy_list":[...]}}``，
          列表项支持 ``"ip:port"`` 字符串或 ``{"ip":..,"port":..}`` / ``{"host":..}`` 对象。
        """
        text = (text or "").strip()
        if not text:
            return []
        if not text.lstrip().startswith(("{", "[")):
            return [ln.strip() for ln in text.splitlines() if ":" in ln]
        try:
            payload = json.loads(text)
        except ValueError:
            return []

        out: list[str] = []

        def _node(node: Any) -> None:
            if isinstance(node, str):
                if ":" in node and node.count(".") >= 3:
                    out.append(node)
            elif isinstance(node, list):
                for x in node:
                    _node(x)
            elif isinstance(node, dict):
                ip = node.get("ip") or node.get("host") or node.get("addr")
                port = node.get("port")
                if ip and port:
                    out.append(f"{ip}:{port}")
                else:
                    for v in node.values():
                        _node(v)

        _node(payload)
        return out

    def _extract_once(self) -> None:
        """调提取 API 取一批新 IP 填充列表（调用方持锁）。

        用 requests 同步短请求（提取极轻量、低频，且本方法被锁串行化）。
        """
        import requests  # 延迟导入：未配提取 API 时零依赖开销

        logger.info("IP 池: 自动提取动态代理 IP ...")
        resp = requests.get(self._extract_api, timeout=10,
                            headers={"User-Agent": "Mozilla/5.0"})
        endpoints = self._parse_extract_response(resp.text)
        if not endpoints:
            raise RuntimeError(f"动态代理提取失败/无可用 IP: {resp.text[:150]!r}")
        self._proxies = [
            {"http": f"http://{ep}", "https": f"http://{ep}"} if not ep.startswith("http")
            else {"http": ep, "https": ep}
            for ep in endpoints
        ]
        self._index = 0
        self._proxy_acquired_at = time.time()
        self._extractions += 1
        self._generation += 1  # 新 IP → 丢弃绑定旧 IP 的 httpx client
        logger.info("IP 池: 提取到 %d 个代理（累计提取 %d 次），%s",
                    len(self._proxies), self._extractions, _mask(endpoints[0]))

    def _needs_extract(self) -> bool:
        """是否需要（重新）提取：无隧道 + 配了提取 API + （列表空 或 已过 TTL）。"""
        if self._tunnel or not self._extract_api:
            return False
        if not self._proxies:
            return True
        return (time.time() - self._proxy_acquired_at) >= self._extract_ttl

    def _ensure_fresh(self) -> None:
        if not self._needs_extract():
            return
        self._extract_once()

    # ── 对外接口 ──────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """是否配置了代理（含隧道 / 静态列表 / 提取 API）。"""
        with self._lock:
            return bool(self._tunnel or self._proxies or self._extract_api)

    def should_proxy(self, url: str) -> bool:
        """该 URL 是否应走代理（白名单域名 + 已配置 + 开关开启）。"""
        if not self._enabled or not self.is_configured():
            return False
        host = _url_host(url)
        # 后缀匹配：eastmoney.com 命中 push2.eastmoney.com，但不会命中 eastmoney.com.evil.com
        return any(host == h or host.endswith("." + h) for h in self._hosts)

    def proxy_for(self, url: str) -> dict[str, str] | None:
        """取该 URL 的代理配置；不该走代理或未配置返回 None（=直连）。

        提取型：必要时（列表空/到期）在此**惰性提取**新 IP（锁内串行，并发只提取一次）。
        返回值非 None 即代表「本次请求将走代理」→ 计入 ``routed_requests``。
        """
        if not self.should_proxy(url):
            return None
        with self._lock:
            if self._tunnel:
                self._routed += 1
                return {"http": self._tunnel, "https": self._tunnel}
            self._ensure_fresh()  # 提取型：到期/空则续提（可能抛 RuntimeError，由调用方重试）
            if self._proxies:
                p = self._proxies[self._index % len(self._proxies)]
                self._index += 1  # 轮换
                self._routed += 1
                return p
            return None

    def report_proxy_failure(self, proxy: dict[str, str] | str | None) -> None:
        """上报某代理传输失败 → 从列表摘除；下次 proxy_for 自动提取新 IP。

        仅对**提取型/列表型**生效（隧道单地址摘除无意义，服务端自己轮换出口）。
        """
        if not proxy:
            return
        key = proxy if isinstance(proxy, str) else _proxy_key(proxy)
        with self._lock:
            if self._tunnel:
                return
            before = len(self._proxies)
            self._proxies = [p for p in self._proxies if _proxy_key(p) != key]
            if len(self._proxies) != before:
                self._proxy_acquired_at = 0.0  # 强制下次续提
                self._generation += 1
                logger.warning("IP 池: 摘除失效代理 %s（剩余 %d）", _mask(key), len(self._proxies))

    def reload(self) -> None:
        """热更新代理配置（改环境变量后调用）。

        重读 开关/白名单/代理，并递增 generation——_http 层据此
        丢弃绑定旧代理的 httpx client（否则热更新不生效）。
        """
        with self._lock:
            self._enabled = _env_bool(_PROXY_ENABLED_ENV, True)
            self._hosts = self._load_hosts()
            self._extract_api = os.getenv(_PROXY_API_ENV, "").strip()
            self._extract_ttl = _env_float(_PROXY_API_TTL_ENV, _DEFAULT_EXTRACT_TTL_SEC)
            self._proxy_acquired_at = 0.0
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
                "extract_api_configured": bool(self._extract_api),
                "routed_requests": self._routed,  # 累计已路由到代理的请求数（含重试）
                "extractions": self._extractions,  # 累计提取的付费 IP 数
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


def report_proxy_failure(proxy: dict[str, str] | str | None) -> None:
    """便捷入口：上报失效代理，触发摘除 + 下次自动提取新 IP。"""
    get_proxy_pool().report_proxy_failure(proxy)


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
