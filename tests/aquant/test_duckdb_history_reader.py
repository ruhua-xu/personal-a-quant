from datetime import date, datetime, timezone
import socket

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from aquant.data import AdjustmentMode, HISTORY_COLUMNS, empty_history_frame
from aquant.data.storage import DuckDBHistoryReader, ParquetHistoryStore
from aquant.data.storage._layout import partition_path
from aquant.data.storage import duckdb_reader as reader_module
from tests.aquant.test_parquet_history_store import dataset_with, request_for, sh_frame


def test_direct_reader_round_trip_and_metadata(tmp_path, sample_dataset, sample_instruments):
    ParquetHistoryStore(tmp_path).write(sample_dataset)
    before = datetime.now(timezone.utc)
    result = DuckDBHistoryReader(tmp_path).read(request_for(sample_instruments))
    after = datetime.now(timezone.utc)
    pd.testing.assert_frame_equal(result.data, sample_dataset.data)
    assert tuple(result.data.columns) == HISTORY_COLUMNS
    assert all(type(value) is date for value in result.data["trade_date"])
    assert not result.data.duplicated(["instrument_key", "trade_date"]).any()
    assert result.provider == "local_parquet"
    assert before <= result.generated_at <= after
    assert result.generated_at.utcoffset().total_seconds() == 0


def test_query_uses_real_duckdb_with_bound_filters(tmp_path, sample_dataset, sample_instruments, monkeypatch):
    ParquetHistoryStore(tmp_path).write(sample_dataset)
    real_connect = duckdb.connect
    calls = []
    connections = []

    class ObservedConnection:
        def __init__(self, *args, **kwargs):
            assert args == (":memory:",)
            assert kwargs["config"]["autoinstall_known_extensions"] is False
            assert kwargs["config"]["autoload_known_extensions"] is False
            self.connection = real_connect(*args, **kwargs)
            connections.append(self.connection)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.connection.close()

        def execute(self, sql, parameters):
            calls.append((sql, parameters))
            return self.connection.execute(sql, parameters)

    def no_pandas_parquet(*args, **kwargs):
        raise AssertionError("reader must query Parquet in DuckDB")

    monkeypatch.setattr(reader_module.duckdb, "connect", ObservedConnection)
    monkeypatch.setattr(pd, "read_parquet", no_pandas_parquet)
    monkeypatch.setattr(pq, "read_table", no_pandas_parquet)
    monkeypatch.setattr(pq, "ParquetFile", no_pandas_parquet)
    request = request_for(sample_instruments[:1], start_date=date(2026, 9, 2))
    result = DuckDBHistoryReader(tmp_path).read(request)
    assert result.data["trade_date"].tolist() == [date(2026, 9, 2), date(2026, 9, 3)]
    assert len(calls) == 1
    sql, parameters = calls[0]
    assert "read_parquet(?)" in sql.replace(", hive_partitioning = false", "")
    assert "instrument_key IN (?)" in sql
    assert "trade_date >= ?" in sql and "trade_date <= ?" in sql
    assert parameters[1:] == [sample_instruments[0].canonical_key, request.start_date, request.end_date]
    assert len(parameters[0]) == 1
    assert sample_instruments[0].canonical_key not in sql
    with pytest.raises(duckdb.ConnectionException):
        connections[0].execute("SELECT 1")


def test_sql_instrument_filter_is_not_only_partition_selection(tmp_path, sample_dataset, sample_instruments):
    # Place valid rows for other instruments in a candidate file to prove the
    # SQL predicate itself excludes them, independently of file pruning.
    path = partition_path(tmp_path, "DAILY", "RAW", sample_instruments[0].canonical_key, 2026)
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pandas(sample_dataset.data, preserve_index=False), path)
    result = DuckDBHistoryReader(tmp_path).read(request_for(sample_instruments[:1]))
    pd.testing.assert_frame_equal(result.data, sh_frame(sample_dataset))


def test_unrelated_instrument_year_and_adjustment_files_are_not_scanned(tmp_path, sample_dataset, sample_instruments):
    store = ParquetHistoryStore(tmp_path)
    store.write(sample_dataset)
    key = sample_instruments[0].canonical_key
    unrelated = [
        partition_path(tmp_path, "DAILY", "RAW", sample_instruments[1].canonical_key, 2026),
        partition_path(tmp_path, "DAILY", "RAW", key, 2025),
        partition_path(tmp_path, "DAILY", "FORWARD", key, 2026),
    ]
    for path in unrelated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"invalid parquet: this file must not be scanned")
    result = DuckDBHistoryReader(tmp_path).read(request_for(sample_instruments[:1]))
    pd.testing.assert_frame_equal(result.data, sh_frame(sample_dataset))


