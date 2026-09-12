"""Deterministic JSON and streaming file hashes; no snapshot persistence."""

from hashlib import sha256
import json
from math import isfinite
from pathlib import Path


def _validate_json(value: object, active: set[int]) -> None:
    """Reject coercion of non-JSON Python values, including nested values."""
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if type(value) not in (dict, list):
        raise ValueError("only JSON-compatible dict/list/string/number/bool/null values are allowed")
    identity = id(value)
    if identity in active:
        raise ValueError("circular values are not JSON-compatible")
    active.add(identity)
    try:
        if type(value) is dict:
            for key, child in value.items():
                if type(key) is not str:
                    raise ValueError("JSON object keys must be strings")
                _validate_json(child, active)
        else:
            for child in value:
                _validate_json(child, active)
    finally:
        active.remove(identity)


def canonical_json_bytes(value: object) -> bytes:
    """Strict JSON -> UTF-8, sorted keys, compact separators, literal Unicode.

    No datetime/Decimal/object coercion, non-finite numbers or circular values.
    List order and numeric types are significant; this is not RFC 8785/JCS.
    Domain-specific list normalization belongs to the manifest identity schema.
    """
    _validate_json(value, set())
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def compute_data_version(identity: dict[str, object]) -> str:
    """Validate/normalize identity fields, then address their canonical bytes.

    Accepts identity fields only: created_at, data_version and location fields
    are rejected. Instruments are deduplicated/sorted and files are sorted by
    relative_path using the same schema as DatasetManifest.create().
    """
    # Delayed import avoids a module cycle; the identity model itself does not
    # compute a version. All hash entry points share its validation rules.
    from .manifest import _ManifestIdentity

    payload = _ManifestIdentity.model_validate(identity).model_dump(mode="json")
    return "sha256:" + sha256(canonical_json_bytes(payload)).hexdigest()


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash file bytes in bounded chunks; its location is not part of the hash.

    This does not freeze the file or detect concurrent writers. Missing files
    and read errors propagate to the caller, never producing a fabricated hash.
    """
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    digest = sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
