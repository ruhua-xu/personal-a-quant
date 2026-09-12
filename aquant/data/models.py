"""Validated, in-memory historical market data contracts.

Daily bars use long format with exactly HISTORY_COLUMNS, Python ``date``
values (not timestamps), and finite real numeric OHLCV columns. The research
data frame may use floats; this does not change the domain's Decimal models.
No market calendar, provider fields, storage, or network access is implied.
"""

from datetime import date
from math import inf
from typing import Annotated, Self

import pandas as pd
from pandas.api.types import is_bool_dtype, is_complex_dtype, is_numeric_dtype
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from aquant.domain import Exchange, InstrumentId, Market

from .enums import AdjustmentMode, BarFrequency


HISTORY_COLUMNS = (
    "instrument_key", "trade_date", "open", "high", "low", "close", "volume",
)
PRICE_COLUMNS = ("open", "high", "low", "close")
NUMERIC_COLUMNS = (*PRICE_COLUMNS, "volume")
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def empty_history_frame() -> pd.DataFrame:
    """Return a fresh empty frame with the standard column order and dtypes."""
    return pd.DataFrame({
        column: pd.Series(dtype="float64" if column in NUMERIC_COLUMNS else "object")
        for column in HISTORY_COLUMNS
    })


class HistoryRequest(BaseModel):
    """Describe an inclusive date interval; never fetches data itself.

    Defaults are daily, unadjusted bars. Instruments are kept in a tuple so
    the request cannot change through mutation of the caller's input list.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instruments: tuple[InstrumentId, ...] = Field(min_length=1)
    start_date: date
    end_date: date
    frequency: BarFrequency = BarFrequency.DAILY
    adjustment: AdjustmentMode = AdjustmentMode.RAW

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


class MarketDataSet(BaseModel):
    """A validated snapshot of bars with explicit provenance metadata.

    Construction copies and stably sorts the input by instrument/date, resets
    its index, and normalizes column order. Extra columns are rejected. An
    empty frame is valid if it has the standard columns. Metadata is frozen;
    pandas data remains mutable, so consumers must revalidate edited frames
    by constructing a new MarketDataSet. No DataFrame JSON format is defined.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    data: pd.DataFrame
    frequency: BarFrequency
    adjustment: AdjustmentMode
    provider: NonEmptyText
    generated_at: AwareDatetime

    @field_validator("data")
    @classmethod
    def validate_data(cls, data: pd.DataFrame) -> pd.DataFrame:
        if data.columns.has_duplicates:
            raise ValueError("duplicate column names are not allowed")
        if set(data.columns) != set(HISTORY_COLUMNS):
            raise ValueError(f"data must contain exactly these columns: {HISTORY_COLUMNS}")
        if data.empty:
            return empty_history_frame()

        frame = data.loc[:, list(HISTORY_COLUMNS)].copy(deep=True)
        if frame.isna().any().any():
            raise ValueError("history data must not contain missing values")

        for key in frame["instrument_key"]:
            if not isinstance(key, str):
                raise ValueError("instrument_key must be an InstrumentId.canonical_key")
            parts = key.split(":", 2)
            if len(parts) != 3 or not parts[2] or parts[2] != parts[2].strip():
                raise ValueError("instrument_key must be an InstrumentId.canonical_key")
            # Validate internal identifiers, without inferring market rules or
            # assuming any particular symbol format.
            Market(parts[0])
            Exchange(parts[1])

        if not frame["trade_date"].map(lambda value: type(value) is date).all():
            raise ValueError("trade_date must contain Python date values, not timestamps")
        if frame.duplicated(["instrument_key", "trade_date"]).any():
            raise ValueError("duplicate instrument_key + trade_date bars are not allowed")

        for column in NUMERIC_COLUMNS:
            dtype = frame[column].dtype
            if not is_numeric_dtype(dtype) or is_bool_dtype(dtype) or is_complex_dtype(dtype):
                raise ValueError(f"{column} must be a real numeric column")
            if frame[column].isin([inf, -inf]).any():
                raise ValueError(f"{column} must contain only finite values")

        if (frame["high"] < frame["low"]).any():
            raise ValueError("high must be greater than or equal to low")
        for column in ("open", "close"):
            if ((frame[column] < frame["low"]) | (frame[column] > frame["high"])).any():
                raise ValueError(f"{column} must lie between low and high")
        if (frame["volume"] < 0).any():
            raise ValueError("volume must be non-negative")

        return frame.sort_values(
            ["instrument_key", "trade_date"], kind="stable",
        ).reset_index(drop=True)
