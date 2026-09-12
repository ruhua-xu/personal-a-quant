from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from aquant.data.versioning import DatasetFileEntry, DatasetManifest, SourceMetadata
from aquant.domain import StrategyRun, StrategyRunStatus


CREATED_AT = datetime(2026, 9, 4, tzinfo=timezone.utc)


def identity_fields(**overrides):
    fields = dict(
        schema_version="1", frequency="DAILY", adjustment="RAW",
        instruments=["US:XNAS:AAPL", "CN:XSHG:510300"],
        start_date="2026-09-01", end_date="2026-09-03", row_count=3,
        files=[
            dict(relative_path="bars/b/2026.parquet", sha256="b" * 64, row_count=1),
            dict(relative_path="bars/a/2026.parquet", sha256="a" * 64, row_count=2),
        ],
        source=dict(provider_name="fixture", provider_version="1", metadata={
            "volume_semantics": "source units", "nested": {"b": 2, "a": [True, None, "数据"]},
        }),
    )
    fields.update(overrides)
    return fields


def make_manifest(**overrides):
    return DatasetManifest.create(created_at=CREATED_AT, **identity_fields(**overrides))


def test_create_normalizes_schema_and_derives_verified_version():
    manifest = make_manifest(instruments=["US:XNAS:AAPL", "CN:XSHG:510300", "US:XNAS:AAPL"])
    assert manifest.schema_version == "1"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", manifest.data_version)
    assert manifest.instruments == ("CN:XSHG:510300", "US:XNAS:AAPL")
    assert [entry.relative_path for entry in manifest.files] == ["bars/a/2026.parquet", "bars/b/2026.parquet"]
    assert manifest.start_date == date(2026, 9, 1)
    assert manifest.end_date == date(2026, 9, 3)
    assert manifest.row_count == 3
    assert manifest.verify_data_version() is None
    assert set(manifest.identity_payload()) == {
        "schema_version", "frequency", "adjustment", "instruments", "start_date",
        "end_date", "row_count", "files", "source",
    }


def test_source_names_strip_whitespace_and_allow_generic_providers():
    source = SourceMetadata(provider_name="  offline-fixture  ", provider_version=" v1 ")
    assert source.provider_name == "offline-fixture"
    assert source.provider_version == "v1"
    assert source.metadata == {}


@pytest.mark.parametrize("field", ["provider_name", "provider_version"])
@pytest.mark.parametrize("value", ["", "  ", None, 1, b"name"])
def test_source_requires_nonempty_text(field, value):
    values = dict(provider_name="fixture", provider_version="1")
    values[field] = value
    with pytest.raises(ValidationError):
        SourceMetadata(**values)


@pytest.mark.parametrize("value", [
    object(), b"bytes", bytearray(b"bytes"), {1, 2}, frozenset({1}), (1, 2),
    CREATED_AT, date(2026, 9, 1), Decimal("0.1"), {1: "non-string key"},
    float("nan"), float("inf"), float("-inf"),
])
def test_non_json_metadata_is_rejected_at_any_depth(value):
    with pytest.raises(ValidationError):
        SourceMetadata(provider_name="fixture", provider_version="1", metadata={"nested": [value]})


@pytest.mark.parametrize("metadata", [None, [], "{}", b"{}"])
def test_metadata_root_must_be_a_json_object(metadata):
    with pytest.raises(ValidationError, match="JSON object"):
        SourceMetadata(provider_name="fixture", provider_version="1", metadata=metadata)


def test_json_metadata_preserves_explicitly_serialized_time_and_primitive_types():
    metadata = {"timestamp": CREATED_AT.isoformat(), "data": [None, True, False, 3, 0.25, "中文"]}
    source = SourceMetadata(provider_name="fixture", provider_version="1", metadata=metadata)
    assert source.metadata == metadata
    assert json.loads(source.model_dump_json())["metadata"] == metadata


def test_circular_metadata_is_rejected():
    metadata = {}
    metadata["self"] = metadata
    with pytest.raises(ValidationError, match="circular"):
        SourceMetadata(provider_name="fixture", provider_version="1", metadata=metadata)


@pytest.mark.parametrize("path", [
    "", "/bars/a.parquet", "//server/share/a.parquet", "../a.parquet", "bars/../a.parquet",
    "C:/bars/a.parquet", "C:a.parquet", r"C:\bars\a.parquet", r"bars\a.parquet",
    "bars//a.parquet", "./bars/a.parquet", "bars/./a.parquet", "bars/", ".", "..",
    "bars/a\x00.parquet", "bars/a\n.parquet", "https://example.invalid/a.parquet",
])
def test_unsafe_or_noncanonical_paths_are_rejected(path):
    with pytest.raises(ValidationError, match="relative_path"):
        DatasetFileEntry(relative_path=path, sha256="a" * 64, row_count=1)


def test_relative_posix_path_does_not_need_to_exist():
    entry = DatasetFileEntry(relative_path="bars/研究/a..b.parquet", sha256="a" * 64, row_count=0)
    assert entry.relative_path == "bars/研究/a..b.parquet"


