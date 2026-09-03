"""Research scores, target positions, and reproducible strategy runs."""

from decimal import Decimal
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from .enums import StrategyRunStatus
from .instrument import InstrumentId


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SecurityScore(BaseModel):
    """A research output; it is not a buy or sell instruction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_key: NonEmptyText
    strategy_version: NonEmptyText
    instrument_id: InstrumentId
    score: Decimal
    as_of: AwareDatetime


class TargetPosition(BaseModel):
    """The highest-level trading intent a strategy may produce."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: NonEmptyText
    strategy_run_id: NonEmptyText
    instrument_id: InstrumentId
    target_weight: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))


class StrategyRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyText
    strategy_key: NonEmptyText
    strategy_version: NonEmptyText
    status: StrategyRunStatus
    as_of: AwareDatetime
    created_at: AwareDatetime
    data_version: NonEmptyText
    parameters: dict[str, Any]
