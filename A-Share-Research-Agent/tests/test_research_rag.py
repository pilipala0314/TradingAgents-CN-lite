"""research_rag 检索逻辑单测（注入内存索引，不依赖网络）。"""
import asyncio
import time

import pytest

from datasource.providers.research_rag import _score_chunk, _tokenize, ResearchRAGProvider


@pytest.mark.unit
class TestTokenize:
    def test_basic(self):
        toks = _tokenize("贵州茅台 白酒 估值")
        assert "贵州" not in toks or "贵" in toks  # 中文按字
        assert "白酒" not in toks  # 中文按字拆开
        assert "5g" in _tokenize("5G 龙头")

    def test_stopwords_filtered(self):
        assert "的" not in _tokenize("我的股票")

    def test_empty(self):
        assert _tokenize("") == []


@pytest.mark.unit
class TestScoreChunk:
    def test_zero_on_no_match(self):
        assert _score_chunk(["ai"], _tokenize("白酒 估值"), "白酒估值") == 0.0

    def test_positive_on_match(self):
        q = _tokenize("白酒")
        chunk_tokens = _tokenize("白酒龙头 估值")
        s = _score_chunk(q, chunk_tokens, "白酒龙头 估值")
        assert s > 0


def _inject(provider: ResearchRAGProvider, symbol: str, chunks: list[dict]) -> None:
    """直接注入内存索引，绕过网络。"""
    provider._cache[symbol] = (time.time(), chunks)


@pytest.mark.unit
class TestSearchStripsInternalTokens:
    """回归：search 返回的 chunk 不得泄漏内部字段 _tokens。"""

    def test_search_output_clean(self):
        p = ResearchRAGProvider()
        _inject(p, "600519", [
            {"id": "1", "title": "贵州茅台白酒龙头", "content": "贵州茅台白酒龙头",
             "org": "中信", "date": "2026-01-01", "rating": "买入", "industry": "白酒",
             "score": 1.0, "_tokens": _tokenize("贵州茅台白酒龙头 贵州茅台白酒龙头")},
            {"id": "2", "title": "银行股研究", "content": "银行股研究",
             "org": "国泰", "date": "2026-01-02", "rating": "中性", "industry": "银行",
             "score": 0.5, "_tokens": _tokenize("银行股研究 银行股研究")},
        ])
        out = asyncio.run(p.search("白酒", top_k=5, symbol="600519"))
        assert out, "应命中白酒相关 chunk"
        assert all("_tokens" not in ch for ch in out), "search 输出不得含 _tokens"
        assert out[0]["title"] == "贵州茅台白酒龙头"

    def test_get_chunks_strips_tokens(self):
        p = ResearchRAGProvider()
        _inject(p, "600519", [
            {"id": "1", "title": "t", "content": "t", "org": "", "date": "", "rating": "",
             "industry": "", "score": 1.0, "_tokens": _tokenize("t t")},
        ])
        out = asyncio.run(p.get_chunks("600519"))
        assert all("_tokens" not in ch for ch in out), "get_chunks 输出不得含 _tokens"

    def test_search_uses_cached_tokens(self):
        """_tokens 缺失时应能回退现算（向后兼容旧缓存条目）。"""
        p = ResearchRAGProvider()
        _inject(p, "600519", [
            {"id": "1", "title": "半导体设备国产替代", "content": "半导体设备国产替代",
             "org": "", "date": "", "rating": "", "industry": "", "score": 1.0},
        ])
        out = asyncio.run(p.search("半导体", top_k=5, symbol="600519"))
        assert out and out[0]["id"] == "1"
