"""In-memory CN ETF universe contracts and content identities; no data access."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field,
    StringConstraints, model_validator,
)

from aquant.data.versioning import canonical_json_bytes
from aquant.domain import AssetType, InstrumentId
from aquant.markets.china import ChinaEquityRuleBook


Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
SchemaVersion = Annotated[int, Field(strict=True, ge=1, le=1)]


def _copy_json(value: Any) -> Any:
    """Validate before serialization can coerce types, and detach nested inputs."""
    return json.loads(canonical_json_bytes(value))


JsonObject = Annotated[dict[str, Any], BeforeValidator(_copy_json)]
JsonList = Annotated[list[Any], BeforeValidator(_copy_json)]
UtcDatetime = Annotated[
    AwareDatetime, AfterValidator(lambda value: value.astimezone(timezone.utc)),
]


class ResearchModel(BaseModel):
    """Frozen top level only. Revalidate at JSON boundaries, not a trust token."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    def verify(self) -> Self:
        """Return a detached, revalidated copy; detect nested mutation/tampering."""
        return type(self).model_validate(self.model_dump(mode="python", warnings="error"))

    def model_dump_json(self, **kwargs: Any) -> str:
        return BaseModel.model_dump_json(self.verify(), **kwargs)


def _cn_etfs(value: Any) -> tuple[InstrumentId, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("instruments must be a nonempty list/tuple of full InstrumentId values")
    instruments = []
    seen: dict[str, InstrumentId] = {}
    rulebook = ChinaEquityRuleBook()
    for item in value:
        # Do not trust model_construct/model_copy or mutable caller-owned containers.
        instrument = InstrumentId.model_validate(
            item.model_dump(mode="python") if isinstance(item, InstrumentId) else item,
        )
        key = instrument.canonical_key
        if key in seen:
            kind = "duplicate" if seen[key] == instrument else "conflicting identity"
            raise ValueError(f"{kind} instrument: {key}")
        seen[key] = instrument
        rulebook.validate_instrument(instrument)
        if instrument.asset_type is not AssetType.ETF:
            raise ValueError("research universe requires CN ETF instruments")
        instruments.append(instrument)
    return tuple(sorted(instruments, key=lambda item: item.canonical_key))


CnEtfs = Annotated[tuple[InstrumentId, ...], BeforeValidator(_cn_etfs)]


class _UniverseDefinitionIdentity(ResearchModel):
    # Schema 1 identity fields are fixed here, independent of audit/storage fields.
    schema_version: SchemaVersion = 1
    universe_id: Text
    as_of: UtcDatetime
    instruments: CnEtfs
    selection_rules: JsonObject
    notes: Text


class _UniverseSnapshotIdentity(_UniverseDefinitionIdentity):
    definition_version: Digest
    known_at: UtcDatetime
    membership_basis: Literal["prospective_frozen", "retrospective_manual"]


class _ContentAddressed(ResearchModel):
    _identity_model: ClassVar[type[ResearchModel]]
    _hash_field: ClassVar[str] = "version"

    def identity_payload(self) -> dict[str, Any]:
        identity = self._identity_model.model_validate({
            name: getattr(self, name) for name in self._identity_model.model_fields
        })
        return identity.model_dump(mode="json")

    @classmethod
    def create(cls, **values: Any) -> Self:
        identity = cls._identity_model.model_validate(values)
        payload = identity.model_dump(mode="json")
        digest = "sha256:" + sha256(canonical_json_bytes(payload)).hexdigest()
        return cls.model_validate({**identity.model_dump(mode="python"), cls._hash_field: digest})

    @model_validator(mode="after")
    def _check_identity(self) -> Self:
        expected = "sha256:" + sha256(canonical_json_bytes(self.identity_payload())).hexdigest()
        if getattr(self, self._hash_field) != expected:
            raise ValueError(f"{self._hash_field} does not match normalized content")
        return self


class UniverseDefinition(_UniverseDefinitionIdentity, _ContentAddressed):
    _identity_model = _UniverseDefinitionIdentity
    version: Digest


class UniverseSnapshot(_UniverseSnapshotIdentity, _ContentAddressed):
    _identity_model = _UniverseSnapshotIdentity
    version: Digest

    @classmethod
    def from_definition(
        cls, definition: UniverseDefinition, *, known_at: datetime,
        membership_basis: Literal["prospective_frozen", "retrospective_manual"],
    ) -> Self:
        checked = definition.verify()
        return cls.create(
            **checked.identity_payload(), definition_version=checked.version,
            known_at=known_at, membership_basis=membership_basis,
        )
