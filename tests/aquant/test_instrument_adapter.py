import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from aquant.adapters.openashare import OpenAshareInstrumentAdapter
from aquant.domain import AssetType, Currency, Exchange, Market


@pytest.mark.parametrize("code,asset_type,key", [
    ("sh510300", AssetType.ETF, "CN:XSHG:510300"),
    ("sz159915", AssetType.ETF, "CN:XSHE:159915"),
    ("sh600000", AssetType.STOCK, "CN:XSHG:600000"),
    ("sz000001", AssetType.STOCK, "CN:XSHE:000001"),
    ("600000.SH", AssetType.STOCK, "CN:XSHG:600000"),
    ("000001.SZ", AssetType.STOCK, "CN:XSHE:000001"),
    (" SH510300 ", AssetType.ETF, "CN:XSHG:510300"),
    (" 159915.sz ", AssetType.ETF, "CN:XSHE:159915"),
])
def test_exchange_qualified_codes_map_to_explicit_instrument(code, asset_type, key):
    instrument = OpenAshareInstrumentAdapter().to_instrument(code, asset_type=asset_type)
    assert instrument.canonical_key == key
    assert instrument.market is Market.CN
    assert instrument.currency is Currency.CNY
    assert instrument.asset_type is asset_type


@pytest.mark.parametrize("code", [
    "600000", "510300", "000001", "US.AAPL", "AAPL", "00700.HK",
    "", "  ", "sh60000", "sh6000000", "sh600000.SZ", "SH 600000", "sh６０００００",
])
def test_ambiguous_unsupported_or_malformed_codes_are_rejected(code):
    with pytest.raises(ValueError, match="explicit SH/SZ"):
        OpenAshareInstrumentAdapter().to_instrument(code, asset_type=AssetType.STOCK)


def test_exchange_is_taken_from_explicit_qualifier_not_symbol_digits():
    instrument = OpenAshareInstrumentAdapter().to_instrument("sz600000", asset_type=AssetType.STOCK)
    assert instrument.exchange is Exchange.XSHE
    assert instrument.canonical_key == "CN:XSHE:600000"


def test_asset_type_is_required_and_not_inferred():
    adapter = OpenAshareInstrumentAdapter()
    with pytest.raises(TypeError, match="asset_type"):
        adapter.to_instrument("sh510300")
    assert adapter.to_instrument("sh510300", asset_type=AssetType.STOCK).asset_type is AssetType.STOCK
    with pytest.raises(ValidationError):
        adapter.to_instrument("sh510300", asset_type=None)


def test_adapter_does_not_import_legacy_or_provider_implementations():
    source = Path(__file__).resolve().parents[2] / "aquant" / "adapters" / "openashare" / "instrument.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert set(imports) <= {"re", "aquant.domain"}
