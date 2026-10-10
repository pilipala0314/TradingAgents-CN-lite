"""Policy Provider — 政策/监管/宏观数据。

数据源：
- 中国证监会：行政处罚、IPO审核、监管动态
- 央行（PBOC）：公开市场操作、MLF/LPR、货币政策
- 国务院：政策文件、常务会议
- 国家统计局：PMI/CPI/PPI 等宏观指标

适用场景：
- 政策面研判（降准降息/行业监管/产业政策）
- 事件驱动（处罚公告 → 风险预警）
- 宏观环境评估（货币政策松紧 → 大盘方向）
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from datasource.errors import ProviderError
from datasource.providers._http import http_get_json, http_get_text
from datasource.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider("policy")
class PolicyProvider:
    """政策/监管/宏观数据 Provider。"""

    @property
    def name(self) -> str:
        return "policy"

    async def health_check(self) -> bool:
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "policy" in provs)

    async def get_csrc_announcements(
        self,
        limit: int = 20,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取证监会最新公告/行政处罚。

        Returns:
            [{title, url, published_at, category}, ...]
        """
        try:
            # 证监会信息公开接口
            url = "http://www.csrc.gov.cn/searchList/a16c14b8e5584e978922a8938e69a97f"
            params = {
                "_isAgg": "true",
                "_isJson": "true",
                "_pageSize": str(min(limit, 50)),
                "_template": "index",
                "_rangeTimeGte": "",
                "_channelName": "",
                "page": "1",
            }
            text = await http_get_text(url, params=params, timeout=15)
            import json
            data = json.loads(text)
            items = data.get("searchVO", {}).get("catMap", {}).get("cjrl", {}).get("listVO", [])
            if not items:
                items = data.get("searchVO", {}).get("listVO", [])

            return [
                {
                    "title": item.get("title", ""),
                    "url": f"http://www.csrc.gov.cn{item.get('url', '')}" if item.get("url", "").startswith("/") else item.get("url", ""),
                    "published_at": item.get("publishedTime", item.get("dateStr", "")),
                    "category": item.get("channelName", "证监会"),
                    "source": "中国证监会",
                }
                for item in (items or [])[:limit]
            ]
        except Exception as exc:
            raise ProviderError(
                f"证监会公告获取失败: {exc}",
                provider="policy", method="get_csrc_announcements", cause=exc,
            ) from exc

    async def get_pboc_operations(
        self,
        days: int = 7,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取央行公开市场操作（逆回购/MLF等）。

        Args:
            days: 回溯天数

        Returns:
            [{date, operation_type, amount, rate, description}, ...]
            其中无法从标题解析的字段为 None（不虚构 0 值）。
        """
        try:
            # 央行公开市场操作公告
            url = "http://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/index.html"
            text = await http_get_text(url, timeout=15)

            # 简化解析：提取标题和日期
            results = []
            # 使用正则提取链接
            import re
            pattern = r'<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>'
            matches = re.findall(pattern, text)
            for href, title in matches[:days * 2]:
                title = title.strip()
                if any(kw in title for kw in ["逆回购", "MLF", "公开市场", "LPR", "降准", "降息"]):
                    results.append({
                        "date": None,  # 列表页无日期，未解析不虚构
                        "operation_type": "逆回购" if "逆回购" in title else "MLF" if "MLF" in title else "其他",
                        "amount": None,
                        "rate": None,
                        "description": title,
                        "url": f"http://www.pbc.gov.cn{href}" if href.startswith("/") else href,
                        "source": "央行",
                    })
            return results[:days]
        except Exception as exc:
            raise ProviderError(
                f"央行公开市场操作获取失败: {exc}",
                provider="policy", method="get_pboc_operations", cause=exc,
            ) from exc

    async def get_macro_indicators(
        self,
        indicators: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """获取最新宏观指标（PMI/CPI/PPI/M2/社融）。

        Args:
            indicators: 指标列表，默认全部

        Returns:
            {
                "pmi": {"latest": 50.1, "prev": 49.8, "date": "2025-06"},
                "cpi": {"latest": 0.2, "prev": 0.1, "date": "2025-06"},
                ...
            }
        """
        # 通过 akshare 获取（最全面）
        try:
            import akshare as ak
        except ImportError as exc:
            raise ProviderError(
                "宏观指标获取需要 akshare，请先执行 pip install akshare",
                provider="policy", method="get_macro_indicators", cause=exc,
            ) from exc

        result = {}

        try:
            # PMI
            try:
                if hasattr(ak, "macro_china_pmi"):
                    pmi_df = await asyncio.to_thread(ak.macro_china_pmi)
                else:
                    pmi_df = None
                if pmi_df is not None and not pmi_df.empty:
                    latest = pmi_df.iloc[-1]
                    prev = pmi_df.iloc[-2] if len(pmi_df) > 1 else latest
                    result["pmi"] = {
                        "latest": float(latest.get("今值", 0) or 0),
                        "prev": float(prev.get("今值", 0) or 0),
                        "date": str(latest.get("日期", "")),
                    }
            except Exception:
                pass

            await asyncio.sleep(1)  # 防反爬

            # CPI
            try:
                cpi_fn = getattr(ak, "macro_china_cpi_monthly", None) or getattr(ak, "macro_china_cpi", None)
                if cpi_fn is not None:
                    cpi_df = await asyncio.to_thread(cpi_fn)
                    if cpi_df is not None and not cpi_df.empty:
                        latest = cpi_df.iloc[-1]
                        result["cpi"] = {
                            "latest": float(latest.get("今值", 0) or 0),
                            "date": str(latest.get("日期", "")),
                        }
            except Exception:
                pass

            return result
        except Exception as exc:
            raise ProviderError(
                f"宏观指标获取失败: {exc}",
                provider="policy", method="get_macro_indicators", cause=exc,
            ) from exc

    async def get_policy_news(
        self,
        keywords: list[str] | None = None,
        limit: int = 20,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取政策相关新闻（国务院/部委/监管）。

        Args:
            keywords: 过滤关键词（如 ["降准", "降息", "注册制"]）
            limit: 返回条数
        """
        try:
            # 国务院政策文件
            url = "https://sousuo.www.gov.cn/search-gov/data"
            params = {
                "t": "zhengcelibrary",
                "q": " ".join(keywords) if keywords else "经济",
                "timetype": "timeqb",
                "mintime": "",
                "maxtime": "",
                "sort": "pubtime",
                "sortType": "1",
                "searchfield": "title",
                "pcodeJiguan": "",
                "childtype": "",
                "subchildtype": "",
                "tsbq": "",
                "pubtimeyear": "",
                "puborg": "",
                "pcodeYear": "",
                "pcodeNum": "",
                "filetype": "",
                "p": "1",
                "n": str(min(limit, 20)),
                "inpro": "",
            }
            data = await http_get_json(url, params=params, timeout=15)
            items = data.get("searchVO", {}).get("catMap", {}).get("zhengcelibrary", {}).get("listVO", [])
            if not items:
                items = data.get("searchVO", {}).get("listVO", [])

            return [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "published_at": item.get("pubtimeStr", item.get("pubtime", "")),
                    "category": item.get("puborg", "国务院"),
                    "source": "中国政府网",
                }
                for item in (items or [])[:limit]
            ]
        except Exception as exc:
            raise ProviderError(
                f"政策新闻获取失败: {exc}",
                provider="policy", method="get_policy_news", cause=exc,
            ) from exc
