"""mootdx 客户端工厂（规避 0.11.x BESTIP.HQ 空串 bug）。

提供：
- `_TDX_SERVERS` 国内通达信 7709 服务器列表（从 settings 加载，可配置化）
- `tdx_client(market)` 创建 mootdx 客户端，4 级兜底：
    1) 顺序探测 _TDX_SERVERS 第一个 TCP 可达；
    2) mootdx bestip 测速；
    3) 裸 factory；
    4) 仍失败 → RuntimeError。
"""
from __future__ import annotations

import logging
import socket
from typing import Any

from core.config import settings

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

_TDX_SERVERS: list[tuple[str, int]] = (
    settings.TDX_SERVERS if settings.TDX_SERVERS else _DEFAULT_TDX_SERVERS
)


def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
    """TCP 握手探测，判断通达信服务器是否可达。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (TimeoutError, OSError) as exc:
        logger.debug("TDX 探测 %s:%s 失败: %s", ip, port, exc)
        return False


def tdx_client(market: str = "std") -> Any:
    """创建 mootdx 客户端。

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
            f"请走国内代理或更新 settings.TDX_SERVERS 列表。原始错误：{exc}"
        ) from exc
