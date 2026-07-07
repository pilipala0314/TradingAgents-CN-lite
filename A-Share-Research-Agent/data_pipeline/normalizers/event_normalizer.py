from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd

from core.enums import EventScope, EventSeverity, EventType
from core.identifiers import MARKET_ENTITY_ID, entity_uuid

from .common import first_existing, to_datetime_value

POSITIVE_KEYWORDS = ("中标", "回购", "增持", "签约", "预增", "扭亏", "突破")
NEGATIVE_KEYWORDS = ("减持", "问询", "处罚", "诉讼", "违规", "爆雷", "下修", "亏损", "退市")
HIGH_IMPORTANCE_KEYWORDS = ("停牌", "复牌", "重组", "业绩预告", "并购", "减持", "解禁", "问询")


class EventNormalizer:
    """将本地 CSV/JSON/JSONL 结构化事件标准化为 `fact_event`。"""

    @staticmethod
    def load_local_file(file_path: str | Path) -> pd.DataFrame:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"事件文件不存在: {path}")

        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path)
        if suffix == ".json":
            return pd.read_json(path)
        if suffix == ".jsonl":
            return pd.read_json(path, lines=True)
        raise ValueError(f"暂不支持的事件文件格式: {suffix}")

    @staticmethod
    def normalize(
        raw_df: pd.DataFrame,
        source: str,
        security_map: dict[str, UUID] | None = None,
        sector_map: dict[str, UUID] | None = None,
        version: int = 1,
    ) -> pd.DataFrame:
        if raw_df.empty:
            return pd.DataFrame()

        security_map = security_map or {}
        sector_map = sector_map or {}
        records: list[dict[str, Any]] = []

        for raw_record in raw_df.to_dict(orient="records"):
            title = str(first_existing(raw_record, "title", "标题", "headline", default="")).strip()
            if not title:
                continue

            symbol = str(first_existing(raw_record, "symbol", "代码", "entity_symbol", default="")).strip()
            sector_name = str(
                first_existing(raw_record, "sector_name", "板块名称", "concept_name", default="")
            ).strip()
            sector_code = str(
                first_existing(raw_record, "sector_code", "板块代码", "concept_code", default="")
            ).strip()
            published_at = to_datetime_value(
                first_existing(raw_record, "published_at", "发布时间", "日期", default=None)
            )
            if published_at is None:
                published_at = datetime.utcnow()

            event_scope = EventNormalizer._infer_event_scope(raw_record, symbol, sector_name, sector_code)
            event_type = EventNormalizer._infer_event_type(title, raw_record)
            entity_id = EventNormalizer._resolve_entity_id(
                event_scope=event_scope,
                symbol=symbol,
                sector_name=sector_name,
                sector_code=sector_code,
                raw_record=raw_record,
                security_map=security_map,
                sector_map=sector_map,
            )
            sentiment_score = EventNormalizer._score_sentiment(title)
            risk_score = EventNormalizer._score_risk(title)
            importance_score = EventNormalizer._score_importance(title)
            severity = EventNormalizer._infer_severity(importance_score, risk_score)

            records.append(
                {
                    "event_type": event_type,
                    "event_scope": event_scope,
                    "entity_id": entity_id,
                    "title": title,
                    "summary": str(
                        first_existing(raw_record, "summary", "摘要", "content", default="")
                    ).strip()
                    or None,
                    "published_at": published_at,
                    "captured_at": datetime.utcnow(),
                    "importance_score": importance_score,
                    "sentiment_score": sentiment_score,
                    "risk_score": risk_score,
                    "severity": severity,
                    "source": source,
                    "source_url": first_existing(raw_record, "url", "source_url", "链接"),
                    "version": version,
                }
            )

        return pd.DataFrame(records).drop_duplicates(
            subset=["event_scope", "entity_id", "published_at", "source", "title"],
            keep="last",
        )

    @staticmethod
    def _infer_event_scope(record: dict[str, Any], symbol: str, sector_name: str, sector_code: str) -> str:
        explicit_scope = str(first_existing(record, "scope", "event_scope", default="")).strip()
        if explicit_scope in {scope.value for scope in EventScope}:
            return explicit_scope
        if symbol:
            return EventScope.SECURITY.value
        if sector_name or sector_code:
            return EventScope.SECTOR.value
        title = str(first_existing(record, "title", "标题", "headline", default=""))
        if any(keyword in title for keyword in ("政策", "国务院", "证监会", "发改委", "央行")):
            return EventScope.MACRO.value
        return EventScope.MARKET.value

    @staticmethod
    def _resolve_entity_id(
        *,
        event_scope: str,
        symbol: str,
        sector_name: str,
        sector_code: str,
        raw_record: dict[str, Any],
        security_map: dict[str, UUID],
        sector_map: dict[str, UUID],
    ) -> UUID | None:
        if symbol:
            return security_map.get(symbol)
        if event_scope == EventScope.SECTOR.value:
            if sector_code and sector_code in sector_map:
                return sector_map[sector_code]
            if sector_name and sector_name in sector_map:
                return sector_map[sector_name]
        explicit_entity_key = str(
            first_existing(raw_record, "entity_key", "scope_key", default="")
        ).strip()
        if explicit_entity_key:
            return entity_uuid(event_scope, explicit_entity_key)
        if event_scope == EventScope.MACRO.value:
            return entity_uuid(event_scope, "cn_macro")
        if event_scope == EventScope.MARKET.value:
            return MARKET_ENTITY_ID
        return None

    @staticmethod
    def _infer_event_type(title: str, record: dict[str, Any]) -> str:
        explicit_type = str(first_existing(record, "event_type", "type", default="")).strip()
        if explicit_type in {event_type.value for event_type in EventType}:
            return explicit_type
        if any(keyword in title for keyword in ("政策", "国务院", "证监会", "发改委")):
            return EventType.POLICY.value
        if any(keyword in title for keyword in ("减持", "解禁")):
            return EventType.REDUCTION.value if "减持" in title else EventType.UNLOCK.value
        if any(keyword in title for keyword in ("问询", "风险提示", "处罚", "诉讼")):
            return EventType.RISK_DISCLOSURE.value
        if any(keyword in title for keyword in ("预告", "快报", "年报", "季报", "中报")):
            return EventType.PERFORMANCE.value
        return EventType.ANNOUNCEMENT.value

    @staticmethod
    def _score_sentiment(title: str) -> float:
        positive_hits = sum(keyword in title for keyword in POSITIVE_KEYWORDS)
        negative_hits = sum(keyword in title for keyword in NEGATIVE_KEYWORDS)
        if positive_hits == negative_hits == 0:
            return 0.5
        score = 0.5 + 0.15 * positive_hits - 0.2 * negative_hits
        return max(0.0, min(1.0, score))

    @staticmethod
    def _score_risk(title: str) -> float:
        negative_hits = sum(keyword in title for keyword in NEGATIVE_KEYWORDS)
        base = 0.2 if negative_hits == 0 else 0.45 + 0.15 * negative_hits
        return max(0.0, min(1.0, base))

    @staticmethod
    def _score_importance(title: str) -> float:
        hits = sum(keyword in title for keyword in HIGH_IMPORTANCE_KEYWORDS)
        score = 0.35 + 0.15 * hits
        return max(0.0, min(1.0, score))

    # ------------------------------------------------------------------
    # 严重性分级 (severity)
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_severity(importance_score: float, risk_score: float) -> str:
        """将 importance + risk 离散化为 LOW / MEDIUM / HIGH / CRITICAL。

        阈值口径与 `core.enums.EventSeverity` 文档严格一致，禁止单边漂移。
        """
        importance = float(importance_score or 0.0)
        risk = float(risk_score or 0.0)
        if importance >= 0.75 and risk >= 0.65:
            return EventSeverity.CRITICAL.value
        if importance >= 0.55 or risk >= 0.55:
            return EventSeverity.HIGH.value
        if importance < 0.40 and risk < 0.30:
            return EventSeverity.LOW.value
        return EventSeverity.MEDIUM.value

    # ------------------------------------------------------------------
    # 列表入口 — EventIngestJob / AkShareClient.get_global_news_aggregated 调用
    # ------------------------------------------------------------------

    @staticmethod
    def load_external_event_file(file_path: str | Path) -> list[dict[str, Any]]:
        """从外部 csv/json/jsonl 文件读入原始事件字典列表。

        forwarder，薄壳 — 真正的 IO 在 `load_local_file`。
        """
        df = EventNormalizer.load_local_file(file_path)
        if df is None or df.empty:
            return []
        return df.to_dict(orient="records")

    @staticmethod
    def raw_to_dataframe(
        raw_events: list[dict[str, Any]],
        business_date: date | None = None,
        source: str = "akshare_global_news",
    ) -> pd.DataFrame:
        """把 `list[dict]` 原始事件打成 `fact_event_raw` 写库用的 DataFrame。

        business_date: 写入 `captured_at` / `published_at` 兜底，确保入库时
        有非空时间戳（下游 `upsert_dataframe` 要求 NOT NULL）。
        """
        if not raw_events:
            return pd.DataFrame(
                columns=[
                    "event_id",
                    "business_date",
                    "title",
                    "summary",
                    "source",
                    "source_url",
                    "published_at",
                    "captured_at",
                    "raw_payload",
                ]
            )
        rows: list[dict[str, Any]] = []
        anchor = datetime.combine(business_date, datetime.min.time()) if business_date else datetime.utcnow()
        for idx, raw in enumerate(raw_events):
            title = str(first_existing(raw, "title", "标题", "headline", default="")).strip() or f"event_{idx}"
            published_at = to_datetime_value(
                first_existing(raw, "published_at", "发布时间", "日期", default=None)
            ) or anchor
            rows.append(
                {
                    "event_id": str(
                        entity_uuid(
                            "event_raw",
                            f"{title}|{published_at.isoformat()}|{source}",
                        )
                    ),
                    "business_date": business_date or anchor.date(),
                    "title": title,
                    "summary": str(
                        first_existing(raw, "summary", "摘要", "content", default="")
                    ).strip()
                    or None,
                    "source": source,
                    "source_url": first_existing(raw, "url", "source_url", "链接"),
                    "published_at": published_at,
                    "captured_at": datetime.utcnow(),
                    "raw_payload": raw,
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def normalize_events(
        raw_events: list[dict[str, Any]],
        business_date: date | None = None,
        symbol_map: dict[str, Any] | None = None,
        source: str = "akshare_global_news",
    ) -> list[dict[str, Any]]:
        """list[dict] 入口的标准化 — 返回 list[dict] 供 ETL 层 `pd.DataFrame(...)` 写入 `fact_event`。

        与 `normalize()` 的区别：接收 list 而非 DataFrame，且自动把
        `symbol_map`（含 `{symbol:{security_id,name}}` 嵌套结构）拍平为
        `normalize()` 期望的 `dict[symbol, security_id]`。
        """
        if not raw_events:
            return []

        # 1) 拍平 symbol_map：同时支持 {symbol: security_id} 和 {symbol: {security_id, name}}
        flat_security_map: dict[str, UUID] = {}
        flat_name_to_security: dict[str, UUID] = {}
        for key, value in (symbol_map or {}).items():
            if isinstance(value, dict):
                sec_id = value.get("security_id")
                if sec_id is not None:
                    flat_security_map[str(key)] = sec_id
                name = value.get("name")
                if name and sec_id is not None:
                    flat_name_to_security[str(name)] = sec_id
            else:
                flat_security_map[str(key)] = value

        # 2) 走 normalize() 走 DataFrame 路径
        raw_df = pd.DataFrame(raw_events)
        # 给 normalize() 提供 source / business_date 默认值
        if "source" not in raw_df.columns:
            raw_df["source"] = source
        if "published_at" not in raw_df.columns and business_date is not None:
            raw_df["published_at"] = datetime.combine(business_date, datetime.min.time())
        normalized_df = EventNormalizer.normalize(
            raw_df=raw_df,
            source=source,
            security_map=flat_security_map,
            version=1,
        )
        if normalized_df is None or normalized_df.empty:
            return []
        return normalized_df.to_dict(orient="records")
