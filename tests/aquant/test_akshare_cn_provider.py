from datetime import date, datetime, timezone
import inspect
from types import SimpleNamespace
from unittest.mock import create_autospec

import pandas as pd
import pytest
import requests
from pydantic import ValidationError

from aquant.data import (
    AdjustmentMode, BarFrequency, HISTORY_COLUMNS, HistoryRequest,
    MarketDataSet, empty_history_frame,
)
from aquant.data.providers import (
    AkShareChinaDataProvider, MarketDataProvider, MarketDataProviderError, ProviderSchemaError,
)
from aquant.domain import AssetType, Currency, Exchange, InstrumentId, Market


def cn_instrument(exchange=Exchange.XSHG, symbol="600000", asset_type=AssetType.STOCK):
    return InstrumentId(
        market=Market.CN, exchange=exchange, symbol=symbol,
        asset_type=asset_type, currency=Currency.CNY,
    )


def request_for(instruments=None, **overrides):
    values = dict(
        instruments=(cn_instrument(),) if instruments is None else instruments,
        start_date=date(2026, 9, 1), end_date=date(2026, 9, 3),
    )
    values.update(overrides)
    return HistoryRequest(**values)


@pytest.fixture
def sina_bars():
    # English columns match the installed public Sina interfaces. ETF prices
    # deliberately retain three decimals; provider-only fields must disappear.
    return pd.DataFrame({
        "date": ["2026-09-03", "2026-08-31", "2026-09-01", "2026-09-04", "2026-09-02"],
        "open": ["4.001"] * 5,
        "close": ["4.123"] * 5,
        "high": ["4.300"] * 5,
        "low": ["3.900"] * 5,
        "volume": ["103", "99", "101", "104", "102"],
        "amount": [1234] * 5,
        "turnover": [0.2] * 5,
        "股票代码": ["wrong-provider-code"] * 5,
        "名称": ["ignored"] * 5,
    }, index=[9, 4, 7, 3, 8])


@pytest.fixture
def mocked_akshare(monkeypatch, sina_bars):
    # Import under the autouse network guard; autospec verifies that our call
    # shape is accepted by the installed AkShare, without invoking either API.
    import akshare as ak

    stock = create_autospec(ak.stock_zh_a_daily, return_value=sina_bars)
    etf = create_autospec(ak.fund_etf_hist_sina, return_value=sina_bars)
    monkeypatch.setattr(ak, "stock_zh_a_daily", stock)
    monkeypatch.setattr(ak, "fund_etf_hist_sina", etf)
    return SimpleNamespace(stock=stock, etf=etf)


@pytest.mark.parametrize("exchange,symbol,asset_type,endpoint,prefix", [
    (Exchange.XSHG, "600000", AssetType.STOCK, "stock", "sh"),
    (Exchange.XSHE, "000001", AssetType.STOCK, "stock", "sz"),
    (Exchange.XSHG, "510300", AssetType.ETF, "etf", "sh"),
    (Exchange.XSHE, "159915", AssetType.ETF, "etf", "sz"),
])
def test_explicit_exchange_asset_routing_and_normalization(mocked_akshare, exchange, symbol, asset_type, endpoint, prefix):
    instrument = cn_instrument(exchange, symbol, asset_type)
    provider = AkShareChinaDataProvider()
    assert isinstance(provider, MarketDataProvider)
    before = datetime.now(timezone.utc)
    result = provider.get_history(request_for([instrument]))
    after = datetime.now(timezone.utc)
    expected_args = {"symbol": prefix + symbol}
    if asset_type is AssetType.STOCK:
        expected_args.update(start_date="20260901", end_date="20260903", adjust="")
    getattr(mocked_akshare, endpoint).assert_called_once_with(**expected_args)
    getattr(mocked_akshare, "etf" if endpoint == "stock" else "stock").assert_not_called()
    assert tuple(result.data.columns) == HISTORY_COLUMNS
    assert result.data["instrument_key"].tolist() == [instrument.canonical_key] * 3
    assert result.data["trade_date"].tolist() == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    assert all(type(value) is date for value in result.data["trade_date"])
    assert result.data["close"].tolist() == [4.123] * 3
    assert result.data["volume"].tolist() == [101, 102, 103]
    assert result.provider == provider.provider_name == "akshare_cn_sina"
    assert result.frequency is BarFrequency.DAILY and result.adjustment is AdjustmentMode.RAW
    assert before <= result.generated_at <= after
    assert result.generated_at.utcoffset().total_seconds() == 0
    MarketDataSet(**{name: getattr(result, name) for name in type(result).model_fields})


