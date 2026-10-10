"""mootdx 客户端工厂（规避 0.11.x BESTIP.HQ 空串 bug）。

提供：
- `_TDX_SERVERS` 国内通达信 7709 服务器列表（TDX_SERVERS 环境变量可覆盖）
- `tdx_client(market)` 创建 mootdx 客户端，4 级兜底：
    1) 顺序探测 _TDX_SERVERS 第一个 TCP 可达；
    2) mootdx bestip 测速；
    3) 裸 factory；
    4) 仍失败 → RuntimeError。

实例按 market 缓存复用（避免每次调用都重新 TCP 探测 10 台服务器，
单次最坏可耗时 20s）。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# 行情 / 财务 K 线走 TCP 7709；不封 IP。配置化后默认仍是国内 10 个 IP。
_DEFAULT_TDX_SERVERS: list[tuple[str, int]] = [
    ("119.97.185.59", 7709),
    ("124.70.133.119", 7709),
    ("116.205.183.150", 7709),
    ("123.60.73.44", 7709),
    ("116.205.163.254", 7709),
    ("121.36.225.169", 7709),
    ("123.60.73.44", 7709),
    ("124.71.9.153", 7709),
    ("110.41.147.114", 7709),
    ("124.71.187.122", 7709),
]

def _load_tdx_servers() -> list[tuple[str, int]]:
    """从 TDX_SERVERS 环境变量加载（JSON 格式），失败用默认列表。"""
    raw = os.getenv("TDX_SERVERS")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [(str(h), int(p)) for h, p in parsed]
        except (ValueError, TypeError):
            logger.warning("TDX_SERVERS 解析失败，使用默认服务器列表")
    return _DEFAULT_TDX_SERVERS


_TDX_SERVERS: list[tuple[str, int]] = _load_tdx_servers()

# 已创建的 client 缓存（按 market），避免重复探测
_tdx_clients: dict[str, Any] = {}
_tdx_clients_lock = threading.Lock()
# 查询串行化锁：mootdx 单一 socket 连接协议非线程安全（与 baostock 同构），
# 并发调用必须整体串行化，否则请求会交错收发导致数据错乱。
_tdx_query_lock = threading.Lock()


def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
    """TCP 握手探测，判断通达信服务器是否可达。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (TimeoutError, OSError) as exc:
        logger.debug("TDX 探测 %s:%s 失败: %s", ip, port, exc)
        return False


def _create_client(market: str) -> Any:
    """创建 mootdx 客户端（不缓存）。

    顺序兜底（4 级）：
    1) 顺序探测 _TDX_SERVERS，第一个 TCP 可达的 server；
    2) 全部不可达 → mootdx bestip 测速；
    3) 仍失败 → 裸 factory（老用户 config 已有可用 BESTIP 时成立）；
    4) 仍失败 → 抛 RuntimeError。
    """
    from mootdx.quotes import Quotes  # 延迟导入：避免未装 mootdx 时整个包不可用

    for ip, port in _TDX_SERVERS:
        if _probe(ip, port):
            return Quotes.factory(market=market, server=(ip, port))
    try:
        return Quotes.factory(market=market, bestip=True)
    except Exception as exc:
        logger.debug("mootdx bestip=True 失败，尝试裸 factory: %s", exc)
    try:
        return Quotes.factory(market=market)
    except Exception as exc:
        raise RuntimeError(
            "所有 mootdx 服务器均不可达。海外网络通常全部超时（TCP 7709），"
            f"请走国内代理或通过环境变量 TDX_SERVERS 更新服务器列表。原始错误：{exc}"
        ) from exc


def tdx_client(market: str = "std") -> Any:
    """获取 mootdx 客户端（按 market 缓存复用，线程安全创建）。

    首次创建耗时较长（最多探测 10 台服务器 × 2s 超时），
    之后复用同一实例，避免每个行情请求都重新探测。
    """
    cached = _tdx_clients.get(market)
    if cached is not None:
        return cached
    with _tdx_clients_lock:
        cached = _tdx_clients.get(market)
        if cached is None:
            cached = _create_client(market)
            _tdx_clients[market] = cached
        return cached


def tdx_query(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """在全局串行化锁内执行 mootdx 客户端调用。

    mootdx 单一 socket 连接协议非线程安全，并发调用（经 to_thread 进入 worker 线程）
    会交错收发导致数据错乱——与 baostock 同构。所有对客户端的方法调用必须经此入口。
    """
    with _tdx_query_lock:
        client = tdx_client()
        return func(client, *args, **kwargs)
