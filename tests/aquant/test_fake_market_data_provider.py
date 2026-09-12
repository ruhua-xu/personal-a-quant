from datetime import date
import socket

import pandas as pd
import pytest
from pydantic import ValidationError

from aquant.data import AdjustmentMode, HistoryRequest, MarketDataSet, empty_history_frame
from aquant.data.providers import FakeMarketDataProvider, MarketDataProvider
from aquant.domain import AssetType, Currency, Exchange, InstrumentId, Market


def request_for(instruments, **overrides):
    values = dict(instruments=instruments, start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
    values.update(overrides)
    return HistoryRequest(**values)


def test_fake_implements_provider_contract_and_filters_instrument(sample_dataset, sample_instruments):
    provider = FakeMarketDataProvider(sample_dataset)
    assert isinstance(provider, MarketDataProvider)
    result = provider.get_history(request_for([sample_instruments[1]]))
    assert result.provider == provider.provider_name == "fake"
    assert result.frequency == sample_dataset.frequency
    assert result.adjustment == sample_dataset.adjustment
    assert result.generated_at == sample_dataset.generated_at
    assert result.data["instrument_key"].tolist() == ["CN:XSHE:159915"]


def test_fake_uses_inclusive_dates_and_filters_securities_together(sample_dataset, sample_instruments):
    provider = FakeMarketDataProvider(sample_dataset)
    result = provider.get_history(request_for(
        [sample_instruments[0]], start_date=date(2026, 9, 2), end_date=date(2026, 9, 3),
    ))
    assert result.data["trade_date"].tolist() == [date(2026, 9, 2), date(2026, 9, 3)]
    assert result.data["instrument_key"].tolist() == ["CN:XSHG:510300"] * 2
    assert result.data["close"].tolist() == [4.0, 4.2]


def test_fake_filters_same_day_across_multiple_instruments(sample_dataset, sample_instruments):
    result = FakeMarketDataProvider(sample_dataset).get_history(request_for(
        sample_instruments, start_date=date(2026, 9, 3), end_date=date(2026, 9, 3),
    ))
    assert result.data["instrument_key"].tolist() == ["CN:XSHG:510300", "US:XNAS:AAPL"]
    assert result.data["trade_date"].tolist() == [date(2026, 9, 3)] * 2


def test_fake_matches_full_identity_not_symbol(sample_dataset):
    same_symbol_elsewhere = InstrumentId(
        market=Market.US, exchange=Exchange.XNAS, symbol="510300",
        asset_type=AssetType.ETF, currency=Currency.USD,
    )
    result = FakeMarketDataProvider(sample_dataset).get_history(request_for([same_symbol_elsewhere]))
    pd.testing.assert_frame_equal(result.data, empty_history_frame())


def test_no_dates_match_returns_standard_empty_frame(sample_dataset, sample_instruments):
    result = FakeMarketDataProvider(sample_dataset).get_history(request_for(
        sample_instruments, start_date=date(2025, 1, 1), end_date=date(2025, 1, 2),
    ))
    pd.testing.assert_frame_equal(result.data, empty_history_frame())
    assert result.generated_at == sample_dataset.generated_at


def test_empty_source_returns_standard_empty_frame(sample_dataset, sample_instruments):
    source = MarketDataSet(
        data=empty_history_frame(), frequency=sample_dataset.frequency,
        adjustment=sample_dataset.adjustment, provider="empty-fixture",
        generated_at=sample_dataset.generated_at,
    )
    result = FakeMarketDataProvider(source).get_history(request_for(sample_instruments))
    pd.testing.assert_frame_equal(result.data, empty_history_frame())


@pytest.mark.parametrize("adjustment", [AdjustmentMode.FORWARD, AdjustmentMode.BACKWARD])
def test_fake_rejects_adjustment_mismatch_without_relabeling(sample_dataset, sample_instruments, adjustment):
    with pytest.raises(ValueError, match="adjustment"):
        FakeMarketDataProvider(sample_dataset).get_history(request_for(
            sample_instruments, adjustment=adjustment,
        ))


def test_fake_accepts_explicitly_adjusted_fixture(sample_dataset, sample_instruments):
    source = MarketDataSet(
        data=sample_dataset.data, frequency=sample_dataset.frequency,
        adjustment=AdjustmentMode.FORWARD, provider="forward-fixture",
        generated_at=sample_dataset.generated_at,
    )
    result = FakeMarketDataProvider(source).get_history(request_for(
        sample_instruments, adjustment=AdjustmentMode.FORWARD,
    ))
    assert result.adjustment is AdjustmentMode.FORWARD
    pd.testing.assert_frame_equal(result.data, source.data)


def test_fake_is_deterministic_and_does_not_access_network(sample_dataset, sample_instruments):
    # conftest blocks DNS and outbound sockets for all aquant unit tests.
    with pytest.raises(AssertionError, match="must not access the network"):
        socket.create_connection(("example.invalid", 443))
    provider = FakeMarketDataProvider(sample_dataset)
    request = request_for(sample_instruments)
    first = provider.get_history(request)
    second = provider.get_history(request)
    pd.testing.assert_frame_equal(first.data, second.data)
    assert first.generated_at == second.generated_at == sample_dataset.generated_at
    assert first.data is not second.data


def test_fake_isolates_source_and_response_edits(sample_dataset, sample_instruments):
    expected = sample_dataset.data.copy(deep=True)
    provider = FakeMarketDataProvider(sample_dataset)
    sample_dataset.data.loc[:, "volume"] = 0
    first = provider.get_history(request_for(sample_instruments))
    pd.testing.assert_frame_equal(first.data, expected)
    first.data.loc[:, "volume"] = -1
    second = provider.get_history(request_for(sample_instruments))
    pd.testing.assert_frame_equal(second.data, expected)


def test_fake_revalidates_edited_dataset_on_construction(sample_dataset):
    sample_dataset.data.loc[:, "volume"] = -1
    with pytest.raises(ValidationError, match="volume"):
        FakeMarketDataProvider(sample_dataset)
