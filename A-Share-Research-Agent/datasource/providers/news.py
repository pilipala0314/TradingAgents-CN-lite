"""News Provider — 多源财经新闻/快讯聚合。

数据源：
- 财联社电报（实时快讯，秒级延迟）
- 新浪财经 7x24 快讯
- 东方财富个股新闻

特点：
- 异步化（原 cls.py 是同步的）
- 多源聚合 + 去重
- 支持按关键词/股票代码过滤
- 支持重要级别过滤（财联社 level 1-5）
"""
from __future__ import annotations

import json
import logging
from typing import Any

from datasource.errors import ProviderError
from datasource.providers._http import DEFAULT_HEADERS, http_get_json, http_get_text
from datasource.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider("news")
class NewsProvider:
    """多源财经新闻 Provider。"""

    @property
    def name(self) -> str:
        return "news"

    async def health_check(self) -> bool:
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "news" in provs)

    async def get_news_flash(
        self,
        limit: int = 30,
        source: str = "auto",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取实时财经快讯。

        Args:
            limit: 返回条数（最大 50）
            source: 数据源 "cls"=财联社 "sina"=新浪7x24
        """
        if source == "sina":
            return await self._fetch_sina_7x24(limit, method="get_news_flash")
        if source == "cls":
            return await self._fetch_cls_telegraph(limit, method="get_news_flash")

        cls_error = None
        try:
            items = await self._fetch_cls_telegraph(limit, method="get_news_flash")
            if items:
                return items
        except Exception as exc:
            cls_error = exc
        try:
            return await self._fetch_sina_7x24(limit, method="get_news_flash")
        except Exception as exc:
            raise ProviderError(
                f"实时快讯获取失败: cls={cls_error} sina={exc}",
                provider="news",
                method="get_news_flash",
                cause=exc,
            ) from exc

    async def get_stock_news(
        self,
        symbol: str,
        limit: int = 20,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取个股相关新闻。

        Args:
            symbol: 6 位股票代码
            limit: 返回条数
        """
        try:
            url = "https://search-api-web.eastmoney.com/search/jsonp"
            code = symbol.strip().split(".")[0]
            # 用 json.dumps 构造 param（避免手拼 JSON 的引号/注入风险）
            param = {
                "uid": "",
                "keyword": code,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    "cmsArticleWebOld": {
                        "searchScope": "default",
                        "sort": "default",
                        "pageIndex": 1,
                        "pageSize": limit,
                        "preTag": "",
                        "postTag": "",
                    }
                },
            }
            params = {"cb": "jQuery", "param": json.dumps(param, ensure_ascii=False)}
            headers = {**DEFAULT_HEADERS, "Referer": "https://so.eastmoney.com/"}
            # 复用全局 httpx 连接池（勿每请求新建 client）
            text = await http_get_text(url, params=params, headers=headers, timeout=10)

            # JSONP 解析
            if text.startswith("jQuery"):
                text = text[len("jQuery"):].lstrip("(").rstrip(");\n").strip()
            payload = json.loads(text)
            result = (payload.get("result") or {}).get("cmsArticleWebOld") or {}
            items = result.get("list", []) if isinstance(result, dict) else []

            return [
                {
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "published_at": it.get("showTime", ""),
                    "source": it.get("mediaName", "东方财富"),
                    "content": "",
                }
                for it in (items or [])
            ]
        except Exception as exc:
            raise ProviderError(
                f"个股新闻获取失败({symbol}): {exc}",
                provider="news", method="get_stock_news", cause=exc,
            ) from exc

    async def get_global_news(
        self,
        limit: int = 30,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取全球宏观新闻（财联社 + 新浪聚合）。"""
        cls_error = None
        try:
            items = await self._fetch_cls_telegraph(limit, method="get_global_news")
            if items:
                return items
        except Exception as exc:
            cls_error = exc
        try:
            return await self._fetch_sina_7x24(limit, method="get_global_news")
        except Exception as exc:
            raise ProviderError(
                f"全球新闻获取失败: cls={cls_error} sina={exc}",
                provider="news",
                method="get_global_news",
                cause=exc,
            ) from exc

    async def get_global_macro_news(
        self,
        business_date: str | None = None,
        look_back_days: int = 7,
        per_day_limit: int = 10,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """get_global_macro_news 路由兼容端点（签名与 akshare 同名方法对齐）。

        财联社/新浪快讯为实时流，不支持按日期回看历史窗口，
        business_date / look_back_days 忽略，per_day_limit 映射为拉取条数。
        """
        return await self.get_global_news(limit=max(per_day_limit, 10))

    # ── 内部方法 ──────────────────────────────────────────────────

    async def _fetch_cls_telegraph(self, limit: int = 30, *, method: str = "get_news_flash") -> list[dict[str, Any]]:
        """财联社电报（异步化）。"""
        url = "https://www.cls.cn/nodeapi/updateTelegraphList"
        headers = {
            **DEFAULT_HEADERS,
            "Referer": "https://www.cls.cn/telegraph",
            "Origin": "https://www.cls.cn",
        }
        params = {
            "app": "CailianpressWeb",
            "category": "",
            "lastTime": "",
            "last_id": "",
            "rn": str(min(limit, 50)),
            "os": "web",
            "sv": "7.7.5",
        }
        try:
            payload = await http_get_json(url, params=params, headers=headers, timeout=15)
            if payload.get("errno") not in (0, None):
                raise RuntimeError(f"errno={payload.get('errno')}")
            raw_items = (payload.get("data") or {}).get("roll_data") or []
            return [
                {
                    "title": item.get("title") or item.get("brief", ""),
                    "content": item.get("content", ""),
                    "published_at": item.get("ctime"),
                    "url": f"https://www.cls.cn/detail/{item.get('id')}" if item.get("id") else "",
                    "level": item.get("level"),
                    "subject": item.get("subject"),
                    "source": "财联社",
                }
                for item in raw_items
            ]
        except Exception as exc:
            raise ProviderError(
                f"财联社电报获取失败: {exc}",
                provider="news", method=method, cause=exc,
            ) from exc

    async def _fetch_sina_7x24(self, limit: int = 30, *, method: str = "get_news_flash") -> list[dict[str, Any]]:
        """新浪财经 7x24 快讯。"""
        url = "https://zhibo.sina.com.cn/api/zhibo/feed"
        params = {
            "page": "1",
            "page_size": str(min(limit, 50)),
            "zhibo_id": "152",
            "tag_id": "0",
            "type": "0",
        }
        try:
            payload = await http_get_json(url, params=params, timeout=10)
            result = payload.get("result", {})
            data = result.get("data", {})
            feed_list = data.get("feed", {}).get("list", [])
            return [
                {
                    "title": item.get("rich_text", "")[:100],
                    "content": item.get("rich_text", ""),
                    "published_at": item.get("create_time", ""),
                    "url": item.get("docurl", ""),
                    "level": None,
                    "subject": item.get("tag", ""),
                    "source": "新浪7x24",
                }
                for item in feed_list
            ]
        except Exception as exc:
            raise ProviderError(
                f"新浪7x24快讯获取失败: {exc}",
                provider="news", method=method, cause=exc,
            ) from exc
