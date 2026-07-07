"""data_pipeline.providers — a-stock-data 工具的本地化封装（薄壳 re-export）。

模块拆分（每个端点一个文件 + 统一 facade）：

- _shared.py         共用 helper（tdx_client, em_get/em_post, tencent_quote, ticker normalize）
- quote.py           行情（mootdx K线/盘口 + 腾讯 PE/PB）
- sector.py          板块（东财 slist 板块归属 + 行业涨跌排名）
- capital.py         资金（东财 push2 资金流 + 龙虎榜席位聚合）
- fundamentals.py    基础数据（mootdx 季报 + 东财 push2 个股基本面）
- news.py            新闻（东财个股新闻）
- shareholder.py     股东户数（RPT_F10_EH_PER）
- block_trade.py     大宗交易（RPT_BLOCKTRADE）
- margin.py          融资融券（RPT_MARGIN_DETAIL）
- cls.py             财联社电报（https://www.cls.cn/nodeapi/updateTelegraphList）
- financials.py      财报三表（RPT_DMSK_FN_BALANCE/CASHFLOW/INCOME）
- cninfo.py          巨潮公告（cninfo hisAnnouncement/query）
- zt_unlock.py       涨停揭秘（RPT_ZTGG）
- auction.py         打板情绪（ak.stock_zt_pool_* 三池聚合）
- scorer.py          战术/阶段/流动性打分（纯计算，无 I/O）
- assembler.py       字段合成（无 I/O）
- facade.py          统一入口 stock_live_context（编排 N 端点 + 合成）
- router.py          兼容旧 import 路径
"""

from . import (
    block_trade,
    capital,
    cls,
    cninfo,
    financials,
    fundamentals,
    margin,
    news,
    quote,
    sector,
    shareholder,
    zt_unlock,
)
from .assembler import assemble, pick_sector_stage
from .facade import stock_live_context
from .scorer import (
    build_market_regime_hint,
    detect_auction_signal,
    detect_lhb_famous_buyers,
    detect_tactical_flags,
    infer_sector_stage,
    score_crowding,
    score_liquidity,
    score_valuation_percentile_from_pe,
)

__all__ = [
    # 字段合成
    "assemble",
    # 子模块
    "block_trade",
    # 打分
    "build_market_regime_hint",
    "capital",
    "cls",
    "cninfo",
    "detect_auction_signal",
    "detect_lhb_famous_buyers",
    "detect_tactical_flags",
    "financials",
    "fundamentals",
    "infer_sector_stage",
    "margin",
    "news",
    "pick_sector_stage",
    "quote",
    "score_crowding",
    "score_liquidity",
    "score_valuation_percentile_from_pe",
    "sector",
    "shareholder",
    # 统一入口
    "stock_live_context",
    "zt_unlock",
]
