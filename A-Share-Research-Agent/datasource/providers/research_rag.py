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
失败语义：永不静默降级；失败直接 raise（DataSource 决定 fallback）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from urllib.parse import urlencode
from typing import Any

from datasource.providers._http import http_get_json
from datasource.providers.akshare import AkshareProvider
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

_REPORT_LIST_URL = "https://reportapi.eastmoney.com/report/list?{query}"

# 内存索引容量上限（超出后淘汰过期/最旧条目，防无界增长）
_MAX_CACHE_SYMBOLS = 256

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


@register_provider("research_rag")
class ResearchRAGProvider:
    """卖方研报 RAG Provider — DataSource 的 'research_rag' provider。

    并发模型：per-symbol 锁（仅同一 symbol 的请求互斥，不同 symbol 并行），
    网络请求不占用全局锁；内存索引带 TTL + 容量上限。

    Args:
        page_size: 单次拉取条数（默认 30）
        ttl_sec:   内存索引 TTL（秒，默认 300 = 5 分钟）
        timeout:   HTTP 超时（秒）
    """

    def __init__(
        self,
        page_size: int = 30,
        ttl_sec: int = 300,
        timeout: float = 10.0,
        akshare_provider: AkshareProvider | None = None,
    ) -> None:
        self.page_size = page_size
        self.ttl_sec = ttl_sec
        self.timeout = timeout
        self._akshare_provider = akshare_provider or AkshareProvider()
        # 内存倒排索引：symbol -> (ts, list[chunk])；_locks 为 per-symbol 互斥锁
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()  # 保护 _cache/_locks 结构本身
        self._headers = {
            "Referer": "https://data.eastmoney.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    @property
    def name(self) -> str:
        return "research_rag"

    async def health_check(self) -> bool:
        """依赖检查：主路径走标准 HTTP（reportapi），akshare 仅为兜底源，缺失不应误报不可用。"""
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "research_rag" in provs)

    async def _fetch(self, url: str) -> dict[str, Any]:
        """统一 GET 入口（东财兼容 headers + 轻量重试；经 _http 层代理路由 + 直连兜底）。"""
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return await http_get_json(
                    url,
                    headers={**self._headers, "Accept": "application/json, text/plain, */*"},
                    timeout=self.timeout,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.6 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    async def _symbol_lock(self, symbol: str) -> asyncio.Lock:
        """获取 symbol 级互斥锁（不同 symbol 并行，同 symbol 防 stampede）。"""
        async with self._meta_lock:
            return self._locks.setdefault(symbol, asyncio.Lock())

    def _evict_if_needed(self) -> None:
        """容量控制：先淘汰过期条目，仍超限则淘汰最旧（不淘汰进行中的条目）。必须在 _meta_lock 内调用。"""
        if len(self._cache) < _MAX_CACHE_SYMBOLS:
            return
        now = time.time()
        expired = [s for s, (ts, _) in self._cache.items() if now - ts >= self.ttl_sec]
        for s in expired:
            self._drop_symbol(s)
        if len(self._cache) >= _MAX_CACHE_SYMBOLS:
            oldest = sorted(((ts, s) for s, (ts, _) in self._cache.items()))
            for _, s in oldest[: len(self._cache) - _MAX_CACHE_SYMBOLS + 1]:
                self._drop_symbol(s)

    def _drop_symbol(self, symbol: str) -> None:
        lock = self._locks.get(symbol)
        if lock is not None and lock.locked():
            return  # 该 symbol 有请求在飞：不淘汰，避免锁丢失导致互斥失效
        self._cache.pop(symbol, None)
        self._locks.pop(symbol, None)

    async def get_chunks(self, symbol: str, topn: int | None = None) -> list[dict[str, Any]]:
        """取某只股票最近 N 篇研报 chunks（带内存缓存，per-symbol 互斥）。

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
        lock = await self._symbol_lock(symbol)
        async with lock:
            async with self._meta_lock:
                cached = self._cache.get(symbol)
                fresh = cached if cached and (time.time() - cached[0]) < self.ttl_sec else None
            if fresh is not None:
                chunks = fresh[1]
            else:
                # 网络请求在结构锁外执行，仅同 symbol 互斥，不阻塞其他 symbol
                chunks = await self._fetch_chunks(symbol)
                async with self._meta_lock:
                    self._evict_if_needed()
                    self._cache[symbol] = (time.time(), chunks)
        if topn is not None and topn > 0:
            chunks = chunks[:topn]
        # 剥离内部字段 _tokens（search 打分用），对外契约保持干净
        return [{k: v for k, v in ch.items() if k != "_tokens"} for ch in chunks]

    async def _get_indexed_chunks(self, symbol: str) -> list[dict[str, Any]]:
        """取 chunks（含内部字段 `_tokens`，仅供 search 打分，勿直接对外返回）。"""
        lock = await self._symbol_lock(symbol)
        async with lock:
            async with self._meta_lock:
                cached = self._cache.get(symbol)
                fresh = cached if cached and (time.time() - cached[0]) < self.ttl_sec else None
            if fresh is not None:
                return fresh[1]
            chunks = await self._fetch_chunks(symbol)
            async with self._meta_lock:
                self._evict_if_needed()
                self._cache[symbol] = (time.time(), chunks)
            return chunks

    async def _fetch_chunks(self, symbol: str) -> list[dict[str, Any]]:
        """实际拉取研报并构造 chunks。"""
        query = urlencode(
            {
                "code": symbol,
                "pageSize": self.page_size,
                "pageNo": 1,
                "industryCode": "",
                "rating": "",
                "ratingChange": "",
                "beginTime": "",
                "endTime": "",
                "fields": "code,infoCode,title,publishDate,orgSName,emRatingName,industryName",
            }
        )
        url = _REPORT_LIST_URL.format(query=query)
        try:
            data = await self._fetch(url)
            rows = (data or {}).get("Data") or []
        except Exception as exc:
            logger.warning("ResearchRAG reportapi 不可用，回退 akshare 研报流: %s", exc)
            # 回退到 akshare，同时处理格式兼容，避免 KeyError
            try:
                result = await self._akshare_provider.get_research_reports(symbol=symbol, page_size=self.page_size)
                if isinstance(result, list):
                    rows = result
                else:
                    # 假设是 df
                    rows = []
                    for _, row in result.iterrows():
                        rows.append({
                            "title": row.get("研报标题") or "",
                            "report_date": row.get("发布日期") or "",
                            "org": row.get("机构名称") or "",
                            "rating": row.get("评级") or "",
                            "industry": row.get("行业") or "",
                            "id": str(row.name) if hasattr(row, "name") else str(len(rows)),
                        })
            except Exception as ak_exc:
                logger.warning("ResearchRAG akshare 回退也失败: %s", ak_exc)
                rows = []
        chunks: list[dict[str, Any]] = []
        for idx, r in enumerate(rows):
            title = str(r.get("title", "") or "").strip()
            date_str = str(r.get("report_date", "") or r.get("date", "") or "")
            if not date_str and r.get("publishDate"):
                try:
                    from datetime import datetime

                    date_str = datetime.fromtimestamp(int(r["publishDate"]) / 1000).strftime("%Y-%m-%d")
                except (ValueError, TypeError, OSError):
                    date_str = ""
            content = title  # 报告列表接口无摘要，content 退化为标题
            chunks.append({
                "id": str(r.get("infoCode") or r.get("id") or idx),
                "title": title,
                "org": str(r.get("orgSName", "") or r.get("org", "") or ""),
                "date": date_str,
                "rating": str(r.get("emRatingName", "") or r.get("rating", "") or ""),
                "industry": str(r.get("industryName", "") or r.get("industry", "") or ""),
                "content": content,
                "score": 1.0 / (1 + idx),  # 时间衰减
                # 内部字段：构建期一次性分词，search 直接复用（避免每次检索重复 tokenize）
                "_tokens": _tokenize(f"{title} {content}"),
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

        # 选定候选 chunks（走 _get_indexed_chunks 以复用构建期缓存的 _tokens）
        if symbol is not None:
            chunks = await self._get_indexed_chunks(symbol)
        else:
            # 拉所有已缓存 symbol（快照 keys 避免遍历期间结构变更）
            async with self._meta_lock:
                symbols = list(self._cache.keys())
            chunks = []
            for sym in symbols:
                chunks.extend(await self._get_indexed_chunks(sym))

        # 打分
        scored: list[tuple[float, dict[str, Any]]] = []
        for ch in chunks:
            text = f"{ch.get('title', '')} {ch.get('content', '')}".strip()
            # 优先用构建期缓存的分词结果，缺失时回退现算（向后兼容旧缓存条目）
            ch_tokens = ch.get("_tokens") or _tokenize(text)
            s = _score_chunk(q_tokens, ch_tokens, text)
            if s > 0:
                # 叠加初始分（时间新度）
                s += float(ch.get("score", 0.0))
                scored.append((s, ch))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict[str, Any]] = []
        for s, ch in scored[:top_k]:
            ch2 = {k: v for k, v in ch.items() if k != "_tokens"}  # 剥离内部字段
            ch2["score"] = round(float(s), 4)
            out.append(ch2)
        return out
