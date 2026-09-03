"""Trading account metadata and currency-specific cash balances."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .enums import Currency


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: NonEmptyText
    display_name: NonEmptyText
    base_currency: Currency
    broker: str | None = None

    @field_validator("broker")
    @classmethod
    def normalize_broker(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class CashBalance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: NonEmptyText
    currency: Currency
    settled_cash: Decimal = Field(ge=Decimal("0"))
    available_cash: Decimal = Field(ge=Decimal("0"))
    frozen_cash: Decimal = Field(ge=Decimal("0"))
