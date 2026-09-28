"""Synthetic metadata only; the parent conftest blocks all network access."""

from datetime import datetime, timezone

import pytest

from aquant.research import UniverseDefinition, UniverseSnapshot


@pytest.fixture
def definition_values(sample_instruments):
    return dict(
        universe_id="cn-etf-fixture", as_of=datetime(2024, 1, 1, tzinfo=timezone.utc),
        instruments=sample_instruments[:2],
        selection_rules={"manual": True, "evidence": ["first", "second"]},
        notes="Synthetic fixture, not a liquidity or PIT assertion.",
    )


@pytest.fixture
def universe(definition_values):
    return UniverseSnapshot.from_definition(
        UniverseDefinition.create(**definition_values),
        known_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        membership_basis="prospective_frozen",
    )


@pytest.fixture
def config_values(universe):
    return dict(
        research_id="fixture-study", strategy_key="etf-price", strategy_version="1",
        data_version="sha256:" + "a" * 64, universe_id=universe.universe_id,
        universe_version=universe.version, top_n=2,
        calendar_instruments=universe.instruments,
        raw_review=dict(reference_id="raw-review-fixture", content_hash="sha256:" + "b" * 64,
                        scope="Synthetic dates and members only", limitations="No actual RAW audit"),
        research_period={"start_date": "2024-02-01", "end_date": "2024-03-31"},
        validation_period={"start_date": "2024-04-01", "end_date": "2024-04-30"},
        test_period={"start_date": "2024-05-01", "end_date": "2024-05-31"},
        hypothesis="Fixture only; no investment claim.",
        parameter_comparisons=[{"trend_lookback": 180}, {"trend_lookback": 200}],
        promotion_criteria={"required_reviews": ["RAW", "PIT", "calendar"]},
    )


@pytest.fixture
def run_values(config_values):
    from aquant.research import ResearchConfig

    config = ResearchConfig.create(**config_values)
    return dict(
        research_run_id="run-fixture", research_id=config.research_id,
        strategy_key=config.strategy_key, strategy_version=config.strategy_version,
        data_version=config.data_version, universe_id=config.universe_id,
        universe_version=config.universe_version, parameters_hash=config.parameters_hash,
        git_commit="a" * 40, runtime_versions={"Python": "3.12.2", "Pydantic": "2.13.5"},
        status="CREATED", created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
