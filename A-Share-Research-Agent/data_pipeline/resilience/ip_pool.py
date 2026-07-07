"""IP 池抽象 — Provider / 客户端侧 IP 切换。

设计：
- `IpPool` Protocol：select() 返回当前 IP，mark_success/mark_failure() 反馈
- `ProxyEnvIpPool`：默认实现，从环境变量读代理列表（HTTP_PROXY / HTTPS_PROXY / IP_POOL_LIST）
- `CffiImpersonateIpPool`：指纹伪装（不换 IP，绕反爬）

使用：
```python
pool = ProxyEnvIpPool()
ip = pool.select()                # 轮询/随机一个代理
httpx.get(url, proxy=ip.url)      # 实际请求
pool.mark_failure(ip)             # 失败 → 切下一个
```
"""
from __future__ import annotations

import logging
import os
import random
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .base import IpPoolConfig, ResilienceStats

logger = logging.getLogger(__name__)


@dataclass
class IpSelection:
    """一次 IP 选择结果。"""

    url: str           # 代理 URL（如 "http://127.0.0.1:7890"），空字符串 = 直连
    label: str         # 标签（用于日志 / 观测，如 "proxy-1" / "direct" / "chrome120"）
    impersonate: str | None = None  # curl_cffi impersonate 字符串（如 "chrome120"）


@runtime_checkable
class IpPool(Protocol):
    """IP 池 Protocol。"""

    def select(self) -> IpSelection: ...
    def mark_success(self, ip: IpSelection) -> None: ...
    def mark_failure(self, ip: IpSelection) -> None: ...
    def stats(self) -> dict[str, int]: ...


# ── 默认实现：环境变量代理池 ─────────────────────────────────────────

class ProxyEnvIpPool:
    """从环境变量读取代理列表，支持轮询 / 随机 / 加权切换。

    读取顺序（自动合并去重）：
    1. `IP_POOL_LIST`     逗号分隔的完整 URL 列表（最高优先）
    2. `HTTP_PROXY`       单个代理
    3. `HTTPS_PROXY`      单个代理
    4. `ALL_PROXY`        单个代理
    若都没设置 → 直连（无代理）
    """

    def __init__(self, config: IpPoolConfig | None = None) -> None:
        self.config = config or IpPoolConfig()
        self._proxies: list[str] = self._load_from_env()
        self._idx: int = 0
        self._failures: dict[str, int] = dict.fromkeys(self._proxies, 0)
        self._lock = threading.Lock()
        self._stats = ResilienceStats()

    @staticmethod
    def _load_from_env() -> list[str]:
        out: list[str] = []
        # 优先 IP_POOL_LIST（多个）
        pool = os.environ.get("IP_POOL_LIST", "").strip()
        if pool:
            out.extend(p.strip() for p in pool.split(",") if p.strip())
        # 单个代理环境变量（去重）
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            v = os.environ.get(key, "").strip()
            if v and v not in out:
                out.append(v)
        return out

    def select(self) -> IpSelection:
        self._stats.total_calls += 1
        if not self._proxies:
            return IpSelection(url="", label="direct")
        with self._lock:
            if self.config.strategy == "random":
                url = random.choice(self._proxies)
            elif self.config.strategy == "weighted":
                # 失败次数越少权重越高
                weights = [1.0 / (1 + self._failures.get(p, 0)) for p in self._proxies]
                url = random.choices(self._proxies, weights=weights, k=1)[0]
            else:  # round_robin
                url = self._proxies[self._idx % len(self._proxies)]
                self._idx += 1
        label = url.split("//")[-1].split(":")[0]  # 取 host
        return IpSelection(url=url, label=f"proxy-{label}")

    def mark_success(self, ip: IpSelection) -> None:
        if ip.url:
            with self._lock:
                self._failures[ip.url] = 0
        self._stats.success += 1

    def mark_failure(self, ip: IpSelection) -> None:
        if ip.url:
            with self._lock:
                self._failures[ip.url] = self._failures.get(ip.url, 0) + 1
            self._stats.ip_switches += 1
        self._stats.failed += 1

    def stats(self) -> dict[str, int]:
        return {
            "total_proxies": len(self._proxies),
            "total_calls": self._stats.total_calls,
            "success": self._stats.success,
            "failed": self._stats.failed,
            "ip_switches": self._stats.ip_switches,
        }


# ── 指纹伪装池（不换 IP，绕反爬） ────────────────────────────────────

class CffiImpersonateIpPool:
    """curl_cffi 指纹伪装池。

    不切 IP，但每次请求轮换 chrome 指纹版本，绕过东财等基于 TLS 指纹的反爬。
    需要先 `pip install curl-cffi`。
    """

    DEFAULT_PROFILES = ["chrome120", "chrome119", "chrome110", "edge101", "safari15_3"]

    def __init__(
        self,
        profiles: list[str] | None = None,
        config: IpPoolConfig | None = None,
    ) -> None:
        self.config = config or IpPoolConfig()
        self.profiles = profiles or self.DEFAULT_PROFILES
        self._idx: int = 0
        self._lock = threading.Lock()
        self._stats = ResilienceStats()
        # 探测依赖
        try:
            import curl_cffi  # noqa: F401
            self._available = True
        except ModuleNotFoundError:
            self._available = False
            logger.warning("CffiImpersonateIpPool: curl_cffi 未安装，指纹伪装不可用")

    @property
    def available(self) -> bool:
        return self._available

    def select(self) -> IpSelection:
        self._stats.total_calls += 1
        if not self._available or not self.profiles:
            return IpSelection(url="", label="direct", impersonate=None)
        with self._idx_lock():  # type: ignore[misc]
            profile = self.profiles[self._idx % len(self.profiles)]
            self._idx += 1
        return IpSelection(url="", label=f"cffi-{profile}", impersonate=profile)

    # 用 threading.Lock 而不是 asyncio.Lock（select 同步即可）
    def _idx_lock(self) -> threading.Lock:
        return self._lock  # type: ignore[return-value]

    def mark_success(self, ip: IpSelection) -> None:
        self._stats.success += 1

    def mark_failure(self, ip: IpSelection) -> None:
        self._stats.failed += 1
        self._stats.ip_switches += 1

    def stats(self) -> dict[str, int]:
        return {
            "available": self._available,
            "profiles": len(self.profiles),
            "total_calls": self._stats.total_calls,
            "ip_switches": self._stats.ip_switches,
        }
