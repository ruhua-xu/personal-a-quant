from datetime import date, datetime, timezone
from pathlib import Path
import re

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from aquant.data import AdjustmentMode, HISTORY_COLUMNS, HistoryRequest, MarketDataSet, empty_history_frame
from aquant.data.storage import HistoricalDataStore, ParquetHistoryStore
from aquant.data.storage._layout import encode_instrument_key, partition_path
from aquant.data.storage import parquet as parquet_module


def request_for(instruments, **overrides):
    values = dict(instruments=instruments, start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
    values.update(overrides)
    return HistoryRequest(**values)


def dataset_with(source, data=None, **overrides):
    values = dict(
        data=source.data if data is None else data, frequency=source.frequency,
        adjustment=source.adjustment, provider=source.provider, generated_at=source.generated_at,
    )
    values.update(overrides)
    return MarketDataSet(**values)


def sh_frame(source):
    return source.data.loc[source.data["instrument_key"].eq("CN:XSHG:510300")].reset_index(drop=True)


def test_single_instrument_round_trip(tmp_path, sample_dataset, sample_instruments):
    store = ParquetHistoryStore(tmp_path)
    assert isinstance(store, HistoricalDataStore)
    source = dataset_with(sample_dataset, sh_frame(sample_dataset))
    store.write(source)
    before = datetime.now(timezone.utc)
    result = store.read(request_for(sample_instruments[:1]))
    after = datetime.now(timezone.utc)
    pd.testing.assert_frame_equal(result.data, source.data)
    assert result.provider == "local_parquet"
    assert before <= result.generated_at <= after
    assert result.generated_at.utcoffset().total_seconds() == 0
    assert tuple(result.data.columns) == HISTORY_COLUMNS
    assert all(type(value) is date for value in result.data["trade_date"])


def test_multiple_instruments_and_partition_layout(tmp_path, sample_dataset, sample_instruments):
    store = ParquetHistoryStore(tmp_path)
    store.write(sample_dataset)
    pd.testing.assert_frame_equal(store.read(request_for(sample_instruments)).data, sample_dataset.data)
    paths = list(tmp_path.rglob("*.parquet"))
    assert len(paths) == 3
    for path in paths:
        assert path.relative_to(tmp_path).parts[:3] == ("bars", "DAILY", "RAW")
        assert path.name == "2026.parquet"
        assert re.fullmatch(r"i-[0-9a-f]{64}", path.parent.name)
        assert ":" not in str(path.relative_to(tmp_path))


def test_cross_year_partitions(tmp_path, sample_dataset, sample_instruments):
    frame = sh_frame(sample_dataset)
    frame["trade_date"] = [date(2025, 12, 31), date(2026, 1, 1), date(2026, 1, 2)]
    source = dataset_with(sample_dataset, frame)
    store = ParquetHistoryStore(tmp_path)
    store.write(source)
    assert sorted(path.name for path in tmp_path.rglob("*.parquet")) == ["2025.parquet", "2026.parquet"]
    result = store.read(request_for(sample_instruments[:1], start_date=date(2025, 12, 31), end_date=date(2026, 1, 1)))
    assert result.data["trade_date"].tolist() == [date(2025, 12, 31), date(2026, 1, 1)]


def test_date_and_instrument_filtering(tmp_path, sample_dataset, sample_instruments):
    store = ParquetHistoryStore(tmp_path)
    store.write(sample_dataset)
    result = store.read(request_for(sample_instruments[:1], start_date=date(2026, 9, 2)))
    assert result.data["instrument_key"].tolist() == [sample_instruments[0].canonical_key] * 2
    assert result.data["trade_date"].tolist() == [date(2026, 9, 2), date(2026, 9, 3)]


def test_empty_store_does_not_create_directory(tmp_path, sample_instruments):
    root = tmp_path / "not-created"
    result = ParquetHistoryStore(root).read(request_for(sample_instruments))
    pd.testing.assert_frame_equal(result.data, empty_history_frame())
    assert result.provider == "local_parquet"
    assert not root.exists()


def test_no_matching_dates(tmp_path, sample_dataset, sample_instruments):
    store = ParquetHistoryStore(tmp_path)
    store.write(sample_dataset)
    result = store.read(request_for(sample_instruments, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2)))
    pd.testing.assert_frame_equal(result.data, empty_history_frame())


def test_repeated_write_is_idempotent(tmp_path, sample_dataset, sample_instruments):
    store = ParquetHistoryStore(tmp_path)
    store.write(sample_dataset)
    store.write(sample_dataset)
    result = store.read(request_for(sample_instruments))
    pd.testing.assert_frame_equal(result.data, sample_dataset.data)
    assert not result.data.duplicated(["instrument_key", "trade_date"]).any()


def test_incremental_upsert_replaces_old_day(tmp_path, sample_dataset, sample_instruments):
    store = ParquetHistoryStore(tmp_path)
    frame = sh_frame(sample_dataset)
    store.write(dataset_with(sample_dataset, frame.iloc[:2]))
    newer = frame.iloc[1:3].copy()
    newer.loc[newer.index[0], "close"] = 4.1
    newer.loc[newer.index[0], "volume"] = 9999
    store.write(dataset_with(sample_dataset, newer))
    result = store.read(request_for(sample_instruments[:1])).data
    assert result["trade_date"].tolist() == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    assert result["close"].tolist() == [3.9, 4.1, 4.2]
    assert result["volume"].tolist() == [1000, 9999, 1200]