@pytest.mark.parametrize("asset_type", list(AssetType))
def test_chinese_alias_contract_does_not_leak_provider_columns(mocked_akshare, sina_bars, asset_type):
    chinese = sina_bars.rename(columns={
        "date": "日期", "open": "开盘", "high": "最高", "low": "最低",
        "close": "收盘", "volume": "成交量", "amount": "成交额", "turnover": "换手率",
    })
    chinese["振幅"] = 1
    chinese["涨跌幅"] = 2
    mocked_akshare.stock.return_value = chinese
    mocked_akshare.etf.return_value = chinese
    result = AkShareChinaDataProvider().get_history(request_for([cn_instrument(asset_type=asset_type)]))
    assert tuple(result.data.columns) == HISTORY_COLUMNS
    assert result.data["open"].eq(4.001).all()
    assert result.data["close"].eq(4.123).all()


def test_exchange_comes_from_input_not_numeric_prefix(mocked_akshare):
    # This is a routing test, not a claim that this identity is a listed stock.
    instrument = cn_instrument(Exchange.XSHE, "600000")
    AkShareChinaDataProvider().get_history(request_for([instrument]))
    assert mocked_akshare.stock.call_args.kwargs["symbol"] == "sz600000"


@pytest.mark.parametrize("updates", [
    {"market": Market.US, "exchange": Exchange.XNAS, "currency": Currency.USD, "symbol": "AAPL"},
    {"exchange": Exchange.XNAS}, {"currency": Currency.USD}, {"asset_type": "BOND"},
    {"symbol": "sh600000"}, {"symbol": "600000.SH"}, {"symbol": "６０００００"}, {"symbol": "../600000"},
])
def test_unsupported_instruments_rejected_before_any_fetch(mocked_akshare, updates):
    invalid = cn_instrument().model_copy(update=updates)
    with pytest.raises(ValueError):
        AkShareChinaDataProvider().get_history(request_for([cn_instrument(), invalid]))
    mocked_akshare.stock.assert_not_called()
    mocked_akshare.etf.assert_not_called()


@pytest.mark.parametrize("frequency", ["MINUTE", "WEEKLY"])
def test_unsupported_frequency_rejected(mocked_akshare, frequency):
    request = request_for().model_copy(update={"frequency": frequency})
    with pytest.raises(ValueError, match="unsupported frequency"):
        AkShareChinaDataProvider().get_history(request)
    mocked_akshare.stock.assert_not_called()


@pytest.mark.parametrize("adjustment", [AdjustmentMode.FORWARD, AdjustmentMode.BACKWARD])
def test_adjusted_history_rejected_without_fallback(mocked_akshare, adjustment):
    with pytest.raises(ValueError, match="unsupported adjustment"):
        AkShareChinaDataProvider().get_history(request_for(adjustment=adjustment))
    mocked_akshare.stock.assert_not_called()
    mocked_akshare.etf.assert_not_called()


def test_legacy_strings_are_not_the_main_interface(mocked_akshare):
    with pytest.raises(TypeError, match="HistoryRequest"):
        AkShareChinaDataProvider().get_history("sh600000")
    with pytest.raises(ValidationError):
        request_for(["sh600000"])
    with pytest.raises(TypeError, match="InstrumentId"):
        AkShareChinaDataProvider().get_history(request_for().model_copy(update={"instruments": ["sh600000"]}))
    mocked_akshare.stock.assert_not_called()


