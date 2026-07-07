"""东财 HTTP 统一节流入口（Keep-Alive session + Retry + 最小间隔 + 随机抖动）。

提供：
- `UA`           统一 User-Agent（与上游 a-stock-data SKILL 一致，覆盖 PC 主流浏览器）
- `EM_SESSION`   复用 Session（带自动重试 adapter）
- `em_get`       GET 入口（自动节流 1s + UA）
- `em_post`      POST 入口（自动节流 1s + UA）

节流间隔 / UA / 重试次数 / backoff 都已抽到 core.config，
通过 `settings.EM_MIN_INTERVAL_SEC` / `EM_USER_AGENT` 调整。
"""
from __future__ import annotations

import random
import time
from typing import Any

import requests

from core.config import settings

# 延迟导入 urllib3 adapter，避免未装时整个包崩
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    _HAS_RETRY = True
except Exception:  # pragma: no cover
    _HAS_RETRY = False


# ── 统一 UA ─────────────────────────────────────────────────────────────
UA: str = settings.EM_USER_AGENT or (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
)


# ── 复用 Session + 自动重试 ─────────────────────────────────────────────
def _build_session() -> requests.Session:
    """构造一个带 Retry adapter 的 Session。失败时降级为普通 session。"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    if _HAS_RETRY:
        try:
            adapter = HTTPAdapter(
                max_retries=Retry(
                    total=settings.EM_RETRY_TOTAL,
                    connect=settings.EM_RETRY_CONNECT,
                    backoff_factor=settings.EM_RETRY_BACKOFF,
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

# 最小请求间隔（秒）— 配置化，默认 1.0
EM_MIN_INTERVAL: float = float(settings.EM_MIN_INTERVAL_SEC)
_em_last_call: list[float] = [0.0]


def _throttle() -> None:
    """按 EM_MIN_INTERVAL 节流 + 随机抖动。"""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))


def em_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs: Any,
) -> requests.Response:
    """东财统一 GET 入口：自动节流 + 复用 session + 默认 UA。"""
    _throttle()
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


def em_post(
    url: str,
    data: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs: Any,
) -> requests.Response:
    """东财统一 POST 入口（cninfo / 同花顺类表单接口）。"""
    _throttle()
    merged_headers = {"User-Agent": UA, **(headers or {})}
    try:
        return EM_SESSION.post(url, data=data, headers=merged_headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()
