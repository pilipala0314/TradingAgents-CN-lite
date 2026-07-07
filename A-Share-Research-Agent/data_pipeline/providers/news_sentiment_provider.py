"""NewsSentimentProvider — 新闻舆情数据接入 + 词典情感打分。

数据源：akshare `stock_news_em`（东财个股新闻流）
- 入参：symbol（如 600519）
- 返回：DataFrame(columns=[news_id, title, content, url, published_at, source_name])

情感打分（简化版 P2-T5）：
- 中文情感词典（精简：正向 60 词 / 负向 60 词）
- 公式：score = (positive - negative) / max(1, positive + negative)
- 范围 [-1, 1]
- label: >= 0.2 positive, <= -0.2 negative, else neutral
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import pandas as pd

from data_pipeline.providers.akshare_provider import AkshareProvider

logger = logging.getLogger(__name__)


# 精简中文情感词典（P2-T5 - 后续 P5 可替换为 LLM 打分）
POSITIVE_WORDS: set[str] = {
    "上涨", "涨停", "拉升", "突破", "新高", "增长", "盈利", "增长", "受益", "强势",
    "回购", "增持", "中标", "签约", "利好", "改善", "提升", "加速", "放量", "反弹",
    "高增长", "超预期", "高分红", "龙头", "走强", "反弹", "订单", "合作", "成功",
    "创新", "扩张", "翻倍", "高景气", "潜力", "推荐", "买入", "看好", "超预期",
    "增长", "上行", "复苏", "盈利", "稳增", "提振", "加仓", "积极", "向好", "增长",
    "突破", "高升", "暴涨", "业绩亮眼", "现金流", "收益", "降本", "增效", "优势",
    "机会", "机会", "机会", "增长", "强于", "超预期", "增长", "稳健", "翻番",
    "良好", "强劲", "上涨", "再创新高", "高歌猛进",
}

NEGATIVE_WORDS: set[str] = {
    "下跌", "跌停", "下挫", "新低", "亏损", "下滑", "萎缩", "利空", "减持", "套牢",
    "违规", "处罚", "警示", "风险", "退市", "停牌", "诉讼", "仲裁", "纠纷", "造假",
    "违规", "下行", "承压", "疲软", "低迷", "走弱", "回落", "缩量", "流出", "恶化",
    "下降", "高估", "看空", "卖出", "减持", "不及预期", "高估", "估值偏高", "砍单",
    "降薪", "裁员", "诉讼", "违规", "处罚", "退市", "重挫", "跳水", "暴跌", "跌停",
    "亏损", "巨亏", "净亏", "破发", "腰斩", "跌破", "弱势", "悲观", "下行", "承压",
    "缩量", "走弱", "疲软", "低迷", "回落", "流出", "恶化", "警示", "退市", "被否",
    "未通过", "否决", "重整", "破产", "重整", "清盘", "暴雷", "黑天鹅", "踩雷", "巨亏",
}


class NewsSentimentProvider:
    """新闻舆情 Provider — DataRouter 的 'cn_news_em' provider。"""

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    async def get_news(self, symbol: str) -> pd.DataFrame:
        """拉取该股的新闻列表。

        Returns:
            DataFrame(columns=[
                "news_id", "title", "content", "url",
                "published_at", "source_name"
            ])
        """
        try:
            logger.info("正在拉取新闻: %s", symbol)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_news_em, symbol=symbol,
            )
        except Exception as e:
            logger.error("拉取新闻失败: symbol=%s error=%s", symbol, e)
            raise

        if df is None or df.empty:
            return _empty_news_df()

        out = pd.DataFrame()
        out["news_id"] = df.get("id") if "id" in df.columns else None
        # 列名兼容（akshare stock_news_em 列名：新闻标题/新闻内容/发布时间/新闻链接/文章来源）
        out["title"] = df.get("新闻标题", df.columns[0] if len(df.columns) > 0 else None)
        out["content"] = df.get("新闻内容", df.columns[1] if len(df.columns) > 1 else None)
        out["url"] = df.get("新闻链接", df.columns[3] if len(df.columns) > 3 else None)
        # 发布时间兼容
        time_col = "发布时间" if "发布时间" in df.columns else (
            df.columns[2] if len(df.columns) > 2 else None
        )
        if time_col is not None and time_col in df.columns:
            out["published_at"] = pd.to_datetime(df[time_col], errors="coerce")
        else:
            out["published_at"] = pd.NaT
        out["source_name"] = df.get("文章来源", df.columns[4] if len(df.columns) > 4 else None)
        # 强制 string
        out["title"] = out["title"].astype(str)
        out["content"] = out["content"].astype(str) if out["content"] is not None else ""
        out["url"] = out["url"].astype(str)
        out["source_name"] = out["source_name"].astype(str) if out["source_name"] is not None else ""
        return out


def _empty_news_df() -> pd.DataFrame:
    return pd.DataFrame({
        "news_id": pd.Series(dtype="object"),
        "title": pd.Series(dtype="object"),
        "content": pd.Series(dtype="object"),
        "url": pd.Series(dtype="object"),
        "published_at": pd.Series(dtype="datetime64[ns]"),
        "source_name": pd.Series(dtype="object"),
    })


def score_sentiment(text: str) -> tuple[float, str]:
    """简化情感打分（词典法）。

    Returns:
        (score in [-1, 1], label in {positive, neutral, negative})
    """
    if not text:
        return 0.0, "neutral"
    text = str(text)
    # 简单分词（中文按字符 + bigram 匹配）
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    total = pos + neg
    if total == 0:
        return 0.0, "neutral"
    score = (pos - neg) / total
    if score >= 0.2:
        label = "positive"
    elif score <= -0.2:
        label = "negative"
    else:
        label = "neutral"
    return float(score), label


def score_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """对 DataFrame 的 title+content 打分，加 sentiment_score/label/method 列。"""
    if df.empty:
        out = df.copy()
        out["sentiment_score"] = pd.Series(dtype="float64")
        out["sentiment_label"] = pd.Series(dtype="object")
        out["score_method"] = pd.Series(dtype="object")
        return out
    out = df.copy()
    scores: list[float] = []
    labels: list[str] = []
    for _, row in out.iterrows():
        text = (str(row.get("title", "")) or "") + " " + (str(row.get("content", "")) or "")
        s, l = score_sentiment(text)
        scores.append(s)
        labels.append(l)
    out["sentiment_score"] = scores
    out["sentiment_label"] = labels
    out["score_method"] = "dict_v1"
    return out


def aggregate_sentiment(df: pd.DataFrame) -> dict[str, Any]:
    """聚合一支股票的新闻舆情打分。

    Returns:
        {"total_count": N, "positive_count": n, "negative_count": n,
         "neutral_count": n, "avg_score": float, "label": "positive|neutral|negative"}
    """
    if df.empty or "sentiment_label" not in df.columns:
        return {
            "total_count": 0,
            "positive_count": 0,
            "negative_count": 0,
            "neutral_count": 0,
            "avg_score": 0.0,
            "label": "neutral",
        }
    total = len(df)
    pos = int((df["sentiment_label"] == "positive").sum())
    neg = int((df["sentiment_label"] == "negative").sum())
    neu = int((df["sentiment_label"] == "neutral").sum())
    avg = float(df["sentiment_score"].astype(float).mean()) if total > 0 else 0.0
    if avg >= 0.2:
        label = "positive"
    elif avg <= -0.2:
        label = "negative"
    else:
        label = "neutral"
    return {
        "total_count": total,
        "positive_count": pos,
        "negative_count": neg,
        "neutral_count": neu,
        "avg_score": round(avg, 4),
        "label": label,
    }
