"""巨潮资讯网公告 — `http://www.cninfo.com.cn/new/hisAnnouncement/query`（POST）。

返回结构: list[{announcement_id, title, published_at, url, type, type_code}, ...]
- 真实接口: 巨潮资讯网历史公告查询，stock 字段需 `code,orgid` 二选一
- orgid 需先调 `szse_stock` 接口拉证券基本信息；为减少耦合，本函数在 orgid 缺失时
  优雅调用一次 `szse_stock` 拿映射（缓存由调用方负责）
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from datasource.errors import ProviderError
from datasource.providers._shared import em_get, em_post, normalize_ticker
from datasource.providers._shared.utils import cninfo_market_params
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STOCK_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"  # 巨潮全量证券基础信息 JSON
CNINFO_HEADERS = {
    "Origin": "https://www.cninfo.com.cn",
    "Referer": "https://www.cninfo.com.cn/new/disclosure/stock",
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
}

# 巨潮按市场分库查询：column/plate 必须与股票所在市场一致，否则查不到。
# 市场映射见 _shared/utils.cninfo_market_params（cninfo_news 共用）。

# 简化的 orgid 缓存（按 code 缓存 orgid），避免每次都查 szse_stock。
_ORGID_CACHE: dict[str, str] = {}


def _resolve_orgid(code: str) -> str:
    """通过 cninfo 的 `szse_stock` 接口找 code → orgid 映射。"""
    code = normalize_ticker(code)
    if code in _ORGID_CACHE:
        return _ORGID_CACHE[code]
    resp = em_get(
        CNINFO_STOCK_URL,
        params={"keyWord": code, "maxNum": 10},
        headers=CNINFO_HEADERS,
        timeout=15,
    )
    if resp.status_code != 200:
        raise ProviderError(
            f"cninfo szse_stock HTTP {resp.status_code}",
            provider="cninfo", method="_resolve_orgid",
        )
    payload = resp.json()
    rows = payload.get("stockList") or []
    for row in rows:
        if str(row.get("code", "")).zfill(6) == code:
            orgid = str(row.get("orgId") or "")
            if not orgid:
                continue
            _ORGID_CACHE[code] = orgid
            return orgid
    raise ProviderError(
        f"cninfo 未找到 code={code} 的 orgid",
        provider="cninfo", method="_resolve_orgid",
    )


def fetch_cninfo_announcements(
    code: str,
    recent_days: int = 7,
    category: str = "",
) -> list[dict[str, Any]]:
    """巨潮公告（最近 N 日）。

    Args:
        code:        6 位股票代码
        recent_days: 回看窗口
        category:    公告分类（""=全部 / "category_ndbg_szsh"=年报 / "category_bndbg_szsh"=半年报）
    """
    code = normalize_ticker(code)
    if not code:
        raise ProviderError(
            "code 必填",
            provider="cninfo", method="fetch_cninfo_announcements",
        )
    orgid = _resolve_orgid(code)
    column, plate = cninfo_market_params(code)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=max(1, recent_days))).strftime("%Y-%m-%d")
    form_data = {
        "stock": f"{code},{orgid}",
        "tabName": "fulltext",
        "pageSize": "30",
        "pageNum": "1",
        "column": column,
        "category": category,
        "plate": plate,
        "seDate": f"{start_date}~{end_date}",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    resp = em_post(CNINFO_QUERY_URL, data=form_data, headers=CNINFO_HEADERS, timeout=20)
    if resp.status_code != 200:
        raise ProviderError(
            f"cninfo hisAnnouncement HTTP {resp.status_code}",
            provider="cninfo", method="fetch_cninfo_announcements",
        )
    payload = resp.json()
    rows = payload.get("announcements") or []
    if not rows:
        logger.info(
            "cninfo 公告为空, code=%s, range=%s..%s",
            code,
            start_date,
            end_date,
        )
        return []
    results: list[dict[str, Any]] = []
    for row in rows:
        ann_id = row.get("announcementId")
        results.append(
            {
                "announcement_id": ann_id,
                "title": row.get("announcementTitle"),
                "published_at": row.get("adjunctUrl", "").split("/")[0] or None,
                "url": (
                    f"https://static.cninfo.com.cn/{row.get('adjunctUrl')}"
                    if row.get("adjunctUrl")
                    else None
                ),
                "type": row.get("announcementType"),
                "type_code": category,
                "orgid": orgid,
            }
        )
    logger.info("巨潮公告拉取完成, code=%s, count=%d", code, len(results))
    return results


@register_provider("cninfo")
class CninfoProvider:
    """巨潮资讯网公告 Provider — DataSource 的 'cninfo' provider。

    端点：
    - `get_cninfo_announcements(code, recent_days=7, category="")`
      → list[{announcement_id, title, published_at, url, type, type_code, orgid}, ...]
    """

    def __init__(self) -> None:
        # 调用方走 asyncio.to_thread 包装 sync 函数；这里直接暴露 async 接口。
        pass

    @property
    def name(self) -> str:
        return "cninfo"

    async def health_check(self) -> bool:
        """依赖检查：走标准 HTTP，无额外依赖。"""
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "cninfo" in provs)

    async def get_cninfo_announcements(
        self,
        code: str,
        recent_days: int = 7,
        category: str = "",
    ) -> list[dict[str, Any]]:
        """拉取指定股票最近 N 日的巨潮公告（同步函数 async 化包装）。

        Args:
            code:        6 位股票代码（必填）
            recent_days: 回看窗口（默认 7）
            category:    公告分类（""=全部 / "category_ndbg_szsh"=年报 / "category_bndbg_szsh"=半年报）
        """
        if not code:
            raise ProviderError(
                "code 必填",
                provider="cninfo", method="get_cninfo_announcements",
            )
        return await asyncio.to_thread(
            fetch_cninfo_announcements, code, recent_days, category
        )
