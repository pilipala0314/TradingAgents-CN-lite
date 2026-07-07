"""data_pipeline.etl — 拆 fact_jobs.py / sync_jobs.py 后的 ETL 子包。

子包内每个文件只负责一个同步阶段，编排器 `FactETLPipeline` 在
`pipeline.py` 中按顺序串接。各 Job 仅暴露 `async run(...)` 接口。
"""

from .capital_flow import CapitalFlowSyncJob
from .event_ingest import EventIngestJob
from .market_bar import MarketBarSyncJob
from .market_feature import MarketDataSyncJob
from .pipeline import FactETLPipeline
from .sector_feature import SectorFeatureSyncJob
from .sector_master import SectorMasterSyncJob
from .security_master import SecurityMasterSyncJob
from .stock_feature import StockFeatureSyncJob

__all__ = [
    "CapitalFlowSyncJob",
    "EventIngestJob",
    "FactETLPipeline",
    "MarketBarSyncJob",
    "MarketDataSyncJob",
    "SectorFeatureSyncJob",
    "SectorMasterSyncJob",
    "SecurityMasterSyncJob",
    "StockFeatureSyncJob",
]
