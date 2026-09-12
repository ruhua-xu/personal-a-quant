"""Provider-independent boundary for historical market data."""

from abc import ABC, abstractmethod

from aquant.data.models import HistoryRequest, MarketDataSet


class MarketDataProviderError(RuntimeError):
    """Acquisition failed; callers must not interpret this as no history."""


class ProviderSchemaError(MarketDataProviderError):
    """The provider response cannot satisfy the standard data contract."""


class MarketDataProvider(ABC):
    """Return standard bars; unsupported frequency/adjustment raises ValueError.

    Date endpoints are inclusive. No matching bars returns an empty standard
    dataset. Providers must not silently substitute a different frequency or
    adjustment. Implementations define acquisition, not strategy behavior.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable name identifying this provider implementation."""

    @abstractmethod
    def get_history(self, request: HistoryRequest) -> MarketDataSet:
        """Return a dataset matching the requested instruments and metadata."""
