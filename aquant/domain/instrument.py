"""Canonical, provider-independent instrument identity."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from .enums import AssetType, Currency, Exchange, Market


InstrumentSymbol = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InstrumentId(BaseModel):
    """An instrument identity assembled only from explicit domain values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    market: Market
    exchange: Exchange
    symbol: InstrumentSymbol
    asset_type: AssetType
    currency: Currency

    @property
    def canonical_key(self) -> str:
        return f"{self.market.value}:{self.exchange.value}:{self.symbol}"
