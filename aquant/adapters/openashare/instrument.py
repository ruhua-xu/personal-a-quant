"""Translate exchange-qualified OpenAshare A-share identifiers, without I/O."""

import re

from aquant.domain import AssetType, Currency, Exchange, InstrumentId, Market


class OpenAshareInstrumentAdapter:
    """Accept SH/SZ prefixes or .SH/.SZ suffixes, ignoring case and outer space.

    Bare six-digit symbols and US tickers are intentionally rejected: they do
    not supply an exchange. Asset type is always supplied by the caller; this
    adapter does not verify that a symbol exists or infer type from its digits.
    It does not import any OpenAshare helpers or third-party data providers.
    """

    def to_instrument(self, code: str, *, asset_type: AssetType) -> InstrumentId:
        if not isinstance(code, str):
            raise TypeError("code must be a string with an explicit SH/SZ exchange")
        normalized = code.strip().upper()
        prefix = re.fullmatch(r"(SH|SZ)([0-9]{6})", normalized)
        suffix = re.fullmatch(r"([0-9]{6})\.(SH|SZ)", normalized)
        if prefix:
            exchange_code, symbol = prefix.groups()
        elif suffix:
            symbol, exchange_code = suffix.groups()
        else:
            raise ValueError("code must use an explicit SH/SZ prefix or .SH/.SZ suffix")

        return InstrumentId(
            market=Market.CN,
            exchange={"SH": Exchange.XSHG, "SZ": Exchange.XSHE}[exchange_code],
            symbol=symbol,
            asset_type=asset_type,
            currency=Currency.CNY,
        )
