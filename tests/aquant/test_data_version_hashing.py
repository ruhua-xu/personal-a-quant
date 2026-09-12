from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from aquant.data.versioning import (
    DatasetManifest, canonical_json_bytes, compute_data_version, sha256_file,
)
from tests.aquant.test_dataset_manifest import CREATED_AT, identity_fields, make_manifest


def test_canonical_json_has_fixed_utf8_encoding_keys_and_separators():
    expected = '{"a":[true,null],"z":"行情"}'.encode("utf-8")
    assert canonical_json_bytes({"z": "行情", "a": [True, None]}) == expected
    assert canonical_json_bytes({"a": [True, None], "z": "行情"}) == expected


def test_canonical_json_is_recursive_but_preserves_list_and_numeric_types():
    assert canonical_json_bytes({"b": {"z": 1, "a": 2}}) == canonical_json_bytes({"b": {"a": 2, "z": 1}})
    assert canonical_json_bytes([1, 2]) != canonical_json_bytes([2, 1])
    assert canonical_json_bytes(1) != canonical_json_bytes(1.0)
    assert canonical_json_bytes(0.0) != canonical_json_bytes(-0.0)


@pytest.mark.parametrize("value", [b"bytes", {1, 2}, (1, 2), CREATED_AT, {1: "key"}, float("nan"), float("inf"), object()])
def test_canonical_json_rejects_non_json_input(value):
    with pytest.raises(ValueError):
        canonical_json_bytes({"nested": [value]})


def test_shared_json_subtree_is_allowed_but_cycles_are_not():
    shared = {"value": 1}
    assert canonical_json_bytes([shared, shared]) == b'[{"value":1},{"value":1}]'
    shared["self"] = shared
    with pytest.raises(ValueError, match="circular"):
        canonical_json_bytes(shared)


def test_version_is_sha256_of_canonical_identity_and_deterministic():
    manifest = make_manifest()
    assert manifest.data_version == "sha256:" + sha256(canonical_json_bytes(manifest.identity_payload())).hexdigest()
    assert compute_data_version(identity_fields()) == manifest.data_version
    assert make_manifest().data_version == manifest.data_version


def test_schema_one_identity_has_a_fixed_golden_version():
    # Deliberate wire-contract vector: serializer/normalization changes must
    # not silently assign a new identity to the same schema-1 manifest.
    assert make_manifest().data_version == (
        "sha256:4a18e94dbe934169aa785587b38f0ce2f48053999b852c85e944ed3c3ffa9f66"
    )


def test_different_creation_times_do_not_change_version():
    first = make_manifest()
    second = DatasetManifest.create(created_at=datetime(2030, 1, 1, tzinfo=timezone.utc), **identity_fields())
    assert first.created_at != second.created_at
    assert first.data_version == second.data_version
    assert "created_at" not in first.identity_payload()
    assert "data_version" not in first.identity_payload()


def test_reordered_instruments_and_duplicates_do_not_change_version():
    fields = identity_fields()
    fields["instruments"] = list(reversed(fields["instruments"])) + [fields["instruments"][0]]
    assert compute_data_version(fields) == make_manifest().data_version


def test_reordered_files_and_file_dict_keys_do_not_change_version():
    fields = identity_fields()
    fields["files"] = [dict(reversed(list(entry.items()))) for entry in reversed(fields["files"])]
    assert compute_data_version(fields) == make_manifest().data_version


def test_reordered_nested_source_metadata_and_payload_keys_do_not_change_version():
    fields = identity_fields()
    fields["source"]["metadata"] = {
        "nested": {"a": [True, None, "数据"], "b": 2}, "volume_semantics": "source units",
    }
    fields["source"] = dict(reversed(list(fields["source"].items())))
    assert compute_data_version(dict(reversed(list(fields.items())))) == make_manifest().data_version


@pytest.mark.parametrize("change", [
    "file_sha", "file_count", "relative_path", "total_count", "provider_version",
    "provider_name", "metadata", "adjustment", "instruments", "start_date", "end_date",
])
def test_each_identity_change_changes_version(change):
    fields = identity_fields()
    if change == "file_sha":
        fields["files"][0]["sha256"] = "c" * 64
    elif change == "file_count":
        fields["files"][0]["row_count"] = 9
    elif change == "relative_path":
        fields["files"][0]["relative_path"] = "bars/c/2026.parquet"
    elif change == "total_count":
        fields["row_count"] = 99
    elif change == "provider_version":
        fields["source"]["provider_version"] = "2"
    elif change == "provider_name":
        fields["source"]["provider_name"] = "other"
    elif change == "metadata":
        fields["source"]["metadata"]["volume_semantics"] = "different units"
    elif change == "adjustment":
        fields["adjustment"] = "FORWARD"
    elif change == "instruments":
        fields["instruments"] = ["CN:XSHG:510300"]
    elif change == "start_date":
        fields["start_date"] = "2026-08-31"
    else:
        fields["end_date"] = "2026-09-04"
    assert compute_data_version(fields) != make_manifest().data_version


@pytest.mark.parametrize("extra", ["created_at", "data_version", "absolute_path", "root_path", "username", "hostname", "uuid"])
def test_non_identity_fields_are_not_accepted_by_hash_entry_point(extra):
    fields = identity_fields()
    fields[extra] = "not part of identity"
    with pytest.raises(ValueError):
        compute_data_version(fields)


def test_different_absolute_roots_do_not_affect_file_hash_or_version(tmp_path):
    versions = []
    for directory in ("first-root", "another-root"):
        root = tmp_path / directory
        path = root / "bars" / "2026.parquet"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"identical file bytes")
        manifest = make_manifest(files=[dict(
            relative_path=path.relative_to(root).as_posix(), sha256=sha256_file(path), row_count=3,
        )])
        assert str(root) not in manifest.model_dump_json()
        versions.append(manifest.data_version)
    assert versions[0] == versions[1]


@pytest.mark.parametrize("contents,expected", [
    (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
])
def test_file_sha256_known_vectors(tmp_path, contents, expected):
    path = tmp_path / "fixture.bin"
    path.write_bytes(contents)
    assert sha256_file(path) == expected


def test_file_hash_reads_in_bounded_chunks(tmp_path, monkeypatch):
    path = tmp_path / "large.bin"
    contents = b"0123456789abcdef" * (128 * 1024 + 1)
    path.write_bytes(contents)
    original_open = Path.open
    read_sizes = []

    class TrackedFile:
        def __enter__(self):
            self.stream = original_open(path, "rb")
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            read_sizes.append(size)
            return self.stream.read(size)

    def open_tracked(actual, *args, **kwargs):
        assert actual == path
        assert args == ("rb",)
        return TrackedFile()

    monkeypatch.setattr(Path, "open", open_tracked)
    assert sha256_file(path) == sha256(contents).hexdigest()
    assert len(read_sizes) >= 4


@pytest.mark.parametrize("size", [1, 7, 1024])
def test_chunk_size_does_not_change_hash(tmp_path, size):
    path = tmp_path / "fixture.bin"
    path.write_bytes(b"abc" * 100)
    assert sha256_file(path, chunk_size=size) == sha256(b"abc" * 100).hexdigest()


@pytest.mark.parametrize("size", [0, -1, True, 1.5])
def test_invalid_chunk_sizes_rejected(tmp_path, size):
    with pytest.raises(ValueError, match="chunk_size"):
        sha256_file(tmp_path / "not-opened", chunk_size=size)


def test_file_read_errors_propagate(tmp_path):
    with pytest.raises(FileNotFoundError):
        sha256_file(tmp_path / "missing.bin")
