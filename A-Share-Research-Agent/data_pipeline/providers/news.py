"""新闻层：东财个股新闻（最简版，仅取标题+时间，不解析富文本）。"""

from __future__ import annotations

import logging
from typing import Any

from ._shared import UA, em_get, em_market_code, normalize_ticker

logger = logging.getLogger(__name__)


def fetch_stock_news(code: str, page_size: int = 20) -> list[dict[str, Any]]:
    """东财个股新闻流（search-api-web JSONP）。

    返回: [{title, url, show_time, media_name}, ...]
    """
    code = normalize_ticker(code)
    em_market_code(code)
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    params = {
        "cb": "jQuery",
        "param": (
            '{"uid":"","keyword":"' + code + '","type":["cmsArticleWebOld"],'
            '"client":"web","clientType":"web","clientVersion":"curr",'
            '"param":{"cmsArticleWebOld":{"searchScope":"default",'
            '"sort":"default","pageIndex":1,"pageSize":' + str(page_size) + ','
            '"preTag":"<em>","postTag":"</em>"}}}'
        ),
    }
    headers = {"User-Agent": UA, "Referer": "https://so.eastmoney.com/"}
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        text = r.text
    except Exception as exc:
        # 网络/解析错误 → raise（DataRouter 走 fallback）
        raise RuntimeError(f"eastmoney_stock_news({code}) failed: {exc}") from exc

    # JSONP 解析：去掉 jQuery( ... );
    if text.startswith("jQuery"):
        text = text[len("jQuery"):].lstrip("(").rstrip(");\n").strip()
    import json

    try:
        payload = json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"news jsonp parse failed: {exc}") from exc

    result = (payload.get("result") or {}).get("cmsArticleWebOld") or []
    items = result.get("list", []) or [] if isinstance(result, dict) else result

    news: list[dict[str, Any]] = []
    for it in items:
        news.append(
            {
                "title": it.get("title", "") or it.get("art_title", ""),
                "url": it.get("url", ""),
                "show_time": it.get("showTime", "") or it.get("show_time", ""),
                "media_name": it.get("mediaName", "") or it.get("media_name", ""),
            }
        )
    return news
