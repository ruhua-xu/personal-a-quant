from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from aquant.research import UniverseDefinition, UniverseSnapshot


def test_definition_normalizes_full_ids_and_has_reproducible_version(definition_values):
    first = UniverseDefinition.create(**definition_values)
    second = UniverseDefinition.create(**{**definition_values, "instruments": list(reversed(first.instruments))})
    assert first == second
    assert [item.canonical_key for item in first.instruments] == ["CN:XSHE:159915", "CN:XSHG:510300"]
    assert UniverseDefinition.model_validate_json(first.model_dump_json()) == first
    assert first.verify() == first


@pytest.mark.parametrize("change", [
    {"market": "US"}, {"exchange": "XNAS"}, {"currency": "USD"},
    {"asset_type": "STOCK"}, {"symbol": " "},
])
def test_invalid_identity(definition_values, change):
    item = definition_values["instruments"][0].model_dump()
    with pytest.raises(ValidationError):
        UniverseDefinition.create(**{**definition_values, "instruments": [{**item, **change}]})


@pytest.mark.parametrize("instruments", [[], ["sh510300"], ["510300"], "510300"])
def test_no_inference_or_empty_pool(definition_values, instruments):
    with pytest.raises(ValidationError):
        UniverseDefinition.create(**{**definition_values, "instruments": instruments})


@pytest.mark.parametrize("conflict", [{}, {"asset_type": "STOCK"}, {"currency": "USD"}])
def test_duplicate_and_conflicting_identity_rejected(definition_values, conflict):
    first = definition_values["instruments"][0]
    other = {**first.model_dump(), **conflict}
    with pytest.raises(ValidationError, match="duplicate|conflicting identity"):
        UniverseDefinition.create(**{**definition_values, "instruments": [first, other]})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), Decimal("0.1"), object(), {1}, (1,), {1: "bad"}])
def test_strict_json_rejects_non_json_values(definition_values, bad):
    with pytest.raises(ValidationError):
        UniverseDefinition.create(**{**definition_values, "selection_rules": {"nested": [bad]}})


def test_circular_json_rejected(definition_values):
    circular = {}
    circular["self"] = circular
    with pytest.raises(ValidationError, match="circular"):
        UniverseDefinition.create(**{**definition_values, "selection_rules": circular})


@pytest.mark.parametrize("field", ["universe_id", "notes"])
def test_empty_text_rejected(definition_values, field):
    with pytest.raises(ValidationError):
        UniverseDefinition.create(**{**definition_values, field: "  "})


def test_time_aware_and_canonical_utc(definition_values):
    utc = UniverseDefinition.create(**definition_values)
    sh = UniverseDefinition.create(**{**definition_values, "as_of": datetime(2024, 1, 1, 8, tzinfo=ZoneInfo("Asia/Shanghai"))})
    assert sh.version == utc.version
    assert sh.as_of.tzinfo is timezone.utc
    assert json.loads(sh.model_dump_json())["as_of"] == "2024-01-01T00:00:00Z"
    with pytest.raises(ValidationError):
        UniverseDefinition.create(**{**definition_values, "as_of": datetime(2024, 1, 1)})


def test_snapshot_identity_roundtrip(universe):
    assert UniverseSnapshot.model_validate_json(universe.model_dump_json()) == universe
    assert universe.verify() == universe
    for name, value in [
        ("notes", "other note"), ("definition_version", "sha256:" + "c" * 64),
        ("known_at", "2024-01-02T00:00:00Z"), ("as_of", "2023-12-31T00:00:00Z"),
        ("membership_basis", "retrospective_manual"), ("selection_rules", {"manual": False}),
        ("instruments", [universe.instruments[0].model_dump(mode="json")]),
    ]:
        payload = universe.identity_payload()
        payload[name] = value
        changed = UniverseSnapshot.create(**payload)
        assert changed.version != universe.version, name
        with pytest.raises(ValidationError, match="does not match"):
            UniverseSnapshot.model_validate({**payload, "version": universe.version})


@pytest.mark.parametrize("field", ["as_of", "known_at"])
def test_snapshot_naive_time_rejected(universe, field):
    with pytest.raises(ValidationError):
        UniverseSnapshot.create(**{**universe.identity_payload(), field: datetime(2024, 1, 1)})


def test_deep_copy_and_reverify_mutation(definition_values):
    original = deepcopy(definition_values)
    definition = UniverseDefinition.create(**definition_values)
    definition_values["selection_rules"]["evidence"].append("caller mutation")
    assert definition.selection_rules == original["selection_rules"]
    snapshot = UniverseSnapshot.from_definition(definition, known_at=definition.as_of, membership_basis="retrospective_manual")
    definition.selection_rules["evidence"].append("internal mutation")
    assert snapshot.selection_rules == original["selection_rules"]
    for operation in (definition.verify, definition.model_dump_json):
        with pytest.raises(ValidationError, match="does not match"):
            operation()
    with pytest.raises(ValidationError):
        UniverseSnapshot.from_definition(definition, known_at=definition.as_of, membership_basis="retrospective_manual")


def test_arbitrary_json_lists_retain_order(definition_values):
    a = UniverseDefinition.create(**definition_values)
    b = UniverseDefinition.create(**{**definition_values, "selection_rules": {"manual": True, "evidence": ["second", "first"]}})
    assert a.version != b.version


@pytest.mark.parametrize("bad", ["latest", "sha256:" + "A" * 64, "sha256:" + "0" * 63])
def test_invalid_version_rejected(universe, bad):
    with pytest.raises(ValidationError):
        UniverseSnapshot.model_validate({**universe.model_dump(), "version": bad})


@pytest.mark.parametrize("bad", [2, True, "1"])
def test_schema_version_strict(definition_values, bad):
    with pytest.raises(ValidationError):
        UniverseDefinition.create(**{**definition_values, "schema_version": bad})
