"""Historical market data provider interfaces."""

from .base import MarketDataProvider
from .fake import FakeMarketDataProvider

__all__ = ["FakeMarketDataProvider", "MarketDataProvider"]
