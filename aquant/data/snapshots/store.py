"""Publish complete local snapshots and verify the bytes actually decoded.

Format 1 deliberately uses one independent bars.parquet per snapshot. It does
not change DatasetManifest schema 1, fetch data, filter reads, or repair files.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.parquet as pq

from aquant.data import AdjustmentMode, BarFrequency, HISTORY_COLUMNS, MarketDataSet
from aquant.data.versioning import (
    DatasetFileEntry, DatasetManifest, SourceMetadata, sha256_file,
)


class SnapshotError(ValueError):
    """Invalid input, missing/corrupt snapshot, or local filesystem failure."""


class SnapshotConflictError(SnapshotError):
    """Another publisher holds this version's lock; retry explicitly later."""


@dataclass(frozen=True)
class VerifiedSnapshot:
    """Verification context for this read, not a deeply immutable DataFrame.

    The returned objects are detached from disk; editing them cannot change the
    published snapshot. This is not a signed/authenticated provenance token.
    """

    manifest: DatasetManifest
    dataset: MarketDataSet
    verified_at: datetime

    @property
    def data_version(self) -> str:
        return self.manifest.data_version


def snapshot_directory_name(data_version: str) -> str:
    """Strict schema-1 version -> portable directory component, no colon."""
    if not isinstance(data_version, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", data_version):
        raise SnapshotError("data_version must be sha256:<64 lowercase hex digits>")
    return "sha256-" + data_version[7:]


def _reject_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        # FILE_ATTRIBUTE_REPARSE_POINT also rejects Windows directory junctions.
        raise SnapshotError(f"snapshot paths must not contain links/reparse points: {path.name}")


def _local_root(root_path: str | Path) -> Path:
    raw = str(root_path).replace("\\", "/")
    if "://" in raw or raw.startswith("//"):
        raise SnapshotError("snapshot root must be a local path, not a URI or UNC path")
    root = Path(root_path).absolute()
    for part in (*reversed(root.parents), root):
        _reject_link(part)
    return root.resolve()


def _path(root: Path, *parts: str) -> Path:
    """Only fixed format names or validated version components enter here."""
    path = root.joinpath(*parts)
    _reject_link(root)
    cursor = root
    for part in path.relative_to(root).parts:
        cursor = cursor / part
        _reject_link(cursor)
    if not path.resolve().is_relative_to(root):
        raise SnapshotError("snapshot path escapes root")
    return path


def _read_bytes(path: Path) -> bytes:
    _reject_link(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SnapshotError(f"snapshot requires independent regular files: {path.name}")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise SnapshotError("snapshot file is not independent")
        return stream.read()


def _metadata(frequency: BarFrequency, adjustment: AdjustmentMode) -> dict[bytes, bytes]:
    return {
        b"aquant.snapshot.format": b"1",
        b"aquant.frequency": frequency.value.encode("ascii"),
        b"aquant.adjustment": adjustment.value.encode("ascii"),
    }


def _table(dataset: MarketDataSet) -> pa.Table:
    frame = dataset.data
    arrays = [
        pa.array(frame["instrument_key"].tolist(), type=pa.string()),
        pa.array(frame["trade_date"].tolist(), type=pa.date32()),
        *(pa.array(frame[name]) for name in HISTORY_COLUMNS[2:]),
    ]
    # Preserve numeric types (including large integer volume); do not cast to
    # float. Exclude pandas index and acquisition timestamps from file identity.
    return pa.Table.from_arrays(arrays, names=HISTORY_COLUMNS).replace_schema_metadata(
        _metadata(dataset.frequency, dataset.adjustment),
    )


def _verify(directory: Path, expected_version: str | None = None) -> VerifiedSnapshot:
    _reject_link(directory)
    if not directory.is_dir():
        raise SnapshotError("snapshot directory is missing")
    manifest = DatasetManifest.model_validate_json(_read_bytes(directory / "manifest.json"))
    if expected_version is not None and manifest.data_version != expected_version:
        raise SnapshotError("manifest data_version does not match requested version/directory")
    if manifest.frequency != BarFrequency.DAILY:
        raise SnapshotError("snapshot format 1 supports only DAILY frequency")
    # A deliberately fixed layout is stricter than the generic Manifest model:
    # no untrusted manifest path is ever used to open a file.
    if len(manifest.files) != 1 or manifest.files[0].relative_path != "bars.parquet":
        raise SnapshotError("snapshot format 1 requires exactly one bars.parquet file entry")
    if {child.name for child in directory.iterdir()} != {"manifest.json", "bars.parquet"}:
        raise SnapshotError("snapshot contains missing or unlisted files")
    entry = manifest.files[0]
    payload = _read_bytes(directory / "bars.parquet")
    if sha256(payload).hexdigest() != entry.sha256:
        raise SnapshotError("Parquet SHA256 mismatch")
    # Decode the exact buffer that was hashed, never reopen the pathname after
    # verification. No directory discovery, Hive inference or network fallback.
    with pq.ParquetFile(pa.BufferReader(payload)) as parquet:
        schema = parquet.schema_arrow
        if tuple(schema.names) != HISTORY_COLUMNS:
            raise SnapshotError("Parquet schema must match HISTORY_COLUMNS in order")
        if schema.field("instrument_key").type != pa.string() or schema.field("trade_date").type != pa.date32():
            raise SnapshotError("Parquet instrument/date types must be string/date32")
        for name in HISTORY_COLUMNS[2:]:
            kind = schema.field(name).type
            if not (pa.types.is_integer(kind) or pa.types.is_floating(kind)):
                raise SnapshotError(f"Parquet {name} must be real numeric")
        for key, value in _metadata(manifest.frequency, manifest.adjustment).items():
            if (schema.metadata or {}).get(key) != value:
                raise SnapshotError(f"Parquet metadata mismatch: {key.decode('ascii')}")
        if parquet.metadata.num_rows != entry.row_count:
            raise SnapshotError("Parquet file row_count mismatch")
        table = parquet.read()
    if table.num_rows != entry.row_count or entry.row_count != manifest.row_count:
        raise SnapshotError("manifest total row_count mismatch")
    if any(column.null_count for column in table.columns):
        raise SnapshotError("Parquet bars must not contain nulls")
    frame = table.to_pandas(date_as_object=True)
    frame["instrument_key"] = frame["instrument_key"].astype("str")
    dataset = MarketDataSet(
        data=frame, frequency=manifest.frequency, adjustment=manifest.adjustment,
        provider=manifest.source.provider_name, generated_at=manifest.created_at,
    )
    if tuple(sorted(set(dataset.data["instrument_key"]))) != manifest.instruments:
        raise SnapshotError("manifest instruments do not match actual bars")
    if not dataset.data.empty and not dataset.data["trade_date"].between(
        manifest.start_date, manifest.end_date, inclusive="both",
    ).all():
        raise SnapshotError("bars fall outside manifest date range")
    return VerifiedSnapshot(manifest, dataset, datetime.now(timezone.utc))


class SnapshotReader:
    """Read a whole snapshot by exact version, without creating directories."""

    def __init__(self, root_path: str | Path) -> None:
        self._root = _local_root(root_path)

    def read(self, data_version: str) -> VerifiedSnapshot:
        try:
            name = snapshot_directory_name(data_version)
            return _verify(_path(self._root, "snapshots", name), data_version)
        except SnapshotError:
            raise
        except (OSError, ValueError, TypeError, pa.ArrowException) as exc:
            raise SnapshotError(f"snapshot read failed: {exc}") from exc


class SnapshotPublisher:
    """Publish an independent, validated snapshot; never overwrite a version.

    Publishers using this API serialize each version with an exclusive local
    lock file. Contention fails promptly; abandoned locks need manual review.
    This is not an adversarial filesystem sandbox or a power-loss transaction.
    """

    def __init__(self, root_path: str | Path) -> None:
        self._root = _local_root(root_path)

    def publish(
        self, dataset: MarketDataSet, source: SourceMetadata, *,
        start_date: date, end_date: date,
    ) -> DatasetManifest:
        """Inclusive declared coverage; required even for a zero-row dataset."""
        try:
            if type(start_date) is not date or type(end_date) is not date or start_date > end_date:
                raise SnapshotError("start_date/end_date must be ordered Python dates")
            # Rebuild, as frozen Pydantic metadata does not freeze pandas/dicts
            # and model_copy(update=...) can bypass validation.
            dataset = MarketDataSet(**{name: getattr(dataset, name) for name in MarketDataSet.model_fields})
            source = SourceMetadata(**{name: getattr(source, name) for name in SourceMetadata.model_fields})
            if dataset.frequency != BarFrequency.DAILY:
                raise SnapshotError("snapshot format 1 supports only DAILY frequency")
            if not dataset.data.empty and not dataset.data["trade_date"].between(start_date, end_date).all():
                raise SnapshotError("bars fall outside declared date range")
            return self._publish(dataset, source, start_date, end_date)
        except SnapshotError:
            raise
        except (OSError, ValueError, TypeError, pa.ArrowException) as exc:
            raise SnapshotError(f"snapshot publication failed: {exc}") from exc

    def _publish(
        self, dataset: MarketDataSet, source: SourceMetadata,
        start_date: date, end_date: date,
    ) -> DatasetManifest:
        staging_root = _path(self._root, ".staging")
        staging_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="snapshot-", dir=staging_root) as temporary:
            staged = _path(self._root, ".staging", Path(temporary).name)
            bars = staged / "bars.parquet"
            pq.write_table(
                _table(dataset), bars, version="2.6", compression="NONE",
                use_dictionary=False, row_group_size=65536, write_statistics=True,
            )
            manifest = DatasetManifest.create(
                created_at=datetime.now(timezone.utc),
                frequency=dataset.frequency, adjustment=dataset.adjustment,
                instruments=tuple(sorted(set(dataset.data["instrument_key"]))),
                start_date=start_date, end_date=end_date, row_count=len(dataset.data),
                files=[DatasetFileEntry(relative_path="bars.parquet", sha256=sha256_file(bars), row_count=len(dataset.data))],
                source=source,
            )
            (staged / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            _verify(staged, manifest.data_version)
            return self._publish_directory(staged, manifest.data_version)

    def _publish_directory(self, staged: Path, data_version: str) -> DatasetManifest:
        name = snapshot_directory_name(data_version)
        published_root = _path(self._root, "snapshots")
        locks_root = _path(self._root, ".publish-locks")
        published_root.mkdir(parents=True, exist_ok=True)
        locks_root.mkdir(parents=True, exist_ok=True)
        lock = _path(self._root, ".publish-locks", name + ".lock")
        try:
            with lock.open("xb"):
                pass
        except FileExistsError as exc:
            raise SnapshotConflictError("snapshot publication lock already exists; retry after publisher finishes") from exc
        try:
            target = _path(self._root, "snapshots", name)
            if target.exists():
                # Reuse only after verifying every byte and semantic invariant.
                return _verify(target, data_version).manifest
            # Do not use replace(). On POSIX the lock protects the existence
            # check against other publishers using this API; Windows rename
            # itself also refuses an existing destination.
            os.rename(staged, target)
            return _verify(target, data_version).manifest
        finally:
            lock.unlink()
