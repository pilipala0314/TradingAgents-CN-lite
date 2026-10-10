"""Geopolitics Provider — 地缘政治/国际关系数据。

数据源：
- 华尔街见闻地缘频道：实时地缘事件
- 新浪财经国际频道：国际关系/制裁/关税
- 关键词过滤引擎：从新闻流中提取地缘事件

覆盖：
- 国际制裁（美国/欧盟/联合国）
- 关税/贸易战（中美/中欧）
- 地缘冲突（军事/领土）
- 外交事件（峰会/协议/断交）
- 能源政治（OPEC/石油禁运）
- 科技封锁（芯片禁令/实体清单）

适用场景：
- 风险预警（地缘升级 → 避险资产/军工/能源）
- 板块映射（制裁 → 国产替代/自主可控）
- 宏观研判（国际关系 → 外资流向/汇率）
"""
from __future__ import annotations

import logging
import re
from typing import Any

from datasource.errors import ProviderError
from datasource.providers._http import http_get_json
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

# 地缘政治关键词库
GEOPOLITICS_KEYWORDS = {
    "制裁": ["制裁", "sanction", "实体清单", "entity list", "SDN", "黑名单"],
    "关税": ["关税", "tariff", "贸易战", "trade war", "加征", "反倾销"],
    "冲突": ["冲突", "军事", "战争", "conflict", "military", "导弹", "核武"],
    "外交": ["峰会", "外交", "断交", "协议", "summit", "diplomatic", "联合声明"],
    "能源": ["OPEC", "石油禁运", "能源制裁", "天然气", "oil embargo", "减产"],
    "科技封锁": ["芯片禁令", "出口管制", "技术封锁", "chip ban", "export control", "光刻机"],
    "台海": ["台海", "台湾", "海峡", "Taiwan", "统一", "军演"],
    "中美": ["中美", "拜登", "特朗普", "白宫", "Biden", "Trump", "对华"],
}


@register_provider("geopolitics")
class GeopoliticsProvider:
    """地缘政治/国际关系数据 Provider。"""

    @property
    def name(self) -> str:
        return "geopolitics"

    async def health_check(self) -> bool:
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "geopolitics" in provs)

    async def get_geopolitics_news(
        self,
        keywords: list[str] | None = None,
        limit: int = 20,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取地缘政治相关新闻（多源聚合 + 关键词过滤）。

        Args:
            keywords: 自定义关键词（默认使用内置地缘词库）
            limit: 返回条数

        Returns:
            [{title, content, published_at, url, source, category, risk_level}, ...]
        """
        # 从华尔街见闻 + 新浪国际频道拉取，然后过滤
        raw_news = await self._fetch_raw_news(limit=50)
        if not raw_news:
            return []

        # 关键词匹配 + 分类
        filter_keywords = keywords or self._all_keywords()
        results = []
        for item in raw_news:
            text = f"{item.get('title', '')} {item.get('content', '')}"
            # 显式传了 keywords 时严格按自定义词过滤，类别分支不得引入与关键词无关的新闻
            if any(kw in text for kw in filter_keywords):
                matched_category = self._match_category(text)
                item["category"] = matched_category or "其他"
                item["risk_level"] = self._assess_risk(text, matched_category)
                results.append(item)

        # 按风险等级排序
        risk_order = {"高": 0, "中": 1, "低": 2}
        results.sort(key=lambda x: risk_order.get(x.get("risk_level", "低"), 3))
        return results[:limit]

    async def get_sanctions_news(
        self,
        limit: int = 15,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取制裁相关新闻（美国/欧盟/联合国）。"""
        return await self.get_geopolitics_news(
            keywords=GEOPOLITICS_KEYWORDS["制裁"] + GEOPOLITICS_KEYWORDS["科技封锁"],
            limit=limit,
        )

    async def get_trade_war_news(
        self,
        limit: int = 15,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取贸易战/关税相关新闻。"""
        return await self.get_geopolitics_news(
            keywords=GEOPOLITICS_KEYWORDS["关税"] + GEOPOLITICS_KEYWORDS["中美"],
            limit=limit,
        )

    async def get_geopolitics_risk_score(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """计算当前地缘政治风险指数。

        基于最近新闻的关键词密度 + 情绪强度估算。

        Returns:
            {
                "risk_score": 65,           # 0-100，越高越危险（无数据时为 None）
                "risk_level": "中",         # 低/中/高（无数据时为"未知"）
                "hot_categories": ["制裁", "台海"],
                "recent_events": [...],
                "trend": "升温",            # 升温/平稳/降温（无数据时为"未知"）
            }
        """
        news = await self.get_geopolitics_news(limit=30)

        # 无新闻数据时不虚构风险值，明确标注未知
        if not news:
            return {
                "risk_score": None,
                "risk_level": "未知",
                "hot_categories": [],
                "recent_events": [],
                "trend": "未知",
                "sample_size": 0,
            }

        # 统计各类别出现频次
        category_counts: dict[str, int] = {}
        high_risk_count = 0
        for item in news:
            cat = item.get("category", "其他")
            category_counts[cat] = category_counts.get(cat, 0) + 1
            if item.get("risk_level") == "高":
                high_risk_count += 1

        # 风险得分：基于高风险新闻占比 + 总热度
        total = len(news) if news else 1
        risk_score = min(100, int(
            (high_risk_count / total) * 60 +  # 高风险占比贡献 60 分
            min(total, 20) * 2                 # 热度贡献最多 40 分
        ))

        if risk_score >= 70:
            risk_level = "高"
        elif risk_score >= 40:
            risk_level = "中"
        else:
            risk_level = "低"

        # 热门类别
        hot_categories = sorted(category_counts.keys(), key=lambda k: category_counts[k], reverse=True)[:3]

        return {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "hot_categories": hot_categories,
            "recent_events": news[:5],
            "trend": "升温" if risk_score >= 60 else "平稳" if risk_score >= 30 else "降温",
            "sample_size": len(news),
        }

    # ── 内部方法 ──────────────────────────────────────────────────

    async def _fetch_raw_news(self, limit: int = 50) -> list[dict[str, Any]]:
        """从多源拉取原始新闻流。

        两个数据源都失败时抛 `ProviderError`；单源失败仍返回另一源的部分数据。
        """
        results: list[dict[str, Any]] = []
        source_failures = 0

        # 华尔街见闻全球频道
        try:
            url = "https://api-one.wallstcn.com/apiv1/content/lives"
            params = {"channel": "global-channel", "limit": str(limit), "first_page": "true"}
            data = await http_get_json(url, params=params, timeout=10)
            items = data.get("data", {}).get("items", [])
            for item in items:
                results.append({
                    "title": item.get("title", "") or item.get("content_text", "")[:80],
                    "content": item.get("content_text", "") or "",
                    "published_at": item.get("display_time", 0),
                    "url": item.get("uri", ""),
                    "source": "华尔街见闻",
                })
        except Exception as exc:
            source_failures += 1
            logger.debug("地缘新闻源(华尔街见闻)失败: %s", exc)

        # 新浪财经国际频道 RSS
        try:
            url = "https://feed.mix.sina.com.cn/api/roll/get"
            params = {"pageid": "153", "lid": "2516", "k": "", "num": str(limit), "page": "1"}
            data = await http_get_json(url, params=params, timeout=10)
            items = data.get("result", {}).get("data", [])
            for item in items:
                results.append({
                    "title": item.get("title", ""),
                    "content": item.get("intro", "") or item.get("summary", ""),
                    "published_at": item.get("ctime", ""),
                    "url": item.get("url", ""),
                    "source": "新浪国际",
                })
        except Exception as exc:
            source_failures += 1
            logger.debug("地缘新闻源(新浪国际)失败: %s", exc)

        # 双源全部失败 → 视为 provider 故障抛错（单源失败仍返回部分数据）
        if source_failures == 2:
            raise ProviderError(
                "地缘新闻双数据源均失败",
                provider="geopolitics", method="_fetch_raw_news",
            )

        return results

    def _match_category(self, text: str) -> str | None:
        """匹配地缘政治类别。"""
        for category, keywords in GEOPOLITICS_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return category
        return None

    def _assess_risk(self, text: str, category: str | None) -> str:
        """评估单条新闻的风险等级。"""
        high_risk_words = ["战争", "军事冲突", "核武", "全面制裁", "断交", "封锁", "入侵", "紧急"]
        medium_risk_words = ["制裁", "关税", "禁令", "军演", "对峙", "升级", "报复"]

        if any(w in text for w in high_risk_words):
            return "高"
        if any(w in text for w in medium_risk_words):
            return "中"
        if category in ("冲突", "台海", "制裁"):
            return "中"
        return "低"

    @staticmethod
    def _all_keywords() -> list[str]:
        """合并所有类别的关键词。"""
        all_kw = []
        for keywords in GEOPOLITICS_KEYWORDS.values():
            all_kw.extend(keywords)
        return list(set(all_kw))