def test_single_date_interval_is_inclusive(mocked_akshare):
    result = AkShareChinaDataProvider().get_history(request_for(start_date=date(2026, 9, 2), end_date=date(2026, 9, 2)))
    assert result.data["trade_date"].tolist() == [date(2026, 9, 2)]


@pytest.mark.parametrize("endpoint", ["stock", "etf"])
@pytest.mark.parametrize("with_columns", [False, True])
def test_empty_dataframe_is_standard_empty_dataset(mocked_akshare, sina_bars, endpoint, with_columns):
    getattr(mocked_akshare, endpoint).return_value = sina_bars.iloc[:0] if with_columns else pd.DataFrame()
    instrument = cn_instrument(asset_type=AssetType.STOCK if endpoint == "stock" else AssetType.ETF)
    result = AkShareChinaDataProvider().get_history(request_for([instrument]))
    pd.testing.assert_frame_equal(result.data, empty_history_frame())
    assert result.provider == "akshare_cn_sina" and result.adjustment is AdjustmentMode.RAW


def test_out_of_range_valid_bars_return_empty(mocked_akshare):
    result = AkShareChinaDataProvider().get_history(request_for(start_date=date(2020, 1, 1), end_date=date(2020, 1, 2)))
    pd.testing.assert_frame_equal(result.data, empty_history_frame())


@pytest.mark.parametrize("response", [None, [], {}, pd.DataFrame(index=[0])])
def test_missing_or_invalid_response_is_not_empty_success(mocked_akshare, response):
    mocked_akshare.stock.return_value = response
    with pytest.raises(ProviderSchemaError):
        AkShareChinaDataProvider().get_history(request_for())


@pytest.mark.parametrize("column", ["date", "open", "high", "low", "close", "volume"])
def test_missing_required_column_reports_schema_error(mocked_akshare, sina_bars, column):
    mocked_akshare.stock.return_value = sina_bars.drop(columns=column)
    with pytest.raises(ProviderSchemaError, match="missing or ambiguous"):
        AkShareChinaDataProvider().get_history(request_for())


@pytest.mark.parametrize("bad_date", ["invalid", "2026-02-30", None, pd.NaT, 20260901, datetime(2026, 9, 1, 10), datetime(2026, 9, 1, tzinfo=timezone.utc), pd.Timestamp("2026-09-01 00:00:00.000000001")])
def test_invalid_dates_fail_explicitly(mocked_akshare, sina_bars, bad_date):
    raw = sina_bars.copy()
    raw["date"] = raw["date"].astype(object)
    raw.loc[raw.index[0], "date"] = bad_date
    mocked_akshare.stock.return_value = raw
    with pytest.raises(ProviderSchemaError, match="invalid historical bars"):
        AkShareChinaDataProvider().get_history(request_for())


@pytest.mark.parametrize("date_factory", [date.fromisoformat, pd.Timestamp])
def test_actual_sina_date_types_are_normalized(mocked_akshare, sina_bars, date_factory):
    raw = sina_bars.copy()
    raw["date"] = raw["date"].map(date_factory)
    mocked_akshare.stock.return_value = raw
    result = AkShareChinaDataProvider().get_history(request_for())
    assert all(type(value) is date for value in result.data["trade_date"])


@pytest.mark.parametrize("column,bad_value", [
    ("open", "bad"), ("high", "bad"), ("low", "bad"), ("close", "bad"), ("volume", "bad"),
    ("close", float("nan")), ("volume", float("inf")), ("open", float("-inf")),
    ("volume", -1), ("close", 100), ("high", 0), ("volume", True),
])
def test_invalid_ohlcv_never_becomes_a_valid_or_empty_result(mocked_akshare, sina_bars, column, bad_value):
    raw = sina_bars.copy()
    raw[column] = raw[column].astype(object)
    raw.loc[raw.index[0], column] = bad_value
    mocked_akshare.stock.return_value = raw
    with pytest.raises(ProviderSchemaError, match="invalid historical bars"):
        AkShareChinaDataProvider().get_history(request_for())


