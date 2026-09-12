"""Partitioned local historical bars with single-file atomic upserts."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from aquant.data import HistoryRequest, MarketDataSet

from ._layout import local_dataset, local_root, partition_path, require_daily
from .base import HistoricalDataStore
from .duckdb_reader import DuckDBHistoryReader


class ParquetHistoryStore(HistoricalDataStore):
    """Store one instrument/frequency/adjustment/year per Parquet file.

    Use a single writer (or external serialization). Atomicity is per file,
    not a multi-partition transaction; concurrent writers and whole-dataset
    snapshots are not implemented. A failed later partition can be retried.
    Caller frames are copied and revalidated, never edited in place.
    """

    def __init__(self, root_path: str | Path) -> None:
        self._root = local_root(root_path)

    def write(self, dataset: MarketDataSet) -> None:
        require_daily(dataset.frequency)
        # Frozen metadata does not prevent callers from editing pandas data.
        snapshot = MarketDataSet(
            data=dataset.data, frequency=dataset.frequency, adjustment=dataset.adjustment,
            provider=dataset.provider, generated_at=dataset.generated_at,
        )
        frame = snapshot.data
        years = frame["trade_date"].map(lambda value: value.year)
        for (key, year), incoming in frame.groupby(["instrument_key", years], sort=True):
            path = partition_path(self._root, snapshot.frequency, snapshot.adjustment, key, year)
            if path.exists():
                existing = self._validated_partition(path, snapshot, key, year)
                incoming = pd.concat([existing, incoming], ignore_index=True).drop_duplicates(
                    ["instrument_key", "trade_date"], keep="last",
                )
            merged = local_dataset(incoming, snapshot.frequency, snapshot.adjustment)
            self._atomic_write(path, merged, key, year)

    @staticmethod
    def _validated_partition(
        path: Path, metadata: MarketDataSet, key: str, year: int,
    ) -> pd.DataFrame:
        # Read exactly one local file, without Hive directory inference.
        with pq.ParquetFile(path) as parquet:
            frame = parquet.read().to_pandas(date_as_object=True)
        validated = local_dataset(frame, metadata.frequency, metadata.adjustment).data
        if not validated["instrument_key"].eq(key).all() or not validated["trade_date"].map(
            lambda value: value.year == year,
        ).all():
            raise ValueError("stored bars do not match their instrument/year partition")
        return validated

    def _atomic_write(self, path: Path, dataset: MarketDataSet, key: str, year: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Close the temporary handle before PyArrow/os.replace on Windows.
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp", delete=False) as temp:
            temporary = Path(temp.name)
        try:
            table = pa.Table.from_pandas(dataset.data, preserve_index=False)
            pq.write_table(table, temporary)
            # Validate the serialized bars before publishing the replacement.
            self._validated_partition(temporary, dataset, key, year)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, request: HistoryRequest) -> MarketDataSet:
        return DuckDBHistoryReader(self._root).read(request)
