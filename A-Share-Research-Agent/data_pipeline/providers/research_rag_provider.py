"""ResearchRAGProvider — 卖方研报 RAG Provider（轻量内存实现）。

数据源：东方财富 reportapi 端点（reportapi.eastmoney.com/report/list）。
简化策略：本实现不爬 PDF / 不调外部 embedding，而是从研报列表接口取「标题 + 评级 + 行业」，
在内存内做关键词 + 命中度排序，封装成 RAG 风格的 chunks 接口。

生产替换路径：
- PDF 解析可接 `pdfplumber` / `pymupdf`
- 向量化可接本地 `sentence-transformers` 或在线 embedding
- 索引可接 `pgvector` / `milvus`
- 本类只暴露稳定的接口契约，替换实现不影响上游。

端点：
- `get_chunks(symbol, topn=20) -> list[dict]`  → 某只股票最近 N 篇研报 chunks
- `search(query, top_k=5, symbol=None) -> list[dict]` → 在已索引 chunks 上做关键词检索

返回值统一为 `list[dict]`。
失败语义：永不静默降级；失败直接 raise（DataRouter 决定 fallback）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 东方财富研报列表 API（reportapi 端点 — 实测可用）
# 文档参考：https://data.eastmoney.com/report/100.html
# 真实响应：{"Success": true, "Message": "成功", "Data": [...]}
_REPORT_LIST_URL = (
    "https://reportapi.eastmoney.com/report/list"
    "?code={symbol}&pageSize={page_size}&pageNo={page_no}"
    "&fields=code,infoCode,title,publishDate,orgSName,emRatingName,industryName"
)

# 停用词（仅在内存搜索时用，避免误命中）
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "或", "等", "为", "对",
    "上", "下", "中", "这", "那", "也", "都", "就", "还", "并", "而",
    "以", "其", "所", "不", "本", "可", "到", "从", "由", "因", "如", "但", "又", "之", "于", "with", "the",
    "and", "for", "of", "to", "on", "an", "is", "are",
}

# 中文 / 英文 / 数字组合分词正则
# 优先匹配 alnum 整体（5G / 2B / A股代码 等），其次中文单字
_TOKEN_RE = re.compile(r"[A-Za-z]+\d+[A-Za-z]*|\d+[A-Za-z]+|[A-Za-z]+|\d+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    """轻量分词（中文按字 + 英文 / 数字按词）。"""
    if not text:
        return []
    tokens = []
    for t in _TOKEN_RE.findall(text):
        t_low = t.lower()
        # 停用词过滤
        if t in _STOPWORDS or t_low in _STOPWORDS:
            continue
        # 过滤单个字母（a/b/c）但保留常见缩写（AI/5G/GDP）
        if len(t) == 1 and t.isascii() and t_low not in {"a", "i"}:
            continue
        tokens.append(t_low)
    return tokens


def _score_chunk(query_tokens: list[str], chunk_tokens: list[str], chunk_text: str) -> float:
    """打分：词频占比 + 标题命中加权 + 短语匹配。"""
    if not query_tokens or not chunk_tokens:
        return 0.0
    counter = Counter(chunk_tokens)
    total = len(chunk_tokens)
    score = 0.0
    for q in query_tokens:
        cnt = counter.get(q, 0)
        if cnt == 0:
            continue
        # 词频占比
        score += cnt / total * 5.0
    # 整词命中加成（在原文中 query 子串出现）
    for q in query_tokens:
        if len(q) >= 2 and q in chunk_text.lower():
            score += 1.0
    return score


class ResearchRAGProvider:
    """卖方研报 RAG Provider — DataRouter 的 'research_rag' provider。

    Args:
        page_size: 单次拉取条数（默认 30）
        ttl_sec:   内存索引 TTL（秒，默认 300 = 5 分钟）
        timeout:   HTTP 超时（秒）
    """

    def __init__(self, page_size: int = 30, ttl_sec: int = 300, timeout: float = 10.0, transport: httpx.BaseTransport | None = None) -> None:
        self.page_size = page_size
        self.ttl_sec = ttl_sec
        self.timeout = timeout
        self._transport = transport
        # 内存倒排索引：symbol -> (ts, list[chunk])
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()
        self._headers = {
            "Referer": "https://data.eastmoney.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    async def _fetch(self, url: str) -> dict[str, Any]:
        """统一 GET 入口（业务方应通过 ResiliencePolicy 加重试 / 限流）。"""
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers, transport=self._transport) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    async def get_chunks(self, symbol: str, topn: int | None = None) -> list[dict[str, Any]]:
        """取某只股票最近 N 篇研报 chunks（带内存缓存）。

        Returns:
            list[dict]，每条含：
                - id:       研报 infoCode
                - title:    研报标题
                - org:      发布机构简称
                - date:     发布日期（YYYY-MM-DD）
                - rating:   评级（如"买入"）
                - industry: 行业
                - content:  摘要 / 标题（本实现无摘要，退化为标题）
                - score:    初始分（按时间衰减）
        """
        async with self._lock:
            now = time.time()
            cached = self._cache.get(symbol)
            if cached and (now - cached[0]) < self.ttl_sec:
                chunks = cached[1]
            else:
                chunks = await self._fetch_chunks(symbol)
                self._cache[symbol] = (now, chunks)
        if topn is not None and topn > 0:
            return chunks[:topn]
        return chunks

    async def _fetch_chunks(self, symbol: str) -> list[dict[str, Any]]:
        """实际拉取研报并构造 chunks。

        API 字段（reportapi 端点，实测）：
        - title          标题
        - publishDate    发布时间（毫秒时间戳）
        - orgSName       机构简称
        - infoCode       研报 infoCode（唯一 ID）
        - emRatingName   评级（如"买入"）
        - industryName   行业
        - code           股票代码
        """
        url = _REPORT_LIST_URL.format(
            symbol=symbol,
            page_size=self.page_size,
            page_no=1,
        )
        data = await self._fetch(url)
        # reportapi 端点真实响应：{"Success": true, "Data": [...]}
        rows = (data or {}).get("Data") or []
        chunks: list[dict[str, Any]] = []
        for idx, r in enumerate(rows):
            title = str(r.get("title", "") or "").strip()
            # publishDate 是毫秒时间戳（13 位），转成 YYYY-MM-DD
            ts_ms = r.get("publishDate")
            date_str = ""
            if ts_ms:
                try:
                    from datetime import datetime
                    date_str = datetime.fromtimestamp(int(ts_ms) / 1000).strftime("%Y-%m-%d")
                except (ValueError, TypeError, OSError):
                    date_str = ""
            chunks.append({
                "id": str(r.get("infoCode", idx)),
                "title": title,
                "org": str(r.get("orgSName", "") or ""),
                "date": date_str,
                "rating": str(r.get("emRatingName", "") or ""),
                "industry": str(r.get("industryName", "") or ""),
                "content": title,  # 报告列表接口无摘要，content 退化为标题
                "score": 1.0 / (1 + idx),  # 时间衰减
            })
        if not chunks:
            # 不抛错：返回空 list（facade 层自己决定 strict 行为）
            logger.debug("ResearchRAG: 股票 %s 无研报数据", symbol)
        return chunks

    async def search(
        self,
        query: str,
        top_k: int = 5,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """在已索引 chunks 上做关键词检索（TF 风格）。

        Args:
            query:  查询串
            top_k:  返回 top K
            symbol: 可选 — 只在该 symbol 的 chunks 中检索；None 时在所有已索引 symbol 中检索

        Returns:
            list[dict]（按 score 降序）
        """
        if not query.strip():
            return []
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        # 选定候选 chunks
        if symbol is not None:
            chunks = await self.get_chunks(symbol)
        else:
            # 拉所有已缓存 symbol
            chunks = []
            for sym in list(self._cache.keys()):
                chunks.extend(await self.get_chunks(sym))

        # 打分
        scored: list[tuple[float, dict[str, Any]]] = []
        for ch in chunks:
            text = f"{ch.get('title', '')} {ch.get('content', '')}".strip()
            ch_tokens = _tokenize(text)
            s = _score_chunk(q_tokens, ch_tokens, text)
            if s > 0:
                # 叠加初始分（时间新度）
                s += float(ch.get("score", 0.0))
                scored.append((s, ch))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict[str, Any]] = []
        for s, ch in scored[:top_k]:
            ch2 = dict(ch)
            ch2["score"] = round(float(s), 4)
            out.append(ch2)
        return out
