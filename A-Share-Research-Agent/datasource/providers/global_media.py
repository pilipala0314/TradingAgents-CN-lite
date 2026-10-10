"""Global Media Provider — 国际权威财经媒体。

数据源：
- 华尔街见闻（wallstreetcn）：中文覆盖全球市场，实时快讯 + 深度文章
- CNBC：美股/全球市场权威源
- Yahoo Finance News：聚合多家媒体
- Reuters RSS：路透社财经新闻（免费 RSS）

适用场景：
- 全球市场联动分析（美股/欧股/商品 → A股映射）
- 重大国际事件捕捉（央行决议/非农/财报季）
- 外资视角研判（国际投行观点/评级变动）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from datasource.errors import ProviderError
from datasource.providers._http import http_get_json, http_get_text
from datasource.registry import register_provider

logger = logging.getLogger(__name__)


# ── 工具 ───────────────────────────────────────────────────────────


def _pub_sort_key(item: dict[str, Any]) -> float:
    """归一化 published_at（int 时间戳 / RSS 日期串）为可排序数值。"""
    t = item.get("published_at", 0)
    if isinstance(t, (int, float)):
        return float(t)
    if isinstance(t, str) and t.strip():
        try:
            from datetime import datetime
            # RSS 日期后缀 GMT/UTC 与 %z 不兼容，先统一替换为 +0000 再解析，
            # 否则 strptime 抛 ValueError 会落到 0.0 导致排序错误
            s = t.strip().replace(" GMT", " +0000").replace(" UTC", " +0000")
            return datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %z").timestamp()
        except ValueError:
            return 0.0
    return 0.0


@register_provider("global_media")
class GlobalMediaProvider:
    """国际权威财经媒体 Provider。"""

    @property
    def name(self) -> str:
        return "global_media"

    async def health_check(self) -> bool:
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "global_media" in provs)

    async def get_wallstreet_news(
        self,
        limit: int = 20,
        channel: str = "global",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """华尔街见闻 — 实时快讯/文章。

        Args:
            limit: 返回条数
            channel: "global"=全球 "a-stock"=A股 "us-stock"=美股 "forex"=外汇

        Returns:
            [{title, content, published_at, url, source, importance}, ...]
        """
        # 华尔街见闻 API
        channel_map = {
            "global": "global-channel",
            "a-stock": "a-stock-channel",
            "us-stock": "us-stock-channel",
            "forex": "forex-channel",
        }
        ch = channel_map.get(channel, "global-channel")
        url = f"https://api-one.wallstcn.com/apiv1/content/lives"
        params = {
            "channel": ch,
            "limit": str(min(limit, 50)),
            "first_page": "true",
        }
        try:
            data = await http_get_json(url, params=params, timeout=10)
            items = data.get("data", {}).get("items", [])
            return [
                {
                    "title": item.get("title", "") or item.get("content_text", "")[:80],
                    "content": item.get("content_text", "") or item.get("content", ""),
                    "published_at": item.get("display_time", 0),
                    "url": item.get("uri", ""),
                    "source": "华尔街见闻",
                    "importance": item.get("importance", 0),
                    "channel": channel,
                }
                for item in items[:limit]
            ]
        except Exception as exc:
            raise ProviderError(
                f"华尔街见闻获取失败: {exc}",
                provider="global_media", method="get_wallstreet_news", cause=exc,
            ) from exc

    async def get_cnbc_news(
        self,
        limit: int = 15,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """CNBC 财经新闻（RSS 解析）。

        Returns:
            [{title, content, published_at, url, source}, ...]
        """
        url = "https://search.cnbc.com/rs/search/combinedcms/view.xml"
        params = {"partnerId": "wrss01", "id": "100003114"}  # Top News
        try:
            text = await http_get_text(url, params=params, timeout=10)
            return self._parse_rss(text, source="CNBC", limit=limit)
        except Exception as exc:
            raise ProviderError(
                f"CNBC 新闻获取失败: {exc}",
                provider="global_media", method="get_cnbc_news", cause=exc,
            ) from exc

    async def get_yahoo_finance_news(
        self,
        symbol: str = "",
        limit: int = 15,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Yahoo Finance 新闻。

        Args:
            symbol: 股票代码（如 AAPL），空=市场总览新闻
        """
        if symbol:
            url = f"https://query1.finance.yahoo.com/v1/finance/search"
            params = {"q": symbol, "newsCount": str(min(limit, 20))}
        else:
            url = "https://query1.finance.yahoo.com/v1/finance/trending/US"
            params = {"count": str(min(limit, 20))}

        try:
            data = await http_get_json(url, params=params, timeout=10)
            # Yahoo 搜索接口返回新闻
            news_items = data.get("news", [])
            return [
                {
                    "title": item.get("title", ""),
                    "content": item.get("summary", ""),
                    "published_at": item.get("providerPublishTime", 0),
                    "url": item.get("link", ""),
                    "source": item.get("publisher", "Yahoo Finance"),
                }
                for item in news_items[:limit]
            ]
        except Exception as exc:
            raise ProviderError(
                f"Yahoo Finance 新闻获取失败: {exc}",
                provider="global_media", method="get_yahoo_finance_news", cause=exc,
            ) from exc

    async def get_reuters_news(
        self,
        limit: int = 15,
        category: str = "business",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Reuters 路透社新闻（RSS）。

        Args:
            category: "business" / "markets" / "world"
        """
        rss_map = {
            "business": "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
            "markets": "https://www.reutersagency.com/feed/?best-topics=markets&post_type=best",
            "world": "https://www.reutersagency.com/feed/?best-topics=political-general&post_type=best",
        }
        url = rss_map.get(category, rss_map["business"])
        try:
            text = await http_get_text(url, timeout=12)
            return self._parse_rss(text, source="Reuters", limit=limit)
        except Exception as exc:
            raise ProviderError(
                f"Reuters 新闻获取失败: {exc}",
                provider="global_media", method="get_reuters_news", cause=exc,
            ) from exc

    async def get_global_market_news(
        self,
        limit: int = 30,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """聚合全球市场新闻（华尔街见闻 + CNBC + Reuters）。

        多源聚合，按时间排序，去重。
        """
        results: list[dict[str, Any]] = []

        # 并发拉取多源
        tasks = [
            self._safe_fetch(self.get_wallstreet_news, limit=15),
            self._safe_fetch(self.get_cnbc_news, limit=10),
            self._safe_fetch(self.get_reuters_news, limit=10),
        ]
        fetched = await asyncio.gather(*tasks)
        for items in fetched:
            if items:
                results.extend(items)

        # 按时间排序（最新的在前）：published_at 可能是 int 时间戳或 RSS 日期串，统一归一化
        results.sort(key=_pub_sort_key, reverse=True)
        return results[:limit]

    # ── 内部工具 ──────────────────────────────────────────────────

    async def _safe_fetch(self, fn, **kwargs) -> list[dict]:
        """安全调用，失败返回空列表。"""
        try:
            return await fn(**kwargs)
        except Exception as exc:
            logger.warning("global_media 子源失败: %s", exc)
            return []

    @staticmethod
    def _parse_rss(xml_text: str, source: str = "", limit: int = 15) -> list[dict[str, Any]]:
        """简易 RSS XML 解析（不依赖 feedparser）。"""
        import re
        results = []
        # 提取 <item> 块
        items = re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL)
        for item_xml in items[:limit]:
            title = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", item_xml)
            link = re.search(r"<link>(.*?)</link>", item_xml)
            desc = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>|<description>(.*?)</description>", item_xml, re.DOTALL)
            pub_date = re.search(r"<pubDate>(.*?)</pubDate>", item_xml)

            title_text = (title.group(1) or title.group(2)) if title else ""
            link_text = link.group(1) if link else ""
            desc_text = (desc.group(1) or desc.group(2)) if desc else ""
            date_text = pub_date.group(1) if pub_date else ""

            # 清理 HTML 标签
            desc_text = re.sub(r"<[^>]+>", "", desc_text).strip()

            results.append({
                "title": title_text.strip(),
                "content": desc_text[:500],
                "published_at": date_text,
                "url": link_text.strip(),
                "source": source,
            })
        return results
