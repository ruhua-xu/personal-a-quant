"""Manual order plans and user-recorded executions.

These models contain no broker communication or trade execution behavior.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
)

from .enums import Currency, ManualOrderStatus, OrderSide
from .instrument import InstrumentId


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ManualOrderPlan(BaseModel):
    """A reviewable plan for a human to execute outside this system."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: NonEmptyText
    account_id: NonEmptyText
    strategy_run_id: NonEmptyText
    instrument_id: InstrumentId
    side: OrderSide
    quantity: Decimal = Field(gt=Decimal("0"))
    reference_price: Decimal = Field(ge=Decimal("0"))
    currency: Currency
    status: ManualOrderStatus
    created_at: AwareDatetime
    reason: str

    @computed_field
    @property
    def estimated_amount(self) -> Decimal:
        return self.quantity * self.reference_price


class ManualExecution(BaseModel):
    """A record of a trade already completed manually by the user."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: NonEmptyText
    order_plan_id: NonEmptyText
    account_id: NonEmptyText
    instrument_id: InstrumentId
    side: OrderSide
    executed_quantity: Decimal = Field(gt=Decimal("0"))
    executed_price: Decimal = Field(ge=Decimal("0"))
    fees: Decimal = Field(ge=Decimal("0"))
    currency: Currency
    executed_at: AwareDatetime
    external_reference: str | None = None
