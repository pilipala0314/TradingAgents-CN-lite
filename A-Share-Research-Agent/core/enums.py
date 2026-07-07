from enum import Enum


class StringEnum(str, Enum):
    pass


class WindowType(StringEnum):
    INTRADAY = "intraday"
    CLOSE = "close"
    POSTCLOSE = "postclose"
    NIGHT_ANNOUNCEMENT = "night_announcement"
    OVERNIGHT = "overnight"
    PREOPEN = "preopen"


class EntityType(StringEnum):
    MARKET = "market"
    INDEX = "index"
    SECTOR = "sector"
    CONCEPT = "concept"
    SECURITY = "security"
    EVENT = "event"


class SecurityType(StringEnum):
    STOCK = "stock"
    ETF = "etf"
    INDEX = "index"
    FUND = "fund"


class SectorType(StringEnum):
    INDUSTRY = "industry"
    CONCEPT = "concept"
    THEME = "theme"
    DEFENSIVE_BUCKET = "defensive_bucket"


class MarketRegime(StringEnum):
    RISK_ON_EXPANSION = "risk_on_expansion"
    RISK_ON_ROTATION = "risk_on_rotation"
    NEUTRAL_MIXED = "neutral_mixed"
    RISK_OFF_STABILIZED = "risk_off_stabilized"
    RISK_OFF_PANIC = "risk_off_panic"


class SectorStage(StringEnum):
    WATCHING = "watching"
    EMERGING = "emerging"
    CONFIRMED = "confirmed"
    STRENGTHENING = "strengthening"
    DIVERGING = "diverging"
    DECAYING = "decaying"


class RuleLevel(StringEnum):
    BLOCK = "block"
    WARNING = "warning"
    CONSTRAIN = "constrain"


class JobType(StringEnum):
    COLLECTOR = "collector"
    PARSER = "parser"
    EMBEDDING = "embedding"
    FEATURE = "feature"
    STRATEGY = "strategy"
    AUDIT = "audit"


class JobStatus(StringEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class FlowType(StringEnum):
    MAIN_FORCE = "main_force"
    NORTHBOUND = "northbound"
    SECTOR_MAIN_FORCE = "sector_main_force"
    INDIVIDUAL_MAIN_FORCE = "individual_main_force"


class EventScope(StringEnum):
    MACRO = "macro"
    MARKET = "market"
    SECTOR = "sector"
    SECURITY = "security"


class EventType(StringEnum):
    POLICY = "policy"
    ANNOUNCEMENT = "announcement"
    RISK_DISCLOSURE = "risk_disclosure"
    PERFORMANCE = "performance"
    REDUCTION = "reduction"
    UNLOCK = "unlock"
    NEWS = "news"


class EventSeverity(StringEnum):
    """事件严重性分级 — 由 importance + risk 综合离散化得出。

    设计目的：在 agent / committee / report 决策层，给到"低/中/高/极高"的离散
    信号比 0~1 浮点分更直观（少一次心智转换，少一次阈值漂移）。

    阈值口径（与 EventNormalizer._infer_severity 保持完全一致）：
        LOW      : importance < 0.40  且  risk < 0.30
        MEDIUM   : 介于 LOW 与 HIGH 之间
        HIGH     : importance >= 0.55 或 risk >= 0.55
        CRITICAL : importance >= 0.75 且 risk >= 0.65   （政策/退市/巨亏等）
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