@pytest.mark.parametrize("adjustment", [AdjustmentMode.FORWARD, AdjustmentMode.BACKWARD])
def test_adjustment_isolation(tmp_path, sample_dataset, sample_instruments, adjustment):
    store = ParquetHistoryStore(tmp_path)
    store.write(sample_dataset)
    adjusted = sample_dataset.data.copy()
    adjusted.loc[:, "volume"] = 42
    store.write(dataset_with(sample_dataset, adjusted, adjustment=adjustment))
    raw = store.read(request_for(sample_instruments))
    other = store.read(request_for(sample_instruments, adjustment=adjustment))
    pd.testing.assert_frame_equal(raw.data, sample_dataset.data)
    assert other.data["volume"].eq(42).all()
    assert other.adjustment == adjustment
    assert len(list(tmp_path.rglob("*.parquet"))) == 6


@pytest.mark.parametrize("key", [
    "CN:XSHG:510300", "US:XNAS:510300", "US:XNAS:../escape", r"US:XNAS:..\escape",
    'US:XNAS:C:/a?b*<>|"', "US:XNAS:CON", "US:XNAS:股票", "US:XNAS:" + "a" * 1000,
])
def test_safe_path_encoding_is_stable_and_cannot_traverse(tmp_path, key):
    encoded = encode_instrument_key(key)
    assert encoded == encode_instrument_key(key)
    assert re.fullmatch(r"i-[0-9a-f]{64}", encoded)
    path = partition_path(tmp_path, "DAILY", "RAW", key, 2026)
    assert path.resolve().is_relative_to(tmp_path)
    assert path.parent.name == encoded


def test_full_case_sensitive_identity_is_encoded():
    keys = ["CN:XSHG:510300", "US:XNAS:510300", "US:XNAS:AAPL", "US:XNAS:aapl"]
    assert len({encode_instrument_key(key) for key in keys}) == len(keys)


def test_mutations_do_not_change_source_or_disk(tmp_path, sample_dataset, sample_instruments):
    expected = sample_dataset.data.copy(deep=True)
    store = ParquetHistoryStore(tmp_path)
    store.write(sample_dataset)
    pd.testing.assert_frame_equal(sample_dataset.data, expected)
    sample_dataset.data.loc[:, "volume"] = -1
    first = store.read(request_for(sample_instruments))
    pd.testing.assert_frame_equal(first.data, expected)
    first.data.loc[:, "volume"] = -1
    pd.testing.assert_frame_equal(store.read(request_for(sample_instruments)).data, expected)


def test_edited_invalid_dataset_rejected_before_writing(tmp_path, sample_dataset):
    sample_dataset.data.loc[:, "volume"] = -1
    with pytest.raises(ValidationError, match="volume"):
        ParquetHistoryStore(tmp_path).write(sample_dataset)
    assert list(tmp_path.iterdir()) == []


def test_empty_write_is_noop(tmp_path, sample_dataset):
    ParquetHistoryStore(tmp_path).write(dataset_with(sample_dataset, empty_history_frame()))
    assert list(tmp_path.iterdir()) == []


def test_atomic_replace_uses_validated_temp_in_same_directory(tmp_path, sample_dataset, monkeypatch):
    original = parquet_module.os.replace
    calls = []

    def observe(source, destination):
        assert Path(source).parent == Path(destination).parent
        assert Path(source) != Path(destination)
        assert Path(source).suffix == ".tmp"
        dataset_with(sample_dataset, pq.ParquetFile(source).read().to_pandas(date_as_object=True))
        calls.append((source, destination))
        original(source, destination)

    monkeypatch.setattr(parquet_module.os, "replace", observe)
    ParquetHistoryStore(tmp_path).write(sample_dataset)
    assert len(calls) == 3
    assert not list(tmp_path.rglob("*.tmp"))


def test_failed_replace_preserves_previous_file_and_cleans_temp(tmp_path, sample_dataset, sample_instruments, monkeypatch):
    store = ParquetHistoryStore(tmp_path)
    store.write(sample_dataset)

    def fail(*args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(parquet_module.os, "replace", fail)
    changed = sample_dataset.data.copy()
    changed.loc[:, "volume"] = 0
    with pytest.raises(OSError, match="simulated"):
        store.write(dataset_with(sample_dataset, changed))
    pd.testing.assert_frame_equal(store.read(request_for(sample_instruments)).data, sample_dataset.data)
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("corruption", ["volume", "duplicate", "instrument", "year"])
def test_upsert_rejects_invalid_existing_partition(tmp_path, sample_dataset, corruption):
    store = ParquetHistoryStore(tmp_path)
    source = dataset_with(sample_dataset, sh_frame(sample_dataset))
    store.write(source)
    path = next(tmp_path.rglob("*.parquet"))
    frame = source.data.copy()
    if corruption == "volume":
        frame.loc[:, "volume"] = -1
    elif corruption == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    elif corruption == "instrument":
        frame.loc[:, "instrument_key"] = "US:XNAS:OTHER"
    else:
        frame.loc[0, "trade_date"] = date(2025, 9, 1)
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        store.write(source)
    assert path.read_bytes() == before


def test_unsupported_frequency_rejected_for_read_and_write(tmp_path, sample_dataset, sample_instruments):
    store = ParquetHistoryStore(tmp_path)
    with pytest.raises(ValueError, match="frequency"):
        store.write(sample_dataset.model_copy(update={"frequency": "MINUTE"}))
    with pytest.raises(ValueError, match="frequency"):
        store.read(request_for(sample_instruments).model_copy(update={"frequency": "MINUTE"}))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("root", ["https://example.invalid/bars", "s3://bucket/bars", r"\\server\bars"])
def test_remote_roots_rejected(root):
    with pytest.raises(ValueError, match="local filesystem"):
        ParquetHistoryStore(root)
