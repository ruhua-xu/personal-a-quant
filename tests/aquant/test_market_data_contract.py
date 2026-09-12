import ast
from datetime import date, datetime, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.api.types import is_numeric_dtype
import pytest
from pydantic import ValidationError

from aquant.data import (
    AdjustmentMode,
    BarFrequency,
    HISTORY_COLUMNS,
    HistoryRequest,
    MarketDataSet,
    empty_history_frame,
)


def dataset(data, **overrides):
    values = dict(
        data=data, frequency=BarFrequency.DAILY, adjustment=AdjustmentMode.RAW,
        provider="fixture", generated_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    values.update(overrides)
    return MarketDataSet(**values)


def test_history_request_defaults_and_same_day_interval(sample_instruments):
    request = HistoryRequest(
        instruments=list(sample_instruments),
        start_date=date(2026, 9, 2), end_date=date(2026, 9, 2),
    )
    assert request.instruments == sample_instruments
    assert request.frequency is BarFrequency.DAILY
    assert request.adjustment is AdjustmentMode.RAW


def test_history_request_rejects_no_instruments():
    with pytest.raises(ValidationError, match="instruments"):
        HistoryRequest(instruments=[], start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))


def test_history_request_rejects_reversed_dates(sample_instruments):
    with pytest.raises(ValidationError, match="start_date"):
        HistoryRequest(
            instruments=sample_instruments,
            start_date=date(2026, 9, 3), end_date=date(2026, 9, 1),
        )


@pytest.mark.parametrize("adjustment", list(AdjustmentMode))
def test_request_preserves_explicit_adjustment(sample_instruments, adjustment):
    request = HistoryRequest(
        instruments=sample_instruments, adjustment=adjustment,
        start_date=date(2026, 9, 1), end_date=date(2026, 9, 3),
    )
    assert request.adjustment is adjustment


def test_request_rejects_unsupported_frequency(sample_instruments):
    with pytest.raises(ValidationError, match="frequency"):
        HistoryRequest(
            instruments=sample_instruments, frequency="MINUTE",
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 3),
        )


def test_dataset_normalizes_schema_order_and_row_order_without_changing_input(history_frame):
    original = history_frame.copy(deep=True)
    result = dataset(history_frame.loc[:, list(reversed(HISTORY_COLUMNS))])
    assert tuple(result.data.columns) == HISTORY_COLUMNS
    assert list(result.data[["instrument_key", "trade_date"]].itertuples(index=False, name=None)) == [
        ("CN:XSHE:159915", date(2026, 9, 2)),
        ("CN:XSHG:510300", date(2026, 9, 1)),
        ("CN:XSHG:510300", date(2026, 9, 2)),
        ("CN:XSHG:510300", date(2026, 9, 3)),
        ("US:XNAS:AAPL", date(2026, 9, 3)),
    ]
    assert result.data.index.tolist() == list(range(5))
    assert all(is_numeric_dtype(result.data[column]) for column in HISTORY_COLUMNS[2:])
    pd.testing.assert_frame_equal(history_frame, original)


def test_dataset_owns_a_copy_of_input(history_frame):
    result = dataset(history_frame)
    history_frame.loc[:, "volume"] = 0
    assert (result.data["volume"] > 0).all()


@pytest.mark.parametrize("column", HISTORY_COLUMNS)
def test_dataset_rejects_missing_columns(history_frame, column):
    with pytest.raises(ValidationError, match="exactly these columns"):
        dataset(history_frame.drop(columns=column))


def test_dataset_rejects_provider_specific_columns(history_frame):
    with pytest.raises(ValidationError, match="exactly these columns"):
        dataset(history_frame.assign(provider_symbol="sh510300"))


def test_dataset_rejects_duplicate_column_names(history_frame):
    with pytest.raises(ValidationError, match="duplicate column"):
        dataset(pd.concat([history_frame, history_frame[["volume"]]], axis=1))


def test_dataset_rejects_duplicate_instrument_dates(history_frame):
    with pytest.raises(ValidationError, match="duplicate instrument_key"):
        dataset(pd.concat([history_frame, history_frame.iloc[[0]]], ignore_index=True))


@pytest.mark.parametrize("column,value,error", [
    ("volume", -1, "volume must be non-negative"),
    ("high", 98.0, "high must be greater"),
    ("close", 104.0, "close must lie between"),
    ("close", 98.0, "close must lie between"),
    ("open", 104.0, "open must lie between"),
    ("open", 98.0, "open must lie between"),
    ("high", float("inf"), "finite values"),
    ("low", float("-inf"), "finite values"),
    ("close", float("nan"), "missing values"),
])
def test_dataset_rejects_invalid_bar_values(history_frame, column, value, error):
    history_frame.loc[0, column] = value
    with pytest.raises(ValidationError, match=error):
        dataset(history_frame)


@pytest.mark.parametrize("values", [["100"] * 5, [True] * 5, [1 + 2j] * 5])
def test_dataset_rejects_non_real_numeric_columns(history_frame, values):
    history_frame["volume"] = values
    with pytest.raises(ValidationError, match="real numeric column"):
        dataset(history_frame)


def test_zero_volume_and_flat_bars_are_valid(history_frame):
    history_frame.loc[:, "volume"] = 0
    for column in ("open", "high", "low", "close"):
        history_frame[column] = 1.0
    assert len(dataset(history_frame).data) == 5


@pytest.mark.parametrize("value", ["510300", "sh510300", "CN:XSHG:", "CN:XSHG:510300 ", "XX:XSHG:510300"])
def test_dataset_rejects_noncanonical_keys(history_frame, value):
    history_frame.loc[0, "instrument_key"] = value
    with pytest.raises(ValidationError):
        dataset(history_frame)


@pytest.mark.parametrize("value", ["2026-09-03", datetime(2026, 9, 3), datetime(2026, 9, 3, tzinfo=timezone.utc)])
def test_trade_date_does_not_silently_strip_timestamp_or_timezone(history_frame, value):
    history_frame["trade_date"] = [value] * len(history_frame)
    with pytest.raises(ValidationError, match="Python date values"):
        dataset(history_frame)


@pytest.mark.parametrize("tz", [timezone.utc, ZoneInfo("Asia/Shanghai")])
def test_generated_at_accepts_aware_datetime(history_frame, tz):
    moment = datetime(2026, 9, 4, tzinfo=tz)
    assert dataset(history_frame, generated_at=moment).generated_at == moment


def test_generated_at_rejects_naive_datetime(history_frame):
    with pytest.raises(ValidationError, match="timezone"):
        dataset(history_frame, generated_at=datetime(2026, 9, 4))


def test_provider_name_is_nonempty(history_frame):
    with pytest.raises(ValidationError, match="provider"):
        dataset(history_frame, provider="  ")


def test_empty_standard_frames_remain_valid_and_typed():
    empty = dataset(pd.DataFrame(columns=HISTORY_COLUMNS)).data
    pd.testing.assert_frame_equal(empty, empty_history_frame())


def test_domain_and_markets_keep_only_standard_library_pydantic_and_internal_imports():
    root = Path(__file__).resolve().parents[2]
    sources = [
        *(root / "aquant" / "domain").rglob("*.py"),
        *(root / "aquant" / "markets").rglob("*.py"),
    ]
    assert sources, "dependency guard must inspect actual source files"
    allowed = sys.stdlib_module_names | {"pydantic", "aquant"}
    violations = []
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] not in allowed or name.startswith(("aquant.data", "aquant.adapters")):
                    violations.append(f"{source.relative_to(root)}:{node.lineno}: {name}")
    assert not violations, "\n".join(violations)
