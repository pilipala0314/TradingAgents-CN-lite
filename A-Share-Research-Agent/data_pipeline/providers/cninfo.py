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

from data_pipeline.providers._shared import em_get, em_post, normalize_ticker

logger = logging.getLogger(__name__)

CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STOCK_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"  # 巨潮全量证券基础信息 JSON
CNINFO_HEADERS = {
    "Origin": "http://www.cninfo.com.cn",
    "Referer": "http://www.cninfo.com.cn/new/disclosure/stock",
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
}

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
        raise RuntimeError(f"cninfo szse_stock HTTP {resp.status_code}")
    payload = resp.json()
    rows = payload.get("stockList") or []
    for row in rows:
        if str(row.get("code", "")).zfill(6) == code:
            orgid = str(row.get("orgId") or "")
            if not orgid:
                continue
            _ORGID_CACHE[code] = orgid
            return orgid
    raise RuntimeError(f"cninfo 未找到 code={code} 的 orgid")


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
        raise ValueError("code 必填")
    orgid = _resolve_orgid(code)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=max(1, recent_days))).strftime("%Y-%m-%d")
    form_data = {
        "stock": f"{code},{orgid}",
        "tabName": "fulltext",
        "pageSize": "30",
        "pageNum": "1",
        "column": "szse",
        "category": category,
        "plate": "sz;szmb;szzx;szcn",
        "seDate": f"{start_date}~{end_date}",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    resp = em_post(CNINFO_QUERY_URL, data=form_data, headers=CNINFO_HEADERS, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"cninfo hisAnnouncement HTTP {resp.status_code}")
    payload = resp.json()
    rows = payload.get("announcements") or []
    if not rows:
        raise RuntimeError(
            f"cninfo hisAnnouncement 未返回公告, code={code}, range={start_date}..{end_date}"
        )
    results: list[dict[str, Any]] = []
    for row in rows:
        ann_id = row.get("announcementId")
        results.append(
            {
                "announcement_id": ann_id,
                "title": row.get("announcementTitle"),
                "published_at": row.get("adjunctUrl", "").split("/")[0] or None,
                "url": (
                    f"http://static.cninfo.com.cn/{row.get('adjunctUrl')}"
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


class CninfoProvider:
    """巨潮资讯网公告 Provider — DataRouter 的 'cninfo' provider。

    端点：
    - `get_cninfo_announcements(code, recent_days=7, category="")`
      → list[{announcement_id, title, published_at, url, type, type_code, orgid}, ...]
    """

    def __init__(self) -> None:
        # 调用方走 asyncio.to_thread 包装 sync 函数；这里直接暴露 async 接口。
        pass

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
            raise ValueError("code 必填")
        return await asyncio.to_thread(
            fetch_cninfo_announcements, code, recent_days, category
        )
