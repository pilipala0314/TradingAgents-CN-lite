"""东财 HTTP 统一节流入口（Keep-Alive session + 最小间隔 + 随机抖动）。

提供：
- `UA`           统一 User-Agent（与上游 a-stock-data SKILL 一致，覆盖 PC 主流浏览器）
- `EM_SESSION`   复用 Session（连接池复用，**不含重试**）
- `em_get`       GET 入口（自动节流 + UA + 代理路由）
- `em_post`      POST 入口（同上）

重试策略：**单一权威层**。本模块不做任何重试，重试统一由上层
`datasource.resilience.RetryPolicy` 负责。原因见 `_build_session`。

节流间隔 / UA / 连接池大小由环境变量控制（EM_MIN_INTERVAL_SEC / EM_USER_AGENT / EM_SESSION_POOL_SIZE）。
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

# urllib3 adapter 仅用于连接池复用（不挂重试）
try:
    from requests.adapters import HTTPAdapter

    _HAS_ADAPTER = True
except Exception:  # pragma: no cover
    _HAS_ADAPTER = False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# ── 统一 UA ─────────────────────────────────────────────────────────────
UA: str = os.getenv("EM_USER_AGENT") or (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
)

# 代理传输失败是否直连兜底（默认关闭）。
# 走代理的域名正是「直连被 WAF 封锁」的域名 → 直连兜底几乎必失败，
# 只会白等一个超时并多打一次无效请求。若白名单里混入了「直连也可用」的域名，
# 可置 true 恢复该行为。
_PROXY_DIRECT_FALLBACK: bool = _env_bool("DATASOURCE_PROXY_DIRECT_FALLBACK", False)

_SESSION_POOL_SIZE: int = _env_int("EM_SESSION_POOL_SIZE", 20)


# ── 复用 Session（仅连接池，无重试）─────────────────────────────────────
def _build_session() -> requests.Session:
    """构造仅含连接池复用的 Session。

    **刻意不挂 urllib3 ``Retry``**：本项目按请求付费走隧道代理，urllib3 内部
    重试会与上层 resilience 重试叠加（``total=3`` × resilience ``3`` = 9 次/逻辑调用），
    直接放大代理开销；且 ``status_forcelist=[429]`` 会把隧道限流的 429 静默重试，
    既烧钱又掩盖限流信号。重试统一收敛到 ``resilience.RetryPolicy`` 单一层次
    （可在那里按错误类型区分可重试性、可与熔断/限流协同）。
    """
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    if _HAS_ADAPTER:
        try:
            adapter = HTTPAdapter(
                pool_connections=10,
                pool_maxsize=_SESSION_POOL_SIZE,
                max_retries=0,  # 重试权交给 resilience 层
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

    已配置代理且域名在白名单时走代理（列表模式每请求轮换出口 IP）。
    代理传输失败默认**不**直连兜底（东财直连必被 WAF 拒，兜底纯浪费）——
    异常向上抛出，由 resilience 层重试（隧道每请求换 IP，重试即换出口）。
    需要直连兜底时置 ``DATASOURCE_PROXY_DIRECT_FALLBACK=true``。
    """
    _throttle()
    prox = proxy_for(url)
    if prox:
        try:
            return EM_SESSION.get(
                url, params=params, headers=headers, timeout=timeout, proxies=prox, **kwargs
            )
        except requests.exceptions.ConnectionError as exc:
            # 代理出口失效 → 摘除，下次调用自动提取新 IP（提取型）；默认不直连兜底
            from datasource.resilience.ip_pool import report_proxy_failure

            report_proxy_failure(prox)
            if not _PROXY_DIRECT_FALLBACK:
                raise
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
            from datasource.resilience.ip_pool import report_proxy_failure

            report_proxy_failure(prox)
            if not _PROXY_DIRECT_FALLBACK:
                raise
            logger.warning("em_post 代理请求失败(%s)，直连兜底: %s", type(exc).__name__, url)
            _throttle()
    return EM_SESSION.post(url, data=data, headers=merged_headers, timeout=timeout, **kwargs)
