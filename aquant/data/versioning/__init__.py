"""Dataset manifest contracts and stable hashes; no snapshot reader/writer."""

from .hashing import canonical_json_bytes, compute_data_version, sha256_file
from .manifest import DatasetFileEntry, DatasetManifest, SourceMetadata

__all__ = [
    "DatasetFileEntry", "DatasetManifest", "SourceMetadata",
    "canonical_json_bytes", "compute_data_version", "sha256_file",
]
