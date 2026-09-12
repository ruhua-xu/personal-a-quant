"""Shared local-only partition layout and result metadata."""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pandas as pd

from aquant.data import AdjustmentMode, BarFrequency, HistoryRequest, MarketDataSet


def encode_instrument_key(canonical_key: str) -> str:
    """Case-sensitive identity -> fixed-length, Windows-safe directory name.

    This is a path encoding, not a dataset content hash. The complete original
    instrument_key is retained inside every Parquet row.
    """
    return "i-" + sha256(canonical_key.encode("utf-8")).hexdigest()


def local_root(root_path: str | Path) -> Path:
    """Require an explicit local root, without creating it on a read."""
    raw = str(root_path).replace("\\", "/")
    if "://" in raw or raw.startswith("//"):
        raise ValueError("root_path must be a local filesystem path, not a URI or UNC path")
    root = Path(root_path).resolve()
    if root.as_posix().startswith("//"):
        raise ValueError("root_path must resolve to a local filesystem path")
    return root


def require_daily(frequency: BarFrequency) -> None:
    if frequency != BarFrequency.DAILY:
        raise ValueError("unsupported frequency: only DAILY history is implemented")


def partition_path(
    root: Path, frequency: BarFrequency, adjustment: AdjustmentMode,
    instrument_key: str, year: int,
) -> Path:
    require_daily(frequency)
    # Enum conversion prevents unchecked metadata becoming path components.
    path = (
        root / "bars" / BarFrequency(frequency).value / AdjustmentMode(adjustment).value
        / encode_instrument_key(instrument_key) / f"{year:04d}.parquet"
    )
    if not path.resolve().is_relative_to(root):
        raise ValueError("partition path escapes root_path")
    return path


def matching_files(root: Path, request: HistoryRequest) -> list[Path]:
    require_daily(request.frequency)
    AdjustmentMode(request.adjustment)
    return [
        path
        for key in sorted({item.canonical_key for item in request.instruments})
        for year in range(request.start_date.year, request.end_date.year + 1)
        if (path := partition_path(root, request.frequency, request.adjustment, key, year)).is_file()
    ]


def local_dataset(
    data: pd.DataFrame, frequency: BarFrequency, adjustment: AdjustmentMode,
) -> MarketDataSet:
    return MarketDataSet(
        data=data, frequency=frequency, adjustment=adjustment,
        provider="local_parquet", generated_at=datetime.now(timezone.utc),
    )
