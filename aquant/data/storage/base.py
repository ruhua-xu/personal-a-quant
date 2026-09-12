"""Synchronous boundary for local historical bars; no acquisition behavior."""

from abc import ABC, abstractmethod

from aquant.data import HistoryRequest, MarketDataSet


class HistoricalDataStore(ABC):
    """Persist bars and return validated snapshots for inclusive date ranges."""

    @abstractmethod
    def write(self, dataset: MarketDataSet) -> None:
        """Upsert by instrument/date; newer imports replace existing bars."""

    @abstractmethod
    def read(self, request: HistoryRequest) -> MarketDataSet:
        """Return local bars, or a standard empty dataset when none match."""
