"""Offline snapshot publication, integrity, semantic and isolation tests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from threading import Event

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aquant.data import AdjustmentMode, HISTORY_COLUMNS, HistoryRequest, MarketDataSet, empty_history_frame
from aquant.data.snapshots import (
    SnapshotConflictError, SnapshotError, SnapshotPublisher, SnapshotReader,
    VerifiedSnapshot, snapshot_directory_name,
)
from aquant.data.snapshots import store as snapshots
from aquant.data.storage import ParquetHistoryStore
from aquant.data.versioning import DatasetManifest, SourceMetadata, sha256_file


START = date(2026, 9, 1)
END = date(2026, 9, 3)


@pytest.fixture
def source():
    return SourceMetadata(provider_name="fixture", provider_version="1", metadata={"unit": "shares"})


def publish(root, dataset, source, **kwargs):
    return SnapshotPublisher(root).publish(
        dataset, source, start_date=kwargs.get("start_date", START), end_date=kwargs.get("end_date", END),
    )


def directory(root, manifest):
    return root / "snapshots" / snapshot_directory_name(manifest.data_version)


def changed_dataset(dataset, **kwargs):
    values = {name: getattr(dataset, name) for name in MarketDataSet.model_fields}
    values.update(kwargs)
    return MarketDataSet(**values)


def rewrite_manifest(root, manifest, **overrides):
    """Rehash and rename after a fixture edit, so semantic tests pass SHA checks."""
    old = directory(root, manifest)
    identity = manifest.identity_payload()
    identity.update(overrides)
    updated = DatasetManifest.create(created_at=manifest.created_at, **identity)
    (old / "manifest.json").write_text(updated.model_dump_json(), encoding="utf-8")
    if old != directory(root, updated):
        old.rename(directory(root, updated))
    return updated


def rewrite_table(root, manifest, transform):
    path = directory(root, manifest) / "bars.parquet"
    with pq.ParquetFile(path) as parquet:
        table = transform(parquet.read())
    pq.write_table(table, path)
    return rewrite_manifest(root, manifest, files=[{
        "relative_path": "bars.parquet", "sha256": sha256_file(path),
        "row_count": manifest.files[0].row_count,
    }])


def test_round_trip_and_verified_context(tmp_path, sample_dataset, source):
    manifest = publish(tmp_path, sample_dataset, source)
    result = SnapshotReader(tmp_path).read(manifest.data_version)
    assert isinstance(result, VerifiedSnapshot)
    assert result.data_version == manifest.data_version
    assert result.manifest == manifest
    pd.testing.assert_frame_equal(result.dataset.data, sample_dataset.data)
    assert result.dataset.frequency == sample_dataset.frequency
    assert result.dataset.adjustment == sample_dataset.adjustment
    assert result.dataset.provider == source.provider_name
    assert result.dataset.generated_at == manifest.created_at
    assert result.verified_at.utcoffset().total_seconds() == 0
    assert all(type(value) is date for value in result.dataset.data.trade_date)
    assert {p.name for p in directory(tmp_path, manifest).iterdir()} == {"manifest.json", "bars.parquet"}
    assert list((tmp_path / ".staging").iterdir()) == []
    assert list((tmp_path / ".publish-locks").iterdir()) == []


def test_empty_snapshot_has_typed_file_and_explicit_coverage(tmp_path, sample_dataset, source):
    manifest = publish(tmp_path, changed_dataset(sample_dataset, data=empty_history_frame()), source)
    result = SnapshotReader(tmp_path).read(manifest.data_version)
    assert manifest.row_count == manifest.files[0].row_count == 0
    assert manifest.instruments == ()
    assert (manifest.start_date, manifest.end_date) == (START, END)
    pd.testing.assert_frame_equal(result.dataset.data, empty_history_frame())


def test_missing_snapshot_is_error_and_does_not_create_root(tmp_path):
    root = tmp_path / "absent"
    with pytest.raises(SnapshotError, match="missing"):
        SnapshotReader(root).read("sha256:" + "0" * 64)
    assert not root.exists()


def test_windows_safe_directory_mapping():
    assert snapshot_directory_name("sha256:" + "ab" * 32) == "sha256-" + "ab" * 32


@pytest.mark.parametrize("version", [
    "latest", "", None, 42, "sha256:" + "A" * 64, "sha256:" + "a" * 63,
    "sha256:" + "a" * 65, "sha256:" + "a" * 64 + "\n", "../outside",
    "sha256:" + "0" * 64 + "/../x", "C:\\outside", "https://example.test/data",
])
def test_invalid_versions_rejected_before_io(tmp_path, version):
    with pytest.raises(SnapshotError, match="data_version"):
        SnapshotReader(tmp_path).read(version)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("root", ["https://example.test/data", "s3://bucket", "//server/share", "\\\\server\\share"])
@pytest.mark.parametrize("kind", [SnapshotPublisher, SnapshotReader])
def test_roots_must_be_local(kind, root):
    with pytest.raises(SnapshotError, match="local path"):
        kind(root)


def test_idempotent_reordered_input_and_acquisition_time(tmp_path, sample_dataset, source):
    first = publish(tmp_path, sample_dataset, source)
    path = directory(tmp_path, first)
    original = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in path.iterdir()}
    second_input = changed_dataset(
        sample_dataset, data=sample_dataset.data.iloc[::-1].reset_index(drop=True),
        generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc), provider="working-store",
    )
    second = publish(tmp_path, second_input, source)
    assert first == second  # reuse original creation time as well as identity
    assert original == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in path.iterdir()}
    assert len(list((tmp_path / "snapshots").iterdir())) == 1


def test_versions_stable_across_roots(tmp_path, sample_dataset, source):
    left = publish(tmp_path / "left", sample_dataset, source)
    right = publish(tmp_path / "right", sample_dataset, source)
    assert left.data_version == right.data_version
    assert left.files == right.files


@pytest.mark.parametrize("change", ["price", "source_name", "source_version", "metadata", "adjustment", "coverage"])
def test_identity_changes_with_content_or_contract(tmp_path, sample_dataset, source, change):
    original = publish(tmp_path, sample_dataset, source)
    kwargs = {}
    if change == "price":
        frame = sample_dataset.data.copy()
        frame.loc[0, "close"] = frame.loc[0, "open"]
        sample_dataset = changed_dataset(sample_dataset, data=frame)
    elif change == "adjustment":
        sample_dataset = changed_dataset(sample_dataset, adjustment=AdjustmentMode.FORWARD)
    elif change == "coverage":
        kwargs["start_date"] = date(2026, 8, 31)
    else:
        field = {"source_name": "provider_name", "source_version": "provider_version", "metadata": "metadata"}[change]
        source = SourceMetadata(**(source.model_dump() | {field: {"unit": "lots"} if field == "metadata" else "different"}))
    updated = publish(tmp_path, sample_dataset, source, **kwargs)
    assert original.data_version != updated.data_version
    SnapshotReader(tmp_path).read(original.data_version)
    SnapshotReader(tmp_path).read(updated.data_version)


@pytest.mark.parametrize("start,end", [(END, START), ("2026-09-01", END), (datetime(2026, 9, 1), END), (START, None)])
def test_invalid_coverage_rejected_without_writes(tmp_path, sample_dataset, source, start, end):
    with pytest.raises(SnapshotError, match="dates"):
        publish(tmp_path, sample_dataset, source, start_date=start, end_date=end)
    assert list(tmp_path.iterdir()) == []


def test_all_bars_must_be_inside_inclusive_range(tmp_path, sample_dataset, source):
    with pytest.raises(SnapshotError, match="outside declared"):
        publish(tmp_path, sample_dataset, source, start_date=date(2026, 9, 2))
    # Wider dates are allowed; weekends/holidays need not contain rows.
    manifest = publish(tmp_path, sample_dataset, source, start_date=date(2026, 8, 30), end_date=date(2026, 9, 6))
    assert len(SnapshotReader(tmp_path).read(manifest.data_version).dataset.data) == 5


def test_revalidate_mutated_dataframe_and_source(tmp_path, sample_dataset, source):
    sample_dataset.data.loc[0, "volume"] = -1
    with pytest.raises(SnapshotError, match="volume"):
        publish(tmp_path, sample_dataset, source)
    sample_dataset.data.loc[0, "volume"] = 1
    source.metadata["bad"] = object()
    with pytest.raises(SnapshotError, match="JSON"):
        publish(tmp_path, sample_dataset, source)
    assert list(tmp_path.iterdir()) == []


def test_large_integer_volume_not_silently_converted_to_float(tmp_path, sample_dataset, source):
    frame = sample_dataset.data.copy()
    frame["volume"] = 2**53 + 1
    dataset = changed_dataset(sample_dataset, data=frame)
    manifest = publish(tmp_path, dataset, source)
    result = SnapshotReader(tmp_path).read(manifest.data_version)
    assert result.dataset.data.volume.tolist() == [2**53 + 1] * len(frame)


def test_returned_objects_and_original_source_cannot_mutate_disk(tmp_path, sample_dataset, source):
    manifest = publish(tmp_path, sample_dataset, source)
    result = SnapshotReader(tmp_path).read(manifest.data_version)
    source.metadata["unit"] = "changed"
    result.manifest.source.metadata["unit"] = "changed"
    result.dataset.data.loc[0, "volume"] = -1
    again = SnapshotReader(tmp_path).read(manifest.data_version)
    assert again.manifest.source.metadata["unit"] == "shares"
    pd.testing.assert_frame_equal(again.dataset.data, sample_dataset.data)


@pytest.mark.parametrize("target", ["manifest.json", "bars.parquet"])
def test_missing_files_are_errors(tmp_path, sample_dataset, source, target):
    manifest = publish(tmp_path, sample_dataset, source)
    (directory(tmp_path, manifest) / target).unlink()
    with pytest.raises(SnapshotError):
        SnapshotReader(tmp_path).read(manifest.data_version)


@pytest.mark.parametrize("kind", ["json", "identity", "parquet", "extra"])
def test_tampering_detected(tmp_path, sample_dataset, source, kind):
    manifest = publish(tmp_path, sample_dataset, source)
    path = directory(tmp_path, manifest)
    if kind == "json":
        (path / "manifest.json").write_text("{bad", encoding="utf-8")
    elif kind == "identity":
        data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        data["row_count"] += 1
        (path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    elif kind == "parquet":
        with (path / "bars.parquet").open("ab") as stream:
            stream.write(b"tamper")
    else:
        (path / "unlisted.parquet").write_bytes(b"unexpected")
    with pytest.raises(SnapshotError):
        SnapshotReader(tmp_path).read(manifest.data_version)


def test_wrong_version_directory_rejected(tmp_path, sample_dataset, source):
    manifest = publish(tmp_path, sample_dataset, source)
    wrong = "sha256:" + "0" * 64
    directory(tmp_path, manifest).rename(tmp_path / "snapshots" / snapshot_directory_name(wrong))
    with pytest.raises(SnapshotError, match="requested version"):
        SnapshotReader(tmp_path).read(wrong)


@pytest.mark.parametrize("relative", ["../bars.parquet", "/bars.parquet", "C:/bars.parquet", "bars.parquet:stream", "a\\b", "a/../b"])
def test_invalid_manifest_paths_rejected_before_open(tmp_path, sample_dataset, source, relative):
    manifest = publish(tmp_path, sample_dataset, source)
    path = directory(tmp_path, manifest) / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["files"][0]["relative_path"] = relative
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SnapshotError, match="relative_path"):
        SnapshotReader(tmp_path).read(manifest.data_version)


def test_unexpected_but_valid_relative_path_rejected(tmp_path, sample_dataset, source):
    manifest = publish(tmp_path, sample_dataset, source)
    entry = manifest.files[0].model_dump() | {"relative_path": "nested/bars.parquet"}
    updated = rewrite_manifest(tmp_path, manifest, files=[entry])
    with pytest.raises(SnapshotError, match="exactly one"):
        SnapshotReader(tmp_path).read(updated.data_version)


@pytest.mark.parametrize("kind,match", [
    ("file_count", "file row_count"), ("total_count", "total row_count"),
    ("instruments", "instruments"), ("dates", "date range"), ("adjustment", "metadata mismatch"),
])
def test_manifest_semantics_fail_even_with_valid_hashes(tmp_path, sample_dataset, source, kind, match):
    manifest = publish(tmp_path, sample_dataset, source)
    changes = {
        "file_count": {"files": [manifest.files[0].model_dump() | {"row_count": 6}]},
        "total_count": {"row_count": 6},
        "instruments": {"instruments": ["CN:XSHG:999999"]},
        "dates": {"start_date": END, "end_date": END},
        "adjustment": {"adjustment": "FORWARD"},
    }[kind]
    updated = rewrite_manifest(tmp_path, manifest, **changes)
    updated.verify_data_version()
    assert sha256_file(directory(tmp_path, updated) / "bars.parquet") == updated.files[0].sha256
    with pytest.raises(SnapshotError, match=match):
        SnapshotReader(tmp_path).read(updated.data_version)


@pytest.mark.parametrize("kind,match", [
    ("frequency", "aquant.frequency"), ("adjustment", "aquant.adjustment"),
    ("format", "aquant.snapshot.format"), ("metadata_missing", "metadata mismatch"),
    ("column_missing", "HISTORY_COLUMNS"), ("extra_column", "HISTORY_COLUMNS"),
    ("numeric_type", "real numeric"), ("date_type", "string/date32"),
    ("key_type", "string/date32"), ("null", "nulls"),
    ("volume", "volume"), ("ohlc", "high"), ("duplicate", "duplicate"),
    ("infinite", "finite"), ("invalid_key", "instrument_key"),
])
def test_parquet_semantics_fail_even_after_rehash(tmp_path, sample_dataset, source, kind, match):
    manifest = publish(tmp_path, sample_dataset, source)

    def transform(table):
        if kind in {"frequency", "adjustment", "format", "metadata_missing"}:
            key, value = {
                "frequency": (b"aquant.frequency", b"WEEKLY"),
                "adjustment": (b"aquant.adjustment", b"BACKWARD"),
                "format": (b"aquant.snapshot.format", b"99"),
                "metadata_missing": (b"aquant.frequency", None),
            }[kind]
            metadata = dict(table.schema.metadata)
            if value is None:
                metadata.pop(key)
            else:
                metadata[key] = value
            return table.replace_schema_metadata(metadata)
        if kind == "column_missing":
            return table.drop(["volume"])
        if kind == "extra_column":
            return table.append_column("provider_extra", pa.array([1] * table.num_rows))
        name, values = {
            "numeric_type": ("volume", ["1"] * table.num_rows),
            "date_type": ("trade_date", ["2026-09-01"] * table.num_rows),
            "key_type": ("instrument_key", [1] * table.num_rows),
            "null": ("volume", [None] + [1] * (table.num_rows - 1)),
            "volume": ("volume", [-1] * table.num_rows),
            "ohlc": ("high", [-100.0] * table.num_rows),
            "infinite": ("close", [float("inf")] * table.num_rows),
            "invalid_key": ("instrument_key", ["legacy"] * table.num_rows),
            "duplicate": ("trade_date", [START] * table.num_rows),
        }[kind]
        return table.set_column(table.schema.get_field_index(name), name, pa.array(values))

    updated = rewrite_table(tmp_path, manifest, transform)
    updated.verify_data_version()
    with pytest.raises(SnapshotError, match=match):
        SnapshotReader(tmp_path).read(updated.data_version)


def test_empty_bars_do_not_hide_declared_members_or_counts(tmp_path, sample_dataset, source):
    manifest = publish(tmp_path, changed_dataset(sample_dataset, data=empty_history_frame()), source)
    updated = rewrite_manifest(tmp_path, manifest, instruments=["CN:XSHG:510300"])
    with pytest.raises(SnapshotError, match="instruments"):
        SnapshotReader(tmp_path).read(updated.data_version)


@pytest.mark.parametrize("stage", ["write", "verify", "rename"])
def test_publication_failure_never_exposes_readable_partial(tmp_path, sample_dataset, source, monkeypatch, stage):
    if stage == "write":
        def fail(table, path, **kwargs):
            Path(path).write_bytes(b"partial")
            raise OSError("injected write failure")
        monkeypatch.setattr(snapshots.pq, "write_table", fail)
    elif stage == "verify":
        monkeypatch.setattr(snapshots, "_verify", lambda *a: (_ for _ in ()).throw(SnapshotError("injected verify failure")))
    else:
        monkeypatch.setattr(snapshots.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("injected rename failure")))
    with pytest.raises(SnapshotError, match="injected"):
        publish(tmp_path, sample_dataset, source)
    assert not list((tmp_path / "snapshots").glob("*"))
    assert not list((tmp_path / ".staging").glob("*"))
    assert not list((tmp_path / ".publish-locks").glob("*"))


@pytest.mark.parametrize("target_state", ["corrupt", "empty", "file"])
def test_existing_target_never_repaired_or_overwritten(tmp_path, sample_dataset, source, target_state):
    manifest = publish(tmp_path, sample_dataset, source)
    target = directory(tmp_path, manifest)
    if target_state == "corrupt":
        (target / "bars.parquet").write_bytes(b"damaged")
    else:
        for path in target.iterdir():
            path.unlink()
        if target_state == "file":
            target.rmdir()
            target.write_bytes(b"reserved")
    before = target.read_bytes() if target.is_file() else {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(SnapshotError):
        publish(tmp_path, sample_dataset, source)
    after = target.read_bytes() if target.is_file() else {p.name: p.read_bytes() for p in target.iterdir()}
    assert after == before


def test_staging_is_never_read_by_version(tmp_path, sample_dataset, source):
    manifest = publish(tmp_path, sample_dataset, source)
    directory(tmp_path, manifest).rename(tmp_path / ".staging" / "abandoned")
    with pytest.raises(SnapshotError, match="missing"):
        SnapshotReader(tmp_path).read(manifest.data_version)


def test_concurrent_publishers_cannot_overwrite(tmp_path, sample_dataset, source, monkeypatch):
    entered, release = Event(), Event()
    real_rename = snapshots.os.rename

    def pause_rename(src, dst):
        entered.set()
        assert release.wait(10)
        real_rename(src, dst)

    monkeypatch.setattr(snapshots.os, "rename", pause_rename)
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(publish, tmp_path, sample_dataset, source)
        try:
            assert entered.wait(10)
            with pytest.raises(SnapshotConflictError, match="lock"):
                publish(tmp_path, sample_dataset, source)
            assert list((tmp_path / "snapshots").iterdir()) == []
        finally:
            release.set()
        manifest = winner.result(timeout=10)
    assert publish(tmp_path, sample_dataset, source) == manifest
    pd.testing.assert_frame_equal(SnapshotReader(tmp_path).read(manifest.data_version).dataset.data, sample_dataset.data)


def test_abandoned_lock_fails_closed(tmp_path, sample_dataset, source):
    manifest = publish(tmp_path, sample_dataset, source)
    lock = tmp_path / ".publish-locks" / (snapshot_directory_name(manifest.data_version) + ".lock")
    lock.write_bytes(b"abandoned")
    with pytest.raises(SnapshotConflictError):
        publish(tmp_path, sample_dataset, source)
    assert lock.read_bytes() == b"abandoned"
    SnapshotReader(tmp_path).read(manifest.data_version)


def test_mutable_store_update_and_delete_cannot_change_snapshot(tmp_path, sample_dataset, source, sample_instruments):
    working = tmp_path / "working"
    root = tmp_path / "immutable"
    store = ParquetHistoryStore(working)
    store.write(sample_dataset)
    request = HistoryRequest(instruments=sample_instruments, start_date=START, end_date=END)
    loaded = store.read(request)
    manifest = publish(root, loaded, source)
    frame = sample_dataset.data.copy()
    frame["volume"] += 999
    store.write(changed_dataset(sample_dataset, data=frame))
    assert not store.read(request).data.equals(loaded.data)
    for path in working.rglob("*.parquet"):
        path.unlink()
    pd.testing.assert_frame_equal(SnapshotReader(root).read(manifest.data_version).dataset.data, loaded.data)


def test_hardlinked_bars_are_rejected(tmp_path, sample_dataset, source):
    manifest = publish(tmp_path, sample_dataset, source)
    original = directory(tmp_path, manifest) / "bars.parquet"
    os.link(original, tmp_path / "shared.parquet")
    with pytest.raises(SnapshotError, match="independent"):
        SnapshotReader(tmp_path).read(manifest.data_version)


def test_reparse_guard_without_os_privileges(tmp_path, monkeypatch):
    # Portable unit check: Windows symlink creation often requires privileges.
    from types import SimpleNamespace
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400))
    with pytest.raises(SnapshotError, match="reparse"):
        snapshots._reject_link(tmp_path)


def test_hashed_buffer_is_the_buffer_decoded(tmp_path, sample_dataset, source, monkeypatch):
    manifest = publish(tmp_path, sample_dataset, source)
    read = snapshots._read_bytes

    def replace_after_read(path):
        payload = read(path)
        if path.name == "bars.parquet":
            path.write_bytes(b"later corruption")
        return payload

    monkeypatch.setattr(snapshots, "_read_bytes", replace_after_read)
    result = SnapshotReader(tmp_path).read(manifest.data_version)
    pd.testing.assert_frame_equal(result.dataset.data, sample_dataset.data)
    with pytest.raises(SnapshotError, match="SHA256"):
        SnapshotReader(tmp_path).read(manifest.data_version)


@pytest.mark.parametrize("location", ["root", "snapshots", ".staging", ".publish-locks"])
def test_real_directory_links_cannot_redirect_snapshot_io(tmp_path, sample_dataset, source, location):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root if location == "root" else root / location
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(SnapshotError, match="links/reparse"):
            publish(root, sample_dataset, source)
        if location in {"root", "snapshots"}:
            with pytest.raises(SnapshotError, match="links/reparse"):
                SnapshotReader(root).read("sha256:" + "0" * 64)
        assert list(outside.iterdir()) == []
    finally:
        # Remove only the directory link, never recurse into its destination.
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_invalid_parquet_with_matching_hash_is_not_empty_success(tmp_path, sample_dataset, source):
    manifest = publish(tmp_path, sample_dataset, source)
    bars = directory(tmp_path, manifest) / "bars.parquet"
    bars.write_bytes(b"not a parquet file")
    updated = rewrite_manifest(tmp_path, manifest, files=[
        manifest.files[0].model_dump() | {"sha256": sha256_file(bars)},
    ])
    with pytest.raises(SnapshotError, match="snapshot read failed"):
        SnapshotReader(tmp_path).read(updated.data_version)


@pytest.mark.parametrize("field,value", [("schema_version", "2"), ("frequency", "WEEKLY")])
def test_unsupported_manifest_contract_is_rejected(tmp_path, sample_dataset, source, field, value):
    manifest = publish(tmp_path, sample_dataset, source)
    path = directory(tmp_path, manifest) / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SnapshotError, match=field):
        SnapshotReader(tmp_path).read(manifest.data_version)


def test_publish_failure_leaves_other_published_versions_intact(tmp_path, sample_dataset, source, monkeypatch):
    original = publish(tmp_path, sample_dataset, source)
    before = {p.name: p.read_bytes() for p in directory(tmp_path, original).iterdir()}
    changed = SourceMetadata(provider_name="fixture", provider_version="new")
    monkeypatch.setattr(snapshots.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("injected publication failure")))
    with pytest.raises(SnapshotError, match="injected"):
        publish(tmp_path, sample_dataset, changed)
    assert before == {p.name: p.read_bytes() for p in directory(tmp_path, original).iterdir()}
    SnapshotReader(tmp_path).read(original.data_version)
