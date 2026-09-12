"""Deterministic fixtures and a network guard for aquant unit tests."""

from datetime import date, datetime, timezone
import socket

import pandas as pd
import pytest

from aquant.data import AdjustmentMode, BarFrequency, HISTORY_COLUMNS, MarketDataSet
from aquant.domain import AssetType, Currency, Exchange, InstrumentId, Market


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("aquant unit tests must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)
    monkeypatch.setattr(socket.socket, "sendto", reject_network)


@pytest.fixture
def sample_instruments():
    return (
        InstrumentId(
            market=Market.CN, exchange=Exchange.XSHG, symbol="510300",
            asset_type=AssetType.ETF, currency=Currency.CNY,
        ),
        InstrumentId(
            market=Market.CN, exchange=Exchange.XSHE, symbol="159915",
            asset_type=AssetType.ETF, currency=Currency.CNY,
        ),
        InstrumentId(
            market=Market.US, exchange=Exchange.XNAS, symbol="AAPL",
            asset_type=AssetType.STOCK, currency=Currency.USD,
        ),
    )


@pytest.fixture
def history_frame(sample_instruments):
    sh, sz, us = (instrument.canonical_key for instrument in sample_instruments)
    # Deliberately unsorted, with several markets and both date endpoints.
    return pd.DataFrame([
        [us, date(2026, 9, 3), 100.0, 103.0, 99.0, 102.0, 1000],
        [sh, date(2026, 9, 3), 4.0, 4.3, 3.9, 4.2, 1200],
        [sz, date(2026, 9, 2), 2.0, 2.3, 1.9, 2.2, 800],
        [sh, date(2026, 9, 1), 3.8, 4.0, 3.7, 3.9, 1000],
        [sh, date(2026, 9, 2), 3.9, 4.2, 3.8, 4.0, 1100],
    ], columns=HISTORY_COLUMNS)


@pytest.fixture
def sample_dataset(history_frame):
    return MarketDataSet(
        data=history_frame,
        frequency=BarFrequency.DAILY,
        adjustment=AdjustmentMode.RAW,
        provider="fixture",
        generated_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