@pytest.mark.parametrize("corruption", ["duplicate_bar", "duplicate_column", "ambiguous_alias"])
def test_duplicate_or_ambiguous_provider_data_is_rejected(mocked_akshare, sina_bars, corruption):
    if corruption == "duplicate_bar":
        raw = pd.concat([sina_bars, sina_bars.iloc[:1]], ignore_index=True)
    elif corruption == "duplicate_column":
        raw = pd.concat([sina_bars, sina_bars[["close"]]], axis=1)
    else:
        raw = sina_bars.assign(收盘=123)
    mocked_akshare.stock.return_value = raw
    with pytest.raises(ProviderSchemaError):
        AkShareChinaDataProvider().get_history(request_for())


@pytest.mark.parametrize("endpoint", ["stock", "etf"])
@pytest.mark.parametrize("error", [requests.ConnectionError("offline"), requests.Timeout("timeout"), RuntimeError("api failure"), KeyError("schema change inside AkShare")])
def test_external_exceptions_preserve_failure_and_cause(mocked_akshare, endpoint, error):
    getattr(mocked_akshare, endpoint).side_effect = error
    instrument = cn_instrument(asset_type=AssetType.STOCK if endpoint == "stock" else AssetType.ETF)
    with pytest.raises(MarketDataProviderError, match="failed for") as caught:
        AkShareChinaDataProvider().get_history(request_for([instrument]))
    assert caught.value.__cause__ is error


def test_failed_second_instrument_does_not_return_partial_success(mocked_akshare):
    mocked_akshare.etf.side_effect = requests.ConnectionError("offline")
    with pytest.raises(MarketDataProviderError):
        AkShareChinaDataProvider().get_history(request_for([cn_instrument(), cn_instrument(symbol="510300", asset_type=AssetType.ETF)]))
    mocked_akshare.stock.assert_called_once()
    mocked_akshare.etf.assert_called_once()


def test_multiple_instruments_and_repeated_identity_are_stable(mocked_akshare):
    stock = cn_instrument()
    etf = cn_instrument(Exchange.XSHE, "159915", AssetType.ETF)
    result = AkShareChinaDataProvider().get_history(request_for([stock, etf, stock]))
    assert len(result.data) == 6
    assert not result.data.duplicated(["instrument_key", "trade_date"]).any()
    assert result.data["instrument_key"].tolist() == sorted([stock.canonical_key] * 3 + [etf.canonical_key] * 3)
    mocked_akshare.stock.assert_called_once()
    mocked_akshare.etf.assert_called_once()


def test_conflicting_metadata_for_same_key_fails_before_fetch(mocked_akshare):
    stock = cn_instrument()
    etf = stock.model_copy(update={"asset_type": AssetType.ETF})
    with pytest.raises(ValueError, match="conflicting"):
        AkShareChinaDataProvider().get_history(request_for([stock, etf]))
    mocked_akshare.stock.assert_not_called()


def test_provider_does_not_mutate_upstream_or_share_frames(mocked_akshare, sina_bars):
    original = sina_bars.copy(deep=True)
    provider = AkShareChinaDataProvider()
    first = provider.get_history(request_for())
    pd.testing.assert_frame_equal(sina_bars, original)
    first.data.loc[:, "volume"] = -1
    second = provider.get_history(request_for())
    assert second.data["volume"].tolist() == [101, 102, 103]
    pd.testing.assert_frame_equal(sina_bars, original)


def test_interface_signatures_are_from_installed_akshare(mocked_akshare):
    assert "adjust" in inspect.signature(mocked_akshare.stock).parameters
    assert list(inspect.signature(mocked_akshare.etf).parameters) == ["symbol"]
