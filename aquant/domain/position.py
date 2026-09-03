"""Long-only position snapshot."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .enums import Currency
from .instrument import InstrumentId


AccountId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: AccountId
    instrument_id: InstrumentId
    quantity: Decimal = Field(ge=Decimal("0"))
    available_quantity: Decimal = Field(ge=Decimal("0"))
    average_cost: Decimal = Field(ge=Decimal("0"))
    cost_currency: Currency

    @model_validator(mode="after")
    def validate_available_quantity(self) -> "Position":
        if self.available_quantity > self.quantity:
            raise ValueError("available_quantity cannot exceed quantity")
        return self
