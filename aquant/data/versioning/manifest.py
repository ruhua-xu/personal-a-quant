"""Dataset identity/provenance contracts, not immutable historical snapshots."""

from datetime import date, datetime
import json
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, StrictStr,
    StringConstraints, field_validator, model_validator,
)

from aquant.data.enums import AdjustmentMode, BarFrequency
from aquant.domain import Exchange, Market

from .hashing import canonical_json_bytes, compute_data_version


NonEmptyText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
RowCount = Annotated[int, Field(strict=True, ge=0)]
FileSHA256 = Annotated[str, StringConstraints(strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
DataVersion = Annotated[str, StringConstraints(strict=True, min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$")]


class SourceMetadata(BaseModel):
    """Explicit provider provenance; nested metadata is strict JSON data.

    Inputs are detached copies. Metadata remains an ordinary JSON dict/list;
    callers editing it later must verify/rebuild the containing manifest.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    provider_name: NonEmptyText
    provider_version: NonEmptyText
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> dict[str, JsonValue]:
        if type(value) is not dict:
            raise ValueError("source metadata must be a JSON object")
        # Validate before Pydantic can coerce keys, tuples, bytes or datetimes.
        # The round trip also detaches all nested lists/dicts from caller input.
        return json.loads(canonical_json_bytes(value))


class DatasetFileEntry(BaseModel):
    """Declared content hash/count at a portable, normalized relative path."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    relative_path: StrictStr
    sha256: FileSHA256
    row_count: RowCount

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if (
            not value or PurePosixPath(value).is_absolute()
            or "\\" in value or ":" in value
            or any(ord(character) < 32 for character in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("relative_path must be a normalized relative POSIX file path, without '..', drives or backslashes")
        return value


class _ManifestIdentity(BaseModel):
    """Shared validation for builders, deserialization and public hash calls."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal["1"] = "1"
    frequency: BarFrequency
    adjustment: AdjustmentMode
    instruments: tuple[StrictStr, ...]
    start_date: date
    end_date: date
    row_count: RowCount
    files: tuple[DatasetFileEntry, ...]
    source: SourceMetadata

    @field_validator("instruments")
    @classmethod
    def canonicalize_instruments(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for key in values:
            parts = key.split(":", 2)
            if len(parts) != 3 or not parts[2] or parts[2] != parts[2].strip():
                raise ValueError("instruments must contain InstrumentId.canonical_key strings")
            Market(parts[0])
            Exchange(parts[1])
        return tuple(sorted(set(values)))

    @field_validator("files")
    @classmethod
    def canonicalize_files(cls, values: tuple[DatasetFileEntry, ...]) -> tuple[DatasetFileEntry, ...]:
        if len({entry.relative_path for entry in values}) != len(values):
            raise ValueError("duplicate file relative_path is not allowed")
        return tuple(sorted(values, key=lambda entry: entry.relative_path))

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


class DatasetManifest(_ManifestIdentity):
    """A checked declaration of dataset identity, not a durable data snapshot.

    Use create() to derive the version. Direct construction/JSON loading also
    verifies supplied versions. verify_data_version() checks current metadata
    only; it neither opens Parquet nor verifies actual file bytes/counts.
    """

    data_version: DataVersion
    created_at: AwareDatetime

    @classmethod
    def create(cls, *, created_at: datetime, **identity_fields: Any) -> Self:
        """Build from identity fields; an explicit data_version is forbidden."""
        identity = _ManifestIdentity.model_validate(identity_fields)
        payload = identity.model_dump(mode="json")
        return cls(
            **payload, created_at=created_at, data_version=compute_data_version(payload),
        )

    def identity_payload(self) -> dict[str, Any]:
        """Return a detached, JSON-compatible payload, without time/location."""
        # Explicitly project identity fields rather than dumping the complete
        # manifest, so neither data_version nor created_at can enter the hash.
        identity = _ManifestIdentity.model_validate({
            name: getattr(self, name) for name in _ManifestIdentity.model_fields
        })
        return identity.model_dump(mode="json")

    def verify_data_version(self) -> None:
        """Raise ValueError on altered identity or mismatched stored version."""
        expected = compute_data_version(self.identity_payload())
        if self.data_version != expected:
            raise ValueError(
                f"manifest data_version mismatch: stored={self.data_version}, expected={expected}",
            )

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        self.verify_data_version()
        return self
