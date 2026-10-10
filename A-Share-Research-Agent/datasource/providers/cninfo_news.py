"""CNINFO News Provider — 巨潮资讯网新闻资讯。

数据源：
- 巨潮资讯网（cninfo.com.cn）：深交所指定信息披露平台
- 覆盖：上市公司公告 + 监管动态 + 行业资讯

与旧 cninfo provider 的区别：
- 旧 provider（桥接）：仅支持个股公告查询
- 本 provider：新增资讯频道（监管动态/行业快讯/政策解读）

适用场景：
- 监管动态追踪（问询函/关注函/监管函）
- 行业政策发布（交易所通知/行业指引）
- 个股公告全量检索（比旧版更丰富的过滤条件）
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from datasource.errors import ProviderError
from datasource.providers._http import http_get_json, http_post_form, DEFAULT_HEADERS
from datasource.providers._shared.utils import CNINFO_ALL_MARKETS, cninfo_market_params
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

_CNINFO_BASE = "http://www.cninfo.com.cn/new"
_CNINFO_API = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
# 注意：不在这里手工设置 Content-Type —— 表单 POST 的编码类型由 httpx 自动附加
_CNINFO_HEADERS = {
    **DEFAULT_HEADERS,
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Origin": "http://www.cninfo.com.cn",
}


@register_provider("cninfo_news")
class CninfoNewsProvider:
    """巨潮资讯网新闻资讯 Provider。"""

    @property
    def name(self) -> str:
        return "cninfo_news"

    async def health_check(self) -> bool:
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "cninfo_news" in provs)

    async def get_cninfo_news(
        self,
        limit: int = 20,
        category: str = "",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取巨潮资讯（监管动态/行业快讯）。

        Args:
            limit: 返回条数
            category: "监管" / "行业" / "政策"，空=全部

        Returns:
            [{title, url, published_at, source, category}, ...]
        """
        # 巨潮资讯列表页 API
        url = f"{_CNINFO_BASE}/fulltextSearch/full"
        params = {
            "searchkey": category or "监管",
            "sdate": (date.today() - timedelta(days=7)).isoformat(),
            "edate": date.today().isoformat(),
            "isfulltext": "false",
            "sortName": "pubdate",
            "sortType": "desc",
            "pageNum": "1",
            "pageSize": str(min(limit, 30)),
        }
        try:
            data = await http_get_json(url, params=params, headers=_CNINFO_HEADERS, timeout=12)
            items = data.get("announcements", []) or data.get("data", [])
            return [
                {
                    "title": item.get("announcementTitle", item.get("title", "")),
                    "url": f"http://www.cninfo.com.cn/new/disclosure/detail?annoId={item.get('annoId', '')}" if item.get("annoId") else item.get("url", ""),
                    "published_at": item.get("announcementTime", item.get("pubdate", "")),
                    "source": "巨潮资讯",
                    "category": category or "综合",
                    "sec_name": item.get("secName", ""),
                }
                for item in items[:limit]
            ]
        except Exception as exc:
            raise ProviderError(
                f"巨潮资讯获取失败: {exc}",
                provider="cninfo_news", method="get_cninfo_news", cause=exc,
            ) from exc

    async def get_cninfo_regulatory(
        self,
        limit: int = 20,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取监管动态（问询函/关注函/监管函/处罚决定）。

        这些是交易所发出的监管文件，对个股风险预警极有价值。
        三个市场分库查询后合并（巨潮按市场分库，只查一个市场会漏另外两个）。
        """
        url = _CNINFO_API
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        per_market = max(1, min(limit, 30))
        for column, plate in CNINFO_ALL_MARKETS:
            form_data = {
                "pageNum": "1",
                "pageSize": str(per_market),
                "column": column,
                "tabName": "fulltext",
                "plate": plate,
                "stock": "",
                "searchkey": "问询函 OR 关注函 OR 监管函",
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": f"{(date.today() - timedelta(days=30)).isoformat()}~{date.today().isoformat()}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            try:
                # 巨潮 hisAnnouncement/query 是表单接口，必须用表单编码提交（不能用 JSON body）
                data = await http_post_form(url, form_data=form_data, headers=_CNINFO_HEADERS, timeout=12)
                items = data.get("announcements", [])
            except Exception as exc:
                # 单市场失败不影响其余市场（监管动态是 best-effort 聚合）
                logger.debug("cninfo 监管动态 %s 市场查询失败: %s", column, exc)
                continue
            for item in items:
                anno_id = item.get("annoId", "")
                if anno_id and anno_id in seen_ids:
                    continue  # 跨市场去重
                if anno_id:
                    seen_ids.add(anno_id)
                results.append({
                    "title": item.get("announcementTitle", "").replace("<em>", "").replace("</em>", ""),
                    "url": f"http://static.cninfo.com.cn/{item.get('adjunctUrl', '')}" if item.get("adjunctUrl") else "",
                    "published_at": item.get("announcementTime", ""),
                    "source": "巨潮-监管",
                    "category": "监管函",
                    "sec_name": item.get("secName", ""),
                    "sec_code": item.get("secCode", ""),
                })
        # 按发布时间倒序取前 limit 条
        results.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        return results[:limit]

    async def get_cninfo_announcements_full(
        self,
        symbol: str = "",
        days: int = 30,
        category: str = "",
        limit: int = 30,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """巨潮公告全量检索（增强版）。

        Args:
            symbol: 股票代码（空=全市场）
            days: 回溯天数
            category: 公告类别（年报/半年报/季报/其他）
            limit: 返回条数
        """
        url = _CNINFO_API
        category_map = {
            "年报": "category_ndbg_szsh",
            "半年报": "category_bndbg_szsh",
            "季报": "category_yjdbg_szsh",
            "": "",
        }
        code = symbol.strip().split(".")[0].zfill(6) if symbol else ""
        # 巨潮按市场分库：指定 symbol 时只查其所属市场；symbol 为空时遍历三市场合并
        markets = [cninfo_market_params(code)] if code else list(CNINFO_ALL_MARKETS)
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        per_market = max(1, min(limit, 50))
        succeeded = 0
        last_exc: Exception | None = None
        for column, plate in markets:
            form_data = {
                "pageNum": "1",
                "pageSize": str(per_market),
                "column": column,
                "tabName": "fulltext",
                "plate": plate,
                "stock": code,
                "searchkey": "",
                "secid": "",
                "category": category_map.get(category, ""),
                "trade": "",
                "seDate": f"{(date.today() - timedelta(days=days)).isoformat()}~{date.today().isoformat()}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            try:
                data = await http_post_form(url, form_data=form_data, headers=_CNINFO_HEADERS, timeout=12)
                items = data.get("announcements", [])
            except Exception as exc:
                logger.debug("cninfo 公告检索 %s 市场失败: %s", column, exc)
                last_exc = exc
                continue
            succeeded += 1
            for item in items or []:
                anno_id = item.get("annoId", "")
                if anno_id and anno_id in seen_ids:
                    continue  # 跨市场去重
                if anno_id:
                    seen_ids.add(anno_id)
                results.append({
                    "title": item.get("announcementTitle", "").replace("<em>", "").replace("</em>", ""),
                    "url": f"http://static.cninfo.com.cn/{item.get('adjunctUrl', '')}" if item.get("adjunctUrl") else "",
                    "published_at": item.get("announcementTime", ""),
                    "source": "巨潮公告",
                    "category": category or "全部",
                    "sec_name": item.get("secName", ""),
                    "sec_code": item.get("secCode", ""),
                    "announcement_id": anno_id,
                })
        if succeeded == 0 and last_exc is not None:
            # 全部市场查询都失败 → 真实故障，抛错（区别于"查询成功但无公告"）
            raise ProviderError(
                f"巨潮公告检索失败（全部市场不可用）: {last_exc}",
                provider="cninfo_news", method="get_cninfo_announcements_full", cause=last_exc,
            ) from last_exc
        results.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        return results[:limit]
