"""Provider-independent enumerations used by the domain core."""

from enum import StrEnum


class Market(StrEnum):
    CN = "CN"
    US = "US"


class Currency(StrEnum):
    CNY = "CNY"
    USD = "USD"


class AssetType(StrEnum):
    STOCK = "STOCK"
    ETF = "ETF"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class ManualOrderStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    EXECUTED = "EXECUTED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class RiskSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCK = "BLOCK"


class StrategyRunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Exchange(StrEnum):
    """Known exchange identifiers; values are explicit and extensible."""

    XSHG = "XSHG"
    XSHE = "XSHE"
    XNAS = "XNAS"
    XNYS = "XNYS"
