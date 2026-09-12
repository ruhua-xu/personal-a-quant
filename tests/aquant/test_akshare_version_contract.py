from importlib.metadata import PackageNotFoundError
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from unittest.mock import Mock

import pandas as pd
import pytest

from aquant.data.providers import AkShareChinaDataProvider, MarketDataProviderError
from aquant.data.providers import akshare_cn
from tests.aquant.test_akshare_cn_provider import request_for


@pytest.fixture
def fake_akshare(monkeypatch):
    # This version-contract suite does not require an actual AkShare install.
    module = ModuleType("akshare")
    module.stock_zh_a_daily = Mock(return_value=pd.DataFrame())
    module.fund_etf_hist_sina = Mock(return_value=pd.DataFrame())
    monkeypatch.setitem(sys.modules, "akshare", module)
    return module


def test_audited_version_is_checked_on_each_request(monkeypatch, fake_akshare):
    lookup = Mock(return_value="1.18.94")
    monkeypatch.setattr(akshare_cn, "distribution_version", lookup)
    provider = AkShareChinaDataProvider()
    assert provider.get_history(request_for()).data.empty
    lookup.assert_called_once_with("akshare")
    fake_akshare.stock_zh_a_daily.assert_called_once()

    # A cached success must not allow another version on a later request.
    lookup.return_value = "1.18.95"
    with pytest.raises(MarketDataProviderError, match="1.18.95"):
        provider.get_history(request_for())
    assert lookup.call_count == 2
    assert fake_akshare.stock_zh_a_daily.call_count == 1


@pytest.mark.parametrize("installed", ["1.17.24", "1.18.93", "1.18.95", "2.0.0", "1.18.94rc1", "1.18.94+local"])
def test_other_versions_fail_before_fetch(monkeypatch, fake_akshare, installed):
    monkeypatch.setattr(akshare_cn, "distribution_version", Mock(return_value=installed))
    with pytest.raises(MarketDataProviderError) as caught:
        AkShareChinaDataProvider().get_history(request_for())
    message = str(caught.value)
    assert f"installed={installed!r}" in message
    assert "supported/tested=1.18.94" in message
    fake_akshare.stock_zh_a_daily.assert_not_called()
    fake_akshare.fund_etf_hist_sina.assert_not_called()


@pytest.mark.parametrize("error,expected", [
    (PackageNotFoundError("akshare"), "installed=<not installed>"),
    (OSError("unreadable metadata"), "installed=<unknown>"),
    (ValueError("malformed metadata"), "installed=<unknown>"),
])
def test_missing_or_unreadable_metadata_is_explicit(monkeypatch, fake_akshare, error, expected):
    monkeypatch.setattr(akshare_cn, "distribution_version", Mock(side_effect=error))
    with pytest.raises(MarketDataProviderError) as caught:
        AkShareChinaDataProvider().get_history(request_for())
    assert expected in str(caught.value)
    assert "supported/tested=1.18.94" in str(caught.value)
    assert caught.value.__cause__ is error
    fake_akshare.stock_zh_a_daily.assert_not_called()
    fake_akshare.fund_etf_hist_sina.assert_not_called()


@pytest.mark.parametrize("value", [None, ""])
def test_empty_version_metadata_is_rejected(monkeypatch, fake_akshare, value):
    monkeypatch.setattr(akshare_cn, "distribution_version", Mock(return_value=value))
    with pytest.raises(MarketDataProviderError, match="Cannot read AkShare version") as caught:
        AkShareChinaDataProvider().get_history(request_for())
    assert "supported/tested=1.18.94" in str(caught.value)
    fake_akshare.stock_zh_a_daily.assert_not_called()


def test_version_mismatch_does_not_even_import_akshare(monkeypatch):
    import builtins

    original_import = builtins.__import__
    imports = []

    def guarded_import(name, *args, **kwargs):
        if name == "akshare" or name.startswith("akshare."):
            imports.append(name)
            raise AssertionError("version must be checked before AkShare import")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(akshare_cn, "distribution_version", Mock(return_value="1.17.24"))
    with pytest.raises(MarketDataProviderError, match="Unsupported AkShare version"):
        AkShareChinaDataProvider().get_history(request_for())
    assert imports == []


def test_fresh_package_import_and_construction_work_without_akshare():
    # A fresh interpreter avoids already-imported modules masking an eager
    # import regression. Block external access in the child as well.
    script = """
import builtins
from importlib import metadata
import socket

def no_network(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.create_connection = socket.getaddrinfo = no_network
socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = no_network

original_import = builtins.__import__
def without_akshare(name, *args, **kwargs):
    if name == 'akshare' or name.startswith('akshare.'):
        raise ModuleNotFoundError('AkShare intentionally unavailable')
    return original_import(name, *args, **kwargs)
builtins.__import__ = without_akshare

original_version = metadata.version
def without_akshare_version(name):
    if name == 'akshare':
        raise AssertionError('version metadata must remain lazy')
    return original_version(name)
metadata.version = without_akshare_version

import aquant.data.providers as providers
assert providers.AkShareChinaDataProvider().provider_name == 'akshare_cn_sina'
assert providers.FakeMarketDataProvider is not None
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("filename", ["requirements_api.txt", "requirements.txt", "requirements_mac.txt"])
def test_all_requirements_lock_the_audited_version(filename):
    root = Path(__file__).resolve().parents[2]
    lines = (root / filename).read_text(encoding="utf-8").splitlines()
    declarations = [line.strip() for line in lines if line.strip().lower().startswith("akshare")]
    assert declarations == ["akshare==1.18.94"]
    assert akshare_cn.SUPPORTED_AKSHARE_VERSION == "1.18.94"
