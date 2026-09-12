"""Read local Parquet through DuckDB with instrument and date predicates."""

from pathlib import Path

import duckdb

from aquant.data import HISTORY_COLUMNS, HistoryRequest, MarketDataSet, empty_history_frame

from ._layout import local_dataset, local_root, matching_files


class DuckDBHistoryReader:
    """Query only candidate instrument/year partitions, without a persistent DB.

    Paths and filter values are bound parameters, never interpolated SQL.
    No extensions are installed/loaded automatically and only checked local
    files are queried. The result is materialized *after* SQL filtering and
    revalidated as a MarketDataSet. Reads do not create a store directory.
    """

    def __init__(self, root_path: str | Path) -> None:
        self._root = local_root(root_path)

    def read(self, request: HistoryRequest) -> MarketDataSet:
        files = matching_files(self._root, request)
        data = empty_history_frame()
        if files:
            keys = sorted({item.canonical_key for item in request.instruments})
            placeholders = ", ".join("?" for _ in keys)
            # Only fixed contract column names and placeholder counts enter SQL.
            columns = ", ".join(f'"{column}"' for column in HISTORY_COLUMNS)
            query = f"""
                SELECT {columns}
                FROM read_parquet(?, hive_partitioning = false)
                WHERE instrument_key IN ({placeholders})
                  AND trade_date >= ?
                  AND trade_date <= ?
                ORDER BY instrument_key, trade_date
            """
            with duckdb.connect(":memory:", config={
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
            }) as connection:
                table = connection.execute(
                    query, [[str(path) for path in files], *keys, request.start_date, request.end_date],
                ).to_arrow_table()
                # DATE becomes Python date, not a naive pandas timestamp.
                data = table.to_pandas(date_as_object=True)
        return local_dataset(data, request.frequency, request.adjustment)
