"""datasource.providers._http — 共享异步 HTTP 工具。

所有 provider 共用的 HTTP 请求基础设施：
- 连接池复用：直连池 + 代理池（代理池按代理地址分别池化，列表模式可真实轮换）
- 白名单代理路由：东财等 WAF 敏感域名走代理（见 resilience.ip_pool）
- 代理失败自动直连兜底（http_* 系列入口）
- 统一超时 + 错误处理
- 仅依赖 httpx（项目标准 HTTP 客户端）
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from datasource.resilience.ip_pool import get_proxy_pool

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# 代理传输失败是否直连兜底（默认关闭）。
# 走代理的域名正是「直连被 WAF 封锁」的域名 → 直连兜底几乎必失败，
# 只会白等一个超时。异常直接抛出，由 resilience 层重试（隧道每请求换 IP）。
# 见 `_shared/http_client.py` 同名开关（两台 HTTP 栈行为一致）。
_PROXY_DIRECT_FALLBACK: bool = _env_bool("DATASOURCE_PROXY_DIRECT_FALLBACK", False)

# 客户端池：
# - 直连池：单 client
# - 代理池：按代理地址分别建 client（列表模式下每请求轮换 → 命中不同 client，
#   每个代理的连接池独立复用；隧道模式地址恒定 → 单 client）
# - generation：ProxyPool.reload() 后递增 → 丢弃绑定了旧代理的 client
_direct_client: httpx.AsyncClient | None = None
_proxy_clients: dict[str, httpx.AsyncClient] = {}
_proxy_clients_generation = -1
_client_lock = asyncio.Lock()


def _build_client(proxy: dict[str, str] | None = None) -> httpx.AsyncClient:
    """构造 httpx.AsyncClient（直连或带代理）。

    兼容两代 httpx 代理参数（pyproject 约束 httpx<0.28，mootdx 又拉低到 0.25.x）：
    - httpx ≥0.26：``proxy=<url>``
    - httpx 0.25：``proxies=<url>``
    """
    kwargs: dict[str, Any] = dict(
        timeout=15,
        headers=DEFAULT_HEADERS,
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        follow_redirects=True,
    )
    if proxy:
        proxy_url = proxy.get("https") or proxy.get("http")
        try:
            return httpx.AsyncClient(proxy=proxy_url, **kwargs)
        except TypeError:  # httpx<0.26 无 proxy 参数
            return httpx.AsyncClient(proxies=proxy_url, **kwargs)
    return httpx.AsyncClient(**kwargs)


def _proxy_key(proxy: dict[str, str]) -> str:
    return proxy.get("https") or proxy.get("http") or ""


async def _get_direct_client() -> httpx.AsyncClient:
    """直连池单例（double-check 防竞态）。"""
    global _direct_client
    c = _direct_client
    if c is None or c.is_closed:
        async with _client_lock:
            c = _direct_client
            if c is None or c.is_closed:
                c = _build_client(None)
                _direct_client = c
    return c


async def _get_client_for(proxy: dict[str, str] | None) -> httpx.AsyncClient:
    """按代理配置返回 client（None=直连池）；代理池按地址独立池化。"""
    if proxy is None:
        return await _get_direct_client()

    pool = get_proxy_pool()
    global _proxy_clients, _proxy_clients_generation
    key = _proxy_key(proxy)
    c = _proxy_clients.get(key)
    if c is None or c.is_closed or _proxy_clients_generation != pool.generation:
        async with _client_lock:
            if _proxy_clients_generation != pool.generation:
                _proxy_clients = {}  # reload/续提 后旧代理 client 全部作废
                _proxy_clients_generation = pool.generation
            c = _proxy_clients.get(key)
            if c is None or c.is_closed:
                c = _build_client(proxy)
                _proxy_clients[key] = c
    return c


async def _get_client(url: str | None = None) -> httpx.AsyncClient:
    """按 URL 返回 client：白名单域名走代理池（每请求推进轮换），其余走直连池。"""
    pool = get_proxy_pool()
    if not (url and pool.should_proxy(url)):
        return await _get_direct_client()
    proxy = pool.proxy_for(url)  # 每次调用推进列表轮换；隧道模式恒定
    if proxy is None:  # 竞态：reload() 后代理被清空
        return await _get_direct_client()
    return await _get_client_for(proxy)


async def get_shared_client(url: str | None = None):
    """获取共享 httpx.AsyncClient（连接池复用）。

    Args:
        url: 目标 URL；东财等 WAF 敏感域名走代理池，其余走直连池。
             传 None 时恒为直连池（不享受代理路由）。

    注意：直接使用本 client 发请求时没有「代理失败→直连兜底」；
    新代码优先用 http_get_json / http_get_text / http_post_* 系列入口。
    """
    return await _get_client(url)


async def _throttle_if_eastmoney(url: str) -> None:
    """东财域名走共享节流器（与 _shared/http_client 的 requests 栈共用时隙）。

    消除「同一东财域名两条 HTTP 管道、节流策略不一致」问题：
    httpx（本模块）与 requests（_shared/http_client）都受 EM_MIN_INTERVAL 约束。
    """
    if "eastmoney.com" in url:
        from datasource.providers._shared.http_client import throttle_async

        await throttle_async()


async def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """统一请求入口：节流 + 代理路由 + （可选）直连兜底 + raise_for_status。"""
    await _throttle_if_eastmoney(url)
    pool = get_proxy_pool()
    proxied = bool(url) and pool.should_proxy(url)
    proxy_used = pool.proxy_for(url) if proxied else None
    client = await _get_client_for(proxy_used)
    try:
        resp = await client.request(method, url, **kwargs)
    except httpx.TransportError as exc:
        # 代理出口失效（隧道到期/被 WAF 踢）→ 上报摘除，下次请求自动提取新 IP
        if proxied:
            pool.report_proxy_failure(proxy_used)
        # 走代理的域名 = 直连被 WAF 封锁的域名 → 直连兜底几乎必失败（白等超时）。
        # 默认直接抛出，交由 resilience 层重试（重试即换出口 IP）。
        if not proxied or not _PROXY_DIRECT_FALLBACK:
            raise
        logger.warning("代理请求失败(%s)，直连兜底重试: %s", type(exc).__name__, url)
        await _throttle_if_eastmoney(url)
        resp = await (await _get_direct_client()).request(method, url, **kwargs)
    resp.raise_for_status()
    return resp


async def http_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> Any:
    """异步 HTTP GET → JSON（连接池复用 + 代理路由 + 直连兜底）。

    Raises:
        httpx.HTTPError 等原始异常（由调用方包装 ProviderError）
    """
    resp = await _request("GET", url, params=params, headers=headers, timeout=timeout)
    return resp.json()


async def http_get_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> str:
    """异步 HTTP GET → text。"""
    resp = await _request("GET", url, params=params, headers=headers, timeout=timeout)
    return resp.text


async def http_post_json(
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> Any:
    """异步 HTTP POST → JSON（JSON 请求体）。"""
    resp = await _request("POST", url, json=json_body, headers=headers, timeout=timeout)
    return resp.json()


async def http_post_form(
    url: str,
    *,
    form_data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> Any:
    """异步 HTTP POST → JSON（表单编码请求体）。

    用于 application/x-www-form-urlencoded 接口（如巨潮 hisAnnouncement/query）；
    Content-Type 由 httpx 自动附加，调用方不要在 headers 里手工设置。
    """
    resp = await _request("POST", url, data=form_data, headers=headers, timeout=timeout)
    return resp.json()


def safe_float(value: Any, default: float = 0.0) -> float:
    """安全浮点转换（防 ValueError/TypeError）。"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default