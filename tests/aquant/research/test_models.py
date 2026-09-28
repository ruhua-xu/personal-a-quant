from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from aquant.domain import SecurityScore
from aquant.research import (
    DateRange, FactorSnapshot, ResearchConfig, ResearchResult, ResearchRun,
    UniverseSnapshot, validate_research_inputs,
)


FACTORS = ("momentum_3m", "momentum_6m", "trend_score", "volatility")


def test_expanded_defaults_json_and_roundtrip(config_values, universe):
    config = ResearchConfig.create(**config_values)
    validate_research_inputs(config, universe)
    payload = json.loads(config.model_dump_json())
    assert len(payload) == 42
    assert payload["factor_weights"] == dict.fromkeys(FACTORS, "0.25")
    assert [payload[key] for key in ("momentum_3m_lookback", "momentum_6m_lookback", "trend_lookback", "volatility_lookback")] == [63, 126, 200, 63]
    assert payload["frequency"] == "DAILY" and payload["adjustment"] == "RAW"
    assert payload["forward_horizons"] == [5, 20]
    assert payload["schema_version"] == 1
    assert ResearchConfig.model_validate_json(config.model_dump_json()) == config
    assert config.verify() == config


def test_golden_schema_one_hash(config_values, universe):
    # Fixed expected digests are not computed by the implementation in this test.
    assert universe.definition_version == "sha256:8d47a49e53ba06efad22e3091fce0ab18e15d8f68c11ba61661be14974b2a190"
    assert universe.version == "sha256:7fdc0ab8a16ee742b939fe64b3a9436f2edc0e9374d070942632c09abbe00b07"
    assert ResearchConfig.create(**config_values).parameters_hash == "sha256:437ac8fc11fc4c2ed7101b62c40b88e33e6b8cb003c3fd001b02eec301690c99"


def test_normalization_equivalence(config_values):
    a = ResearchConfig.create(**config_values)
    b = ResearchConfig.create(**{
        **config_values, "factor_weights": dict.fromkeys(FACTORS, "2.500e-1"),
        "calendar_instruments": list(reversed(a.calendar_instruments)),
        "forward_horizons": [20, 5, 20],
    })
    assert a.parameters_hash == b.parameters_hash


def test_weights_exact_and_independent_of_decimal_context(config_values):
    weights = dict(zip(FACTORS, ["0.1", "0.2", "0.3", "0.4"]))
    with localcontext() as ctx:
        ctx.prec = 2
        config = ResearchConfig.create(**{**config_values, "factor_weights": weights})
        assert config.factor_weights == weights
        # Would round to 1 under a low precision Decimal sum.
        weights["volatility"] = "0.40000000000000000000000000000000000001"
        with pytest.raises(ValidationError, match="exactly"):
            ResearchConfig.create(**{**config_values, "factor_weights": weights})


@pytest.mark.parametrize("weights", [
    {}, {"other": "1"}, dict.fromkeys(FACTORS, 0.25), dict.fromkeys(FACTORS, Decimal("0.25")),
    dict.fromkeys(FACTORS, "0.3"), dict.fromkeys(FACTORS, "NaN"),
    dict.fromkeys(FACTORS, "Infinity"), dict.fromkeys(FACTORS, "-0.25"),
    dict.fromkeys(FACTORS, "not-a-number"), dict.fromkeys(FACTORS, "1.1"),
])
def test_invalid_weights(config_values, weights):
    with pytest.raises(ValidationError):
        ResearchConfig.create(**{**config_values, "factor_weights": weights})


@pytest.mark.parametrize("field,bad", [
    ("momentum_3m_lookback", True), ("momentum_3m_lookback", 0),
    ("momentum_3m_lookback", "63"), ("momentum_3m_lookback", 63.0),
    ("momentum_3m_lookback", 126), ("momentum_6m_lookback", -1),
    ("trend_lookback", 0), ("volatility_lookback", 1), ("annualization_sessions", False),
    ("volatility_ddof", 0), ("volatility_ddof", True), ("top_n", 0),
    ("cash_allowed", 1), ("bucket_count", 1), ("min_bucket_size", 0),
    ("min_ic_observations", 1), ("forward_horizons", []),
    ("forward_horizons", [True, 5]), ("forward_horizons", [0, 5]),
    ("forward_horizons", [5.0]), ("frequency", "MINUTE"), ("adjustment", "FORWARD"),
    ("price_field", "adjusted_close"), ("price_semantics", "total_return"),
    ("weekly_anchor", "FRIDAY"), ("score_contract_version", "2"),
    ("hypothesis", "  "), ("strategy_version", ""),
    ("parameter_comparisons", ["search(...) "]),
])
def test_invalid_parameters(config_values, field, bad):
    with pytest.raises(ValidationError):
        ResearchConfig.create(**{**config_values, field: bad})


@pytest.mark.parametrize("bad", [
    {"start_date": "2024-03-31", "end_date": "2024-04-30"},
    {"start_date": "2024-01-01", "end_date": "2024-01-30"},
    {"start_date": "2024-04-30", "end_date": "2024-04-01"},
])
def test_overlapping_reversed_splits(config_values, bad):
    with pytest.raises(ValidationError):
        ResearchConfig.create(**{**config_values, "validation_period": bad})


@pytest.mark.parametrize("bad", [datetime(2024, 1, 1, tzinfo=timezone.utc), 1704067200, "20240101", "2024-01-01T00:00:00Z"])
def test_dates_are_dates_not_timestamps(bad):
    with pytest.raises(ValidationError):
        DateRange(start_date=bad, end_date="2024-12-31")


@pytest.mark.parametrize("field,value", [
    ("top_n", 1), ("strategy_version", "2"), ("research_id", "another-study"),
    ("data_version", "sha256:" + "c" * 64), ("trend_lookback", 199),
    ("cash_allowed", False), ("forward_horizons", [5, 21]),
    ("hypothesis", "A different hypothesis"),
])
def test_material_parameters_change_identity_and_tampering_fails(config_values, field, value):
    config = ResearchConfig.create(**config_values)
    altered = {**config.identity_payload(), field: value}
    assert ResearchConfig.create(**altered).parameters_hash != config.parameters_hash
    with pytest.raises(ValidationError, match="does not match"):
        ResearchConfig.model_validate_json(json.dumps({**altered, "parameters_hash": config.parameters_hash}))


def test_ordered_comparisons_and_json_deep_copy(config_values):
    config = ResearchConfig.create(**config_values)
    reversed_config = ResearchConfig.create(**{**config_values, "parameter_comparisons": list(reversed(config_values["parameter_comparisons"]))})
    assert config.parameters_hash != reversed_config.parameters_hash
    config_values["promotion_criteria"]["required_reviews"].append("caller mutation")
    config_values["parameter_comparisons"][0]["trend_lookback"] = 999
    assert config.promotion_criteria["required_reviews"] == ["RAW", "PIT", "calendar"]
    assert config.parameter_comparisons[0] == {"trend_lookback": 180}
    config.parameter_comparisons[0]["trend_lookback"] = 999
    for operation in (config.verify, config.model_dump_json):
        with pytest.raises(ValidationError):
            operation()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), Decimal("0.1"), object(), (1, 2)])
def test_config_strict_json(config_values, bad):
    with pytest.raises(ValidationError):
        ResearchConfig.create(**{**config_values, "promotion_criteria": {"bad": bad}})


@pytest.mark.parametrize("field,value", [("git_commit", "a" * 40), ("research_run_id", "run-1"), ("local_path", "C:/data")])
def test_execution_and_path_fields_not_configuration(config_values, field, value):
    with pytest.raises(ValidationError):
        ResearchConfig.create(**{**config_values, field: value})


@pytest.mark.parametrize("field,value", [("universe_id", "different"), ("universe_version", "sha256:" + "b" * 64), ("top_n", 3)])
def test_cross_object_rejects_reference_or_size_mismatch(config_values, universe, field, value):
    config = ResearchConfig.create(**{**config_values, field: value})
    with pytest.raises(ValueError):
        validate_research_inputs(config, universe)


def test_calendar_membership_and_nonempty(config_values, universe):
    with pytest.raises(ValidationError):
        ResearchConfig.create(**{**config_values, "calendar_instruments": []})
    outside = {**universe.instruments[0].model_dump(), "symbol": "510500"}
    config = ResearchConfig.create(**{**config_values, "calendar_instruments": [outside]})
    with pytest.raises(ValueError, match="subset"):
        validate_research_inputs(config, universe)
    for instruments in ([universe.instruments[0]] * 2, [{**outside, "currency": "USD"}]):
        with pytest.raises(ValidationError):
            ResearchConfig.create(**{**config_values, "calendar_instruments": instruments})


@pytest.mark.parametrize("offset,valid", [(timedelta(microseconds=-1), True), (timedelta(0), True), (timedelta(microseconds=1), False)])
def test_prospective_known_at_rulebook_boundary(config_values, universe, offset, valid):
    boundary = datetime(2024, 2, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    changed = UniverseSnapshot.create(**{**universe.identity_payload(), "known_at": boundary + offset})
    config = ResearchConfig.create(**{**config_values, "universe_version": changed.version})
    if valid:
        validate_research_inputs(config, changed)
    else:
        with pytest.raises(ValueError, match="scoring start"):
            validate_research_inputs(config, changed)


def test_retrospective_membership_is_preserved_not_promoted(config_values, universe):
    changed = UniverseSnapshot.create(**{**universe.identity_payload(), "known_at": "2026-01-01T00:00:00Z", "membership_basis": "retrospective_manual"})
    config = ResearchConfig.create(**{**config_values, "universe_version": changed.version})
    validate_research_inputs(config, changed)
    assert changed.membership_basis == "retrospective_manual"


def test_cross_object_checks_reverify_mutated_content(config_values, universe):
    config = ResearchConfig.create(**config_values)
    universe.selection_rules["manual"] = False
    with pytest.raises(ValidationError, match="does not match"):
        validate_research_inputs(config, universe)


NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
LATER = NOW + timedelta(seconds=1)


@pytest.mark.parametrize("overrides", [
    {}, {"status": "RUNNING", "started_at": NOW},
    {"status": "COMPLETED", "started_at": NOW, "completed_at": LATER},
    {"status": "FAILED", "completed_at": LATER, "error": {"code": "INPUT", "message": "Invalid input"}},
    {"status": "FAILED", "started_at": NOW, "completed_at": LATER, "error": {"code": "SCHEMA", "message": "Bad schema", "last_completed_stage": "input"}},
])
def test_run_valid_states_roundtrip(run_values, overrides):
    run = ResearchRun(**{**run_values, **overrides})
    assert ResearchRun.model_validate_json(run.model_dump_json()) == run
    assert run.git_commit_source == "caller_supplied"


@pytest.mark.parametrize("overrides", [
    {"started_at": NOW}, {"completed_at": LATER},
    {"status": "RUNNING"}, {"status": "RUNNING", "started_at": NOW, "completed_at": LATER},
    {"status": "COMPLETED"}, {"status": "COMPLETED", "started_at": NOW},
    {"status": "FAILED", "completed_at": LATER},
    {"status": "FAILED", "error": {"code": "ERR", "message": "failed"}},
    {"error": {"code": "ERR", "message": "failed"}},
    {"status": "COMPLETED", "started_at": NOW, "completed_at": LATER, "error": {"code": "ERR", "message": "failed"}},
    {"status": "RUNNING", "started_at": NOW - timedelta(seconds=1)},
    {"status": "COMPLETED", "started_at": LATER, "completed_at": NOW},
    {"git_commit": "abcdef"}, {"git_commit_source": "auto_verified"}, {"runtime_versions": {}},
])
def test_run_invalid_state_or_identity(run_values, overrides):
    with pytest.raises(ValidationError):
        ResearchRun(**{**run_values, **overrides})


@pytest.mark.parametrize("field", ["created_at", "started_at", "completed_at"])
def test_run_naive_time_rejected(run_values, field):
    values = {**run_values, "status": "COMPLETED", "started_at": NOW, "completed_at": LATER}
    values[field] = datetime(2024, 6, 1)
    with pytest.raises(ValidationError):
        ResearchRun(**values)


def test_run_utc_deep_copy_and_audit_outside_hash(run_values):
    first = ResearchRun(**run_values)
    run_values["runtime_versions"]["Python"] = "changed"
    assert first.runtime_versions["Python"] == "3.12.2"
    second = ResearchRun(**{**first.model_dump(), "research_run_id": "run-2", "created_at": datetime(2024, 6, 1, 8, tzinfo=ZoneInfo("Asia/Shanghai")), "git_commit": "b" * 40})
    assert first.parameters_hash == second.parameters_hash
    assert second.created_at.tzinfo is timezone.utc


def test_test_stage_requires_explicit_release_and_consumption(run_values, config_values):
    with pytest.raises(ValidationError, match="test_release_ref"):
        ResearchRun(**{**run_values, "evaluation_stage": "test"})
    values = {**run_values, "evaluation_stage": "test", "test_release_ref": config_values["raw_review"], "status": "RUNNING", "started_at": NOW}
    with pytest.raises(ValidationError, match="consumed"):
        ResearchRun(**values)
    assert ResearchRun(**values, test_consumed=True).test_consumed
    with pytest.raises(ValidationError, match="consumed"):
        ResearchRun(**{**values, "status": "FAILED", "completed_at": LATER, "error": {"code": "STOPPED", "message": "Test already started"}})


@pytest.mark.parametrize("reference", ["/private/local.json", "C:/local.json", "../local.json", "https://example.com/review", ""])
def test_reference_is_logical_not_filesystem_or_network_path(config_values, reference):
    values = {**config_values, "raw_review": {**config_values["raw_review"], "reference_id": reference}}
    with pytest.raises(ValidationError):
        ResearchConfig.create(**values)


def test_config_hash_covers_every_declared_field(config_values):
    config = ResearchConfig.create(**config_values)
    assert set(config.identity_payload()) == set(ResearchConfig.model_fields) - {"parameters_hash"}


def test_weight_extremes_and_negative_zero(config_values):
    weights = dict.fromkeys(FACTORS, "-0.000")
    weights["momentum_3m"] = "1.000"
    config = ResearchConfig.create(**{**config_values, "factor_weights": weights})
    assert config.factor_weights == dict(zip(FACTORS, ["1", "0", "0", "0"]))


def test_empty_dict_and_list_are_not_fabricated_research(config_values):
    config = ResearchConfig.create(**{**config_values, "parameter_comparisons": []})
    assert config.parameter_comparisons == []  # Explicitly no manual comparisons, not a search.


def test_minimal_wrappers_do_not_invent_results(universe, run_values):
    wrapper = FactorSnapshot(research_run_id="run", signal_date=date(2024, 6, 1), as_of=NOW, instrument_id=universe.instruments[0])
    assert wrapper.security_score is None
    assert FactorSnapshot.model_validate_json(wrapper.model_dump_json()) == wrapper
    refs = {key: value for key, value in run_values.items() if key not in {"git_commit", "runtime_versions", "status", "created_at"}}
    result = ResearchResult(**refs, limitations=("Schema fixture only, no research performed",))
    assert result.coverage_summary is None and result.audit_checks is None
    assert result.artifact_references == () and result.promotion_level == 0
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        ResearchResult(**refs, limitations=("limited",), promotion_level=2)


def test_security_score_reused_with_decimal_and_utc(universe):
    sh = NOW.astimezone(ZoneInfo("Asia/Shanghai"))
    score = SecurityScore(strategy_key="fixture", strategy_version="1", instrument_id=universe.instruments[0], score=Decimal("0.1") + Decimal("0.2"), as_of=sh)
    wrapper = FactorSnapshot(research_run_id="run", signal_date=NOW.date(), as_of=sh, instrument_id=score.instrument_id, security_score=score)
    assert type(wrapper.security_score) is SecurityScore
    assert wrapper.security_score.score == Decimal("0.3")
    assert wrapper.security_score.as_of.tzinfo is timezone.utc
    assert "+08:00" not in wrapper.model_dump_json()
    assert FactorSnapshot.model_validate_json(wrapper.model_dump_json()) == wrapper


@pytest.mark.parametrize("change", [{"score": "-0.1"}, {"score": "1.1"}, {"score": "NaN"}, {"as_of": LATER}, {"as_of": datetime(2024, 6, 1)}])
def test_wrapper_rejects_invalid_score(universe, change):
    score = dict(strategy_key="fixture", strategy_version="1", instrument_id=universe.instruments[0], score="0.5", as_of=NOW)
    with pytest.raises(ValidationError):
        FactorSnapshot(research_run_id="run", signal_date=NOW.date(), as_of=NOW, instrument_id=universe.instruments[0], security_score={**score, **change})


def test_wrapper_rejects_naive_time_date_and_instrument_mismatch(universe):
    values = dict(research_run_id="run", signal_date=NOW.date(), as_of=NOW, instrument_id=universe.instruments[0])
    with pytest.raises(ValidationError):
        FactorSnapshot(**{**values, "as_of": NOW.replace(tzinfo=None)})
    with pytest.raises(ValidationError, match="signal_date"):
        FactorSnapshot(**{**values, "signal_date": date(2024, 6, 2)})
    score = dict(strategy_key="fixture", strategy_version="1", instrument_id=universe.instruments[1], score="0.5", as_of=NOW)
    with pytest.raises(ValidationError, match="instrument/as_of"):
        FactorSnapshot(**values, security_score=score)


@pytest.mark.parametrize("field", ["code", "message"])
def test_failed_run_requires_nonempty_error_text(run_values, field):
    error = {"code": "ERR", "message": "failure", field: "  "}
    with pytest.raises(ValidationError):
        ResearchRun(**{**run_values, "status": "FAILED", "completed_at": LATER, "error": error})


def test_json_load_and_explicit_verify_share_config_normalization(config_values):
    config = ResearchConfig.create(**config_values)
    payload = json.loads(config.model_dump_json())
    payload["factor_weights"] = dict.fromkeys(FACTORS, "0.2500")
    payload["forward_horizons"] = [20, 5, 20]
    payload["calendar_instruments"].reverse()
    restored = ResearchConfig.model_validate_json(json.dumps(payload))
    assert restored == config
    assert restored.verify() == config


def test_json_unknown_or_missing_config_fields_fail(config_values):
    config = ResearchConfig.create(**config_values)
    payload = json.loads(config.model_dump_json())
    with pytest.raises(ValidationError):
        ResearchConfig.model_validate({**payload, "optimizer": "auto"})
    del payload["parameters_hash"]
    with pytest.raises(ValidationError):
        ResearchConfig.model_validate_json(json.dumps(payload))