def test_multi_instrument_multi_year_query(tmp_path, sample_dataset, sample_instruments):
    old = sample_dataset.data.copy()
    old["trade_date"] = old["trade_date"].map(lambda value: value.replace(year=2025))
    store = ParquetHistoryStore(tmp_path)
    store.write(dataset_with(sample_dataset, old))
    store.write(sample_dataset)
    result = DuckDBHistoryReader(tmp_path).read(request_for(
        [sample_instruments[0], sample_instruments[2]], start_date=date(2025, 9, 3), end_date=date(2026, 9, 1),
    ))
    assert list(zip(result.data["instrument_key"], result.data["trade_date"])) == [
        (sample_instruments[0].canonical_key, date(2025, 9, 3)),
        (sample_instruments[0].canonical_key, date(2026, 9, 1)),
        (sample_instruments[2].canonical_key, date(2025, 9, 3)),
    ]


@pytest.mark.parametrize("no_match", ["absent_root", "dates", "instrument", "adjustment"])
def test_empty_results_are_valid(tmp_path, sample_dataset, sample_instruments, no_match):
    root = tmp_path / "store"
    request = request_for(sample_instruments[:1])
    if no_match != "absent_root":
        ParquetHistoryStore(root).write(dataset_with(sample_dataset, sh_frame(sample_dataset)))
    if no_match == "dates":
        request = request_for(sample_instruments, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2))
    elif no_match == "instrument":
        request = request_for(sample_instruments[1:])
    elif no_match == "adjustment":
        request = request_for(sample_instruments, adjustment=AdjustmentMode.FORWARD)
    result = DuckDBHistoryReader(root).read(request)
    pd.testing.assert_frame_equal(result.data, empty_history_frame())
    assert result.frequency == request.frequency and result.adjustment == request.adjustment
    assert result.provider == "local_parquet"
    if no_match == "absent_root":
        assert not root.exists()


def test_future_frequency_does_not_fall_back_to_daily(tmp_path, sample_instruments):
    request = request_for(sample_instruments).model_copy(update={"frequency": "MINUTE"})
    with pytest.raises(ValueError, match="frequency"):
        DuckDBHistoryReader(tmp_path).read(request)


@pytest.mark.parametrize("corruption", ["negative_volume", "duplicate", "invalid_ohlc", "timestamp"])
def test_reader_revalidates_disk_data(tmp_path, sample_dataset, sample_instruments, corruption):
    frame = sh_frame(sample_dataset)
    if corruption == "negative_volume":
        frame.loc[:, "volume"] = -1
    elif corruption == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    elif corruption == "invalid_ohlc":
        frame.loc[:, "high"] = 0
    else:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    path = partition_path(tmp_path, "DAILY", "RAW", sample_instruments[0].canonical_key, 2026)
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path)
    with pytest.raises(ValidationError):
        DuckDBHistoryReader(tmp_path).read(request_for(sample_instruments))


def test_response_edits_do_not_change_disk(tmp_path, sample_dataset, sample_instruments):
    ParquetHistoryStore(tmp_path).write(sample_dataset)
    reader = DuckDBHistoryReader(tmp_path)
    result = reader.read(request_for(sample_instruments))
    result.data.loc[:, "volume"] = -1
    pd.testing.assert_frame_equal(reader.read(request_for(sample_instruments)).data, sample_dataset.data)


def test_storage_is_offline(tmp_path, sample_dataset, sample_instruments):
    # The autouse guard blocks Python sockets throughout all aquant tests;
    # DuckDB's native path is restricted to local files, without extensions.
    with pytest.raises(AssertionError, match="must not access the network"):
        socket.create_connection(("example.invalid", 443))
    ParquetHistoryStore(tmp_path).write(sample_dataset)
    result = DuckDBHistoryReader(tmp_path).read(request_for(sample_instruments))
    assert len(result.data) == len(sample_dataset.data)


def test_store_read_delegates_to_duckdb(tmp_path, sample_instruments, monkeypatch):
    request = request_for(sample_instruments)
    sentinel = object()
    calls = []

    def observe(self, actual_request):
        calls.append(actual_request)
        return sentinel

    monkeypatch.setattr(DuckDBHistoryReader, "read", observe)
    assert ParquetHistoryStore(tmp_path).read(request) is sentinel
    assert calls == [request]


def test_symbol_sql_and_path_characters_are_only_data(tmp_path, sample_dataset, sample_instruments):
    instrument = sample_instruments[0].model_copy(update={"symbol": "../odd:' OR 1=1 --\\*[]"})
    frame = sh_frame(sample_dataset)
    frame.loc[:, "instrument_key"] = instrument.canonical_key
    root = tmp_path / "local's store [fixture]"
    store = ParquetHistoryStore(root)
    source = dataset_with(sample_dataset, frame)
    store.write(source)
    result = DuckDBHistoryReader(root).read(request_for([instrument]))
    pd.testing.assert_frame_equal(result.data, source.data)
    assert len(list(root.rglob("*.parquet"))) == 1
    assert DuckDBHistoryReader(root).read(request_for(sample_instruments)).data.empty


def test_repeated_request_instruments_do_not_duplicate_results(tmp_path, sample_dataset, sample_instruments):
    ParquetHistoryStore(tmp_path).write(sample_dataset)
    result = DuckDBHistoryReader(tmp_path).read(request_for([sample_instruments[0]] * 2))
    pd.testing.assert_frame_equal(result.data, sh_frame(sample_dataset))