@pytest.mark.parametrize("digest", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 64 + "\n", "sha256:" + "a" * 64])
def test_invalid_file_sha256_is_rejected(digest):
    with pytest.raises(ValidationError, match="sha256"):
        DatasetFileEntry(relative_path="bars/a.parquet", sha256=digest, row_count=1)


@pytest.mark.parametrize("count", [-1, 1.5, "3", True])
def test_row_counts_are_nonnegative_integers(count):
    with pytest.raises(ValidationError, match="row_count"):
        DatasetFileEntry(relative_path="bars/a.parquet", sha256="a" * 64, row_count=count)
    with pytest.raises(ValidationError, match="row_count"):
        make_manifest(row_count=count)


def test_duplicate_file_paths_are_rejected_even_if_contents_differ():
    files = identity_fields()["files"]
    files[1]["relative_path"] = files[0]["relative_path"]
    with pytest.raises(ValidationError, match="duplicate file"):
        make_manifest(files=files)


@pytest.mark.parametrize("key", ["510300", "sh510300", "CN:XSHG:", "CN:XSHG: 510300", "ZZ:XSHG:510300", "CN:UNKNOWN:510300", b"CN:XSHG:510300"])
def test_instrument_keys_must_be_canonical(key):
    with pytest.raises(ValidationError):
        make_manifest(instruments=[key])


def test_instrument_identity_does_not_collapse_markets():
    manifest = make_manifest(instruments=["US:XNAS:510300", "CN:XSHG:510300"])
    assert len(manifest.instruments) == 2


def test_date_range_must_be_valid():
    with pytest.raises(ValidationError, match="start_date"):
        make_manifest(start_date="2026-09-04")


@pytest.mark.parametrize("version", ["2", 1, "", None])
def test_schema_version_is_fixed(version):
    with pytest.raises(ValidationError, match="schema_version"):
        make_manifest(schema_version=version)


@pytest.mark.parametrize("created_at", [CREATED_AT, datetime(2026, 9, 4, tzinfo=ZoneInfo("Asia/Shanghai"))])
def test_aware_creation_times_are_accepted(created_at):
    manifest = DatasetManifest.create(created_at=created_at, **identity_fields())
    assert manifest.created_at == created_at


def test_naive_creation_time_is_rejected():
    with pytest.raises(ValidationError, match="timezone"):
        DatasetManifest.create(created_at=datetime(2026, 9, 4), **identity_fields())


def test_builder_does_not_accept_caller_supplied_version():
    with pytest.raises(ValidationError, match="data_version"):
        make_manifest(data_version="sha256:" + "0" * 64)


def test_json_round_trip_verifies_supplied_identity():
    manifest = make_manifest()
    assert DatasetManifest.model_validate_json(manifest.model_dump_json()) == manifest
    values = manifest.model_dump(mode="json")
    values["source"]["provider_version"] = "2"
    with pytest.raises(ValidationError, match="data_version mismatch"):
        DatasetManifest.model_validate_json(json.dumps(values))


def test_arbitrary_data_version_is_rejected_on_direct_construction():
    with pytest.raises(ValidationError, match="data_version mismatch"):
        DatasetManifest(**identity_fields(), created_at=CREATED_AT, data_version="sha256:" + "0" * 64)


def test_verify_detects_identity_changed_via_unvalidated_model_copy():
    manifest = make_manifest().model_copy(update={"row_count": 99})
    with pytest.raises(ValueError, match="data_version mismatch"):
        manifest.verify_data_version()


def test_verify_detects_nested_metadata_tampering():
    manifest = make_manifest()
    manifest.source.metadata["nested"]["a"].append("changed")
    with pytest.raises(ValueError, match="data_version mismatch"):
        manifest.verify_data_version()


def test_verify_rejects_non_json_metadata_injected_after_creation():
    manifest = make_manifest()
    manifest.source.metadata["invalid"] = object()
    with pytest.raises(ValueError, match="JSON-compatible"):
        manifest.verify_data_version()


def test_source_and_manifest_detach_mutable_caller_inputs():
    fields = identity_fields()
    source = SourceMetadata(**fields["source"])
    manifest = make_manifest(source=source)
    expected = manifest.identity_payload()
    fields["source"]["metadata"]["nested"]["a"].append("caller change")
    source.metadata["nested"]["a"].append("source change")
    assert manifest.identity_payload() == expected
    manifest.identity_payload()["source"]["metadata"]["nested"]["a"].append("payload change")
    manifest.verify_data_version()


def test_verify_never_opens_declared_files(monkeypatch):
    manifest = make_manifest()

    def forbid_file_read(*args, **kwargs):
        raise AssertionError("identity verification must not read files")

    monkeypatch.setattr(Path, "open", forbid_file_read)
    manifest.verify_data_version()


def test_empty_dataset_manifest_is_valid():
    manifest = make_manifest(row_count=0, files=[], instruments=[])
    assert manifest.files == manifest.instruments == ()
    manifest.verify_data_version()


def test_strategy_run_can_reference_manifest_without_model_changes():
    manifest = make_manifest()
    run = StrategyRun(
        run_id="fixture-run", strategy_key="fixture", strategy_version="1",
        status=StrategyRunStatus.CREATED, as_of=CREATED_AT, created_at=CREATED_AT,
        data_version=manifest.data_version, parameters={},
    )
    assert run.data_version == manifest.data_version
