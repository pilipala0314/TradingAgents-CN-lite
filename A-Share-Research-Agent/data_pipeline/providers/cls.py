"""财联社电报 — `https://www.cls.cn/nodeapi/updateTelegraphList`。

返回结构: list[{title, content, published_at, url, level, subject}, ...]
- level: 重要级（1-5）
- subject: 主题分类

接口限制: 财联社公开接口需带 Referer 头 + Cookie 否则 401。失败时直接报错。
"""
from __future__ import annotations

import logging
from typing import Any

from data_pipeline.providers._shared import em_get

logger = logging.getLogger(__name__)

CLS_URL = "https://www.cls.cn/nodeapi/updateTelegraphList"
CLS_REFERER = "https://www.cls.cn/telegraph"
CLS_HEADERS = {
    "Referer": CLS_REFERER,
    "Origin": "https://www.cls.cn",
    "Accept": "application/json, text/plain, */*",
}


def fetch_cls_telegraph(limit: int = 30, last_id: str | None = None) -> list[dict[str, Any]]:
    """财联社快讯电报。

    Args:
        limit:    返回条数（最大 50）
        last_id:  翻页游标（None 取最新）
    """
    if limit <= 0 or limit > 50:
        raise ValueError("limit 必须在 1..50 之间")
    params = {
        "app": "CailianpressWeb",
        "category": "",
        "lastTime": last_id or "",
        "last_id": last_id or "",
        "rn": str(limit),
        "os": "web",
        "sv": "7.7.5",
    }
    resp = em_get(CLS_URL, params=params, headers=CLS_HEADERS, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(
            f"财联社接口 HTTP {resp.status_code}, 可能是 Referer/Cookie 校验失败"
        )
    payload = resp.json()
    if payload.get("errno") not in (0, None):
        raise RuntimeError(f"财联社接口业务错误, errno={payload.get('errno')}, msg={payload.get('msg')}")
    raw_items = (payload.get("data") or {}).get("roll_data") or []
    if not raw_items:
        raise RuntimeError("财联社接口未返回 roll_data 列表")
    results: list[dict[str, Any]] = []
    for item in raw_items:
        results.append(
            {
                "title": item.get("title") or item.get("brief", ""),
                "content": item.get("content", ""),
                "published_at": item.get("ctime"),
                "url": f"https://www.cls.cn/detail/{item.get('id')}" if item.get("id") else None,
                "level": item.get("level"),
                "subject": item.get("subject"),
                "source": "财联社",
            }
        )
    logger.info("财联社电报拉取完成, count=%d", len(results))
    return results
