"""Sentiment Provider — 市场舆情/情绪数据。

数据源：
- 东方财富股吧：帖子热度、情绪倾向
- 雪球：关注人数、讨论热度
- 百度指数：搜索趋势（反映散户关注度）
- 东财人气榜：市场热度排名

适用场景：
- 判断个股/板块情绪温度
- 辅助择时（极端情绪 → 反转信号）
- 事件驱动研究（舆情爆发 → 关注催化）
"""
from __future__ import annotations

import logging
from typing import Any

from datasource.errors import ProviderError
from datasource.providers._http import http_get_json, http_post_json
from datasource.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider("sentiment")
class SentimentProvider:
    """市场舆情/情绪数据 Provider。"""

    @property
    def name(self) -> str:
        return "sentiment"

    async def health_check(self) -> bool:
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "sentiment" in provs)

    async def get_stock_sentiment(
        self,
        symbol: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """获取个股舆情情绪指标。

        聚合：东财股吧帖子数/阅读量 + 关注人数变化

        Args:
            symbol: 6 位股票代码

        Returns:
            {
                "symbol": "600519",
                "guba_post_count": 1234,      # 近期帖子数
                "guba_read_count": 56789,     # 阅读量
                "attention_change": 0.05,     # 关注度变化
                "sentiment_score": 0.65,      # 情绪得分 (0-1, >0.5偏多)
                "hot_rank": 12,               # 人气排名
            }
        """
        code = symbol.strip().split(".")[0]
        result: dict[str, Any] = {
            "symbol": code,
            "guba_post_count": 0,
            "guba_read_count": 0,
            "attention_change": 0.0,
            "sentiment_score": None,  # 无实质舆情数据时不虚构分数
            "hot_rank": None,
            "source": "eastmoney_guba",
        }

        # 东财股吧热度
        try:
            url = f"https://guba.eastmoney.com/interface/GetData.aspx"
            params = {
                "path": "newapi/GetTopicListByCode",
                "code": code,
                "ps": "10",
                "p": "1",
                "type": "0",
            }
            data = await http_get_json(url, params=params, timeout=8)
            if isinstance(data, dict):
                re_list = data.get("re", {}).get("list", [])
                total_read = sum(item.get("post_click_count", 0) for item in re_list)
                result["guba_post_count"] = len(re_list)
                result["guba_read_count"] = total_read
        except Exception as exc:
            logger.debug("东财股吧数据获取失败(%s): %s", code, exc)

        # 东财人气榜排名
        try:
            hot_data = await self._get_popularity_rank(code)
            if hot_data:
                result["hot_rank"] = hot_data.get("rank")
                result["attention_change"] = hot_data.get("change", 0.0)
        except Exception:
            pass

        # 启发式情绪得分（仅在有真实阅读量数据时计算）：
        # 阅读量越高 → 关注度越高 → 情绪越极端。这是启发式近似，
        # 不替代真实的 NLP 情绪分析，仅供研究参考。
        if result["guba_read_count"] > 0:
            score = min(1.0, result["guba_read_count"] / 100000)
            result["sentiment_score"] = round(0.5 + score * 0.3, 3)

        return result

    async def get_hot_stocks(
        self,
        limit: int = 20,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取市场人气榜（东财人气排名）。

        Returns:
            [{rank, code, name, hot_score, change_rank}, ...]
        """
        try:
            url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
            payload = {
                "appId": "appId01",
                "globalId": "786e4c21-70dc-435a-93bb-38",
                "marketType": "",
                "pageNo": 1,
                "pageSize": min(limit, 50),
            }
            data = await http_post_json(url, json_body=payload, timeout=10)

            items = data.get("data", [])
            return [
                {
                    "rank": idx + 1,
                    "code": item.get("sc", "").replace("0.", "").replace("1.", ""),
                    "name": item.get("name", ""),
                    "hot_score": item.get("hotScore", 0),
                    "change_rank": item.get("hisRank", 0) - (idx + 1) if item.get("hisRank") else 0,
                }
                for idx, item in enumerate(items)
            ]
        except Exception as exc:
            raise ProviderError(
                f"人气榜获取失败: {exc}",
                provider="sentiment", method="get_hot_stocks", cause=exc,
            ) from exc

    async def get_sector_sentiment(
        self,
        sector_name: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """获取板块舆情温度。

        Args:
            sector_name: 板块名称（如"半导体"），空=全市场

        Returns:
            {
                "sector": "全市场",
                "sentiment_direction": "偏多",  # 偏多/偏空/中性（无数据时为"未知"）
                "sentiment_score": 0.3,         # 情绪得分 [-1,1]，正=偏多（无数据时为 None）
                "sectors": [{"name": "半导体", "pct_change": 2.1}, ...],
                "key_topics": ["AI芯片", "国产替代"],
                "source": "inferred",
            }

        说明：暂不支持按指定板块过滤，`sector_name` 有值时返回结构中显式标注
        `requested_sector` 与 `note="暂不支持指定板块，返回全市场行业情绪"`；
        无数据/接口失败时不虚构"中性"等情绪值。
        """
        result: dict[str, Any] = {
            "sector": sector_name or "全市场",
            "sentiment_direction": "未知",
            "sentiment_score": None,
            "sectors": [],
            "key_topics": [],
            "source": "inferred",
        }
        if sector_name:
            result["requested_sector"] = sector_name
            result["note"] = "暂不支持指定板块，返回全市场行业情绪"

        # 通过东财板块数据推断（仅在有真实数据时填充情绪值，不虚构）
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": "1", "pz": "5", "po": "1", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f3",
                "fs": "m:90+t:2",
                "fields": "f3,f12,f14,f104,f105",
                "ut": "b2884a393a59ad64002292a3e90d46a5",
            }
            data = await http_get_json(url, params=params, timeout=8)
            items = (data.get("data") or {}).get("diff", [])
            if items:
                sectors = [
                    {"name": item.get("f14", ""), "pct_change": item.get("f3", 0)}
                    for item in items
                ]
                avg_pct = sum(item.get("f3", 0) for item in items) / len(items)
                if avg_pct > 1:
                    result["sentiment_direction"] = "偏多"
                elif avg_pct < -1:
                    result["sentiment_direction"] = "偏空"
                else:
                    result["sentiment_direction"] = "中性"
                # 情绪得分：涨幅归一化到 [-1, 1]（±5% 视为满偏），有真实数据才计算
                result["sentiment_score"] = round(
                    max(-1.0, min(1.0, avg_pct / 5)), 3
                )
                result["sectors"] = sectors
                result["key_topics"] = [item.get("f14", "") for item in items[:3]]
        except Exception:
            pass

        return result

    # ── 内部 ──────────────────────────────────────────────────────

    async def _get_popularity_rank(self, code: str) -> dict | None:
        """查询个股在东财人气榜的排名。"""
        try:
            url = "https://emappdata.eastmoney.com/stockrank/getCurrentLatest"
            market = "1" if code.startswith(("6", "9")) else "0"
            payload = {
                "appId": "appId01",
                "globalId": "786e4c21-70dc-435a-93bb-38",
                "code": f"{market}.{code}",
            }
            data = await http_post_json(url, json_body=payload, timeout=8)
            item = data.get("data") or {}
            if item:
                return {
                    "rank": item.get("rank"),
                    "change": item.get("hisRank", 0) - item.get("rank", 0) if item.get("hisRank") else 0,
                }
        except Exception:
            pass
        return None
