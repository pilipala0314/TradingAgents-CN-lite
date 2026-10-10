"""东财 HTTP 统一节流入口（Keep-Alive session + Retry + 最小间隔 + 随机抖动）。

提供：
- `UA`           统一 User-Agent（与上游 a-stock-data SKILL 一致，覆盖 PC 主流浏览器）
- `EM_SESSION`   复用 Session（带自动重试 adapter）
- `em_get`       GET 入口（自动节流 1s + UA）
- `em_post`      POST 入口（自动节流 1s + UA）

节流间隔 / UA / 重试次数 / backoff 由环境变量控制（EM_MIN_INTERVAL_SEC / EM_USER_AGENT / EM_RETRY_*）。
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any

import requests

from datasource.resilience.ip_pool import proxy_for

logger = logging.getLogger(__name__)

# 延迟导入 urllib3 adapter，避免未装时整个包崩
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    _HAS_RETRY = True
except Exception:  # pragma: no cover
    _HAS_RETRY = False


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# ── 统一 UA ─────────────────────────────────────────────────────────────
UA: str = os.getenv("EM_USER_AGENT") or (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
)

_EM_RETRY_TOTAL = _env_int("EM_RETRY_TOTAL", 3)
_EM_RETRY_CONNECT = _env_int("EM_RETRY_CONNECT", 3)
_EM_RETRY_BACKOFF = _env_float("EM_RETRY_BACKOFF", 0.6)


# ── 复用 Session + 自动重试 ─────────────────────────────────────────────
def _build_session() -> requests.Session:
    """构造一个带 Retry adapter 的 Session。失败时降级为普通 session。"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    if _HAS_RETRY:
        try:
            adapter = HTTPAdapter(
                max_retries=Retry(
                    total=_EM_RETRY_TOTAL,
                    connect=_EM_RETRY_CONNECT,
                    backoff_factor=_EM_RETRY_BACKOFF,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET", "POST"],
                )
            )
            s.mount("https://", adapter)
            s.mount("http://", adapter)
        except Exception:  # pragma: no cover
            pass
    return s


EM_SESSION: requests.Session = _build_session()

# 最小请求间隔（秒）— 环境变量可配，默认 1.0
EM_MIN_INTERVAL: float = _env_float("EM_MIN_INTERVAL_SEC", 1.0)
# 东财调用时隙状态（同步 em_get/em_post 与异步 _http 共享，见 _throttle / throttle_async）
_em_last_call: list[float] = [0.0]
_throttle_lock = threading.Lock()


def _reserve_slot() -> float:
    """原子预留本次调用的起始时隙，返回本次需要等待的秒数（0 表示立即放行）。

    原「读-判-睡」实现下多个 to_thread 线程会同时通过间隔检查导致节流失效；
    改为锁内预留后，后续线程必须排队，最小间隔才真正生效。
    """
    with _throttle_lock:
        now = time.time()
        wait = max(0.0, EM_MIN_INTERVAL - (now - _em_last_call[0]))
        if wait > 0:
            wait += random.uniform(0.1, 0.5)  # 抖动防规律性
        _em_last_call[0] = now + wait
        return wait


def _throttle() -> None:
    """按 EM_MIN_INTERVAL 节流 + 随机抖动（同步版，供 em_get/em_post 使用）。"""
    wait = _reserve_slot()
    if wait > 0:
        time.sleep(wait)


async def throttle_async() -> None:
    """按 EM_MIN_INTERVAL 节流 + 随机抖动（异步版，供 _http.py 复用同一时隙状态）。

    与同步版共享 _em_last_call：同一进程内东财请求无论走 requests 还是 httpx，
    都受同一最小间隔约束，消除「双 HTTP 栈节流不一致」问题。
    """
    import asyncio

    wait = _reserve_slot()
    if wait > 0:
        await asyncio.sleep(wait)


def em_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs: Any,
) -> requests.Response:
    """东财统一 GET 入口：自动节流 + 复用 session + 默认 UA。

    已配置代理且域名在白名单时走代理（列表模式每请求轮换出口 IP）；
    代理传输失败自动降级直连重试一次。
    """
    _throttle()
    prox = proxy_for(url)
    if prox:
        try:
            return EM_SESSION.get(
                url, params=params, headers=headers, timeout=timeout, proxies=prox, **kwargs
            )
        except requests.exceptions.ConnectionError as exc:
            logger.warning("em_get 代理请求失败(%s)，直连兜底: %s", type(exc).__name__, url)
            _throttle()
    return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)


def em_post(
    url: str,
    data: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs: Any,
) -> requests.Response:
    """东财统一 POST 入口（cninfo / 同花顺类表单接口；代理同 em_get）。"""
    _throttle()
    merged_headers = {"User-Agent": UA, **(headers or {})}
    prox = proxy_for(url)
    if prox:
        try:
            return EM_SESSION.post(
                url, data=data, headers=merged_headers, timeout=timeout, proxies=prox, **kwargs
            )
        except requests.exceptions.ConnectionError as exc:
            logger.warning("em_post 代理请求失败(%s)，直连兜底: %s", type(exc).__name__, url)
            _throttle()
    return EM_SESSION.post(url, data=data, headers=merged_headers, timeout=timeout, **kwargs)
