import pandas as pd
import pytest
import requests

from aquant.data import HISTORY_COLUMNS, empty_history_frame
from aquant.data.providers import AkShareChinaDataProvider, MarketDataProviderError
from aquant.data.storage import DuckDBHistoryReader, ParquetHistoryStore
from aquant.domain import AssetType, Exchange

from tests.aquant.test_akshare_cn_provider import (
    cn_instrument, mocked_akshare, request_for, sina_bars,
)


def test_mock_akshare_to_parquet_to_duckdb_preserves_all_bars(tmp_path, mocked_akshare):
    request = request_for([
        cn_instrument(Exchange.XSHG, "600000", AssetType.STOCK),
        cn_instrument(Exchange.XSHE, "000001", AssetType.STOCK),
        cn_instrument(Exchange.XSHG, "510300", AssetType.ETF),
        cn_instrument(Exchange.XSHE, "159915", AssetType.ETF),
    ])
    standardized = AkShareChinaDataProvider().get_history(request)
    expected = standardized.data.copy(deep=True)
    root = tmp_path / "local-history"
    store = ParquetHistoryStore(root)
    store.write(standardized)
    store.write(standardized)
    restored = DuckDBHistoryReader(root).read(request)
    pd.testing.assert_frame_equal(restored.data, expected)
    pd.testing.assert_frame_equal(standardized.data, expected)
    assert tuple(restored.data.columns) == HISTORY_COLUMNS
    assert len(restored.data) == 12
    assert not restored.data.duplicated(["instrument_key", "trade_date"]).any()
    assert len(list(root.rglob("*.parquet"))) == 4
    assert restored.provider == "local_parquet"
    assert standardized.provider == "akshare_cn_sina"
    assert restored.frequency == standardized.frequency
    assert restored.adjustment == standardized.adjustment
    assert mocked_akshare.stock.call_count == mocked_akshare.etf.call_count == 2


def test_empty_provider_result_stays_empty_through_store(tmp_path, mocked_akshare):
    mocked_akshare.stock.return_value = pd.DataFrame()
    request = request_for()
    dataset = AkShareChinaDataProvider().get_history(request)
    ParquetHistoryStore(tmp_path).write(dataset)
    result = DuckDBHistoryReader(tmp_path).read(request)
    pd.testing.assert_frame_equal(result.data, empty_history_frame())
    assert list(tmp_path.iterdir()) == []


def test_provider_failure_cannot_be_persisted_as_empty_history(tmp_path, mocked_akshare):
    mocked_akshare.stock.side_effect = requests.ConnectionError("offline fixture")
    with pytest.raises(MarketDataProviderError):
        dataset = AkShareChinaDataProvider().get_history(request_for())
        ParquetHistoryStore(tmp_path).write(dataset)
    assert list(tmp_path.iterdir()) == []
