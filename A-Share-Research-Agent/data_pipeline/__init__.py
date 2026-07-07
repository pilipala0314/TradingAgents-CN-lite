from importlib import import_module

__all__ = [
    "AShareDataRouter",
    "AkShareClient",
    "CapitalFlowSyncJob",
    "EventIngestJob",
    "FactETLPipeline",
    "MarketBarSyncJob",
    "MarketDataSyncJob",
    "SectorFeatureSyncJob",
    "SectorMasterSyncJob",
    "SecurityMasterSyncJob",
]


def __getattr__(name: str):
    if name == "AkShareClient":
        return getattr(import_module("data_pipeline.akshare_client"), name)
    if name == "AShareDataRouter":
        return getattr(import_module("data_pipeline.data_router"), name)
    if name == "MarketDataSyncJob":
        return getattr(import_module("data_pipeline.sync_jobs"), name)
    if name in {
        "SecurityMasterSyncJob",
        "SectorMasterSyncJob",
        "MarketBarSyncJob",
        "CapitalFlowSyncJob",
        "EventIngestJob",
        "SectorFeatureSyncJob",
        "FactETLPipeline",
    }:
        return getattr(import_module("data_pipeline.fact_jobs"), name)
    raise AttributeError(f"module 'data_pipeline' has no attribute {name!r}")
