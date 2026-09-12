"""Explicit-exchange, RAW daily AkShare adapter. No storage or scheduling.

Audited against AkShare 1.18.94; interface/RAW evidence and limitations are
recorded in docs/architecture/akshare-cn-provider.md. AkShare is imported
only when a supported request is actually executed.
"""

from datetime import date, datetime, time, timezone
from numbers import Number
import re

import pandas as pd
from pandas.api.types import is_bool

from aquant.data import (
    AdjustmentMode, BarFrequency, HISTORY_COLUMNS, HistoryRequest,
    MarketDataSet, empty_history_frame,
)
from aquant.domain import AssetType, Exchange, InstrumentId
from aquant.markets.china import ChinaEquityRuleBook

from .base import MarketDataProvider, MarketDataProviderError, ProviderSchemaError


_EXCHANGE_PREFIX = {Exchange.XSHG: "sh", Exchange.XSHE: "sz"}
# Sina currently returns English fields. Chinese aliases are an explicit
# normalization contract, not an assumption about the selected API's schema.
_COLUMN_ALIASES = {
    "trade_date": ("date", "日期"),
    "open": ("open", "开盘"),
    "high": ("high", "最高"),
    "low": ("low", "最低"),
    "close": ("close", "收盘"),
    "volume": ("volume", "成交量"),
}


def _daily_date(value: object) -> date:
    """Accept date labels, not guessed numeric epochs or intraday timestamps."""
    if type(value) is date:
        return value
    if isinstance(value, datetime) and not pd.isna(value):
        if value.tzinfo is None and value == datetime.combine(value.date(), time.min):
            return value.date()
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value)
    raise ValueError("trade_date requires a date, ISO date string or naive midnight timestamp")


class AkShareChinaDataProvider(MarketDataProvider):
    """Fetch only requested CN stocks/ETFs, using their explicit exchange.

    DAILY/RAW only. Unsupported requests raise ValueError before any fetch.
    Empty DataFrames mean no bars; None, schema failures and acquisition
    exceptions fail explicitly. A failed instrument fails the whole request,
    never returning a misleading partial dataset. No retries or fallbacks.
    """

    @property
    def provider_name(self) -> str:
        return "akshare_cn_sina"

    def get_history(self, request: HistoryRequest) -> MarketDataSet:
        if not isinstance(request, HistoryRequest):
            raise TypeError("get_history requires a HistoryRequest containing InstrumentId values")
        if request.frequency != BarFrequency.DAILY:
            raise ValueError("unsupported frequency: AkShareChinaDataProvider only supports DAILY")
        if request.adjustment != AdjustmentMode.RAW:
            raise ValueError("unsupported adjustment: AkShareChinaDataProvider only supports RAW")

        # Validate the entire batch before starting external work.
        instruments: dict[str, InstrumentId] = {}
        for instrument in request.instruments:
            if not isinstance(instrument, InstrumentId):
                raise TypeError("instruments must be InstrumentId values, not legacy strings")
            ChinaEquityRuleBook().validate_instrument(instrument)
            if instrument.asset_type not in {AssetType.STOCK, AssetType.ETF}:
                raise ValueError("unsupported asset_type: only STOCK and ETF are supported")
            if not re.fullmatch(r"[0-9]{6}", instrument.symbol):
                raise ValueError("unsupported AkShare symbol: requires six ASCII digits without prefix")
            previous = instruments.setdefault(instrument.canonical_key, instrument)
            if previous != instrument:
                raise ValueError("conflicting instrument metadata for the same canonical_key")

        frames = []
        for instrument in instruments.values():
            response = self._fetch(instrument, request)
            frame = self._normalize(response, instrument)
            selected = frame["trade_date"].between(request.start_date, request.end_date, inclusive="both")
            if selected.any():
                frames.append(frame.loc[selected])
        combined = pd.concat(frames, ignore_index=True) if frames else empty_history_frame()
        return self._dataset(combined)

    @staticmethod
    def _fetch(instrument: InstrumentId, request: HistoryRequest) -> object:
        # Never derive the exchange from the numeric symbol.
        symbol = _EXCHANGE_PREFIX[instrument.exchange] + instrument.symbol
        endpoint = "stock_zh_a_daily" if instrument.asset_type == AssetType.STOCK else "fund_etf_hist_sina"
        try:
            import akshare as ak

            if instrument.asset_type == AssetType.STOCK:
                return ak.stock_zh_a_daily(
                    symbol=symbol, start_date=request.start_date.strftime("%Y%m%d"),
                    end_date=request.end_date.strftime("%Y%m%d"), adjust="",
                )
            # This public endpoint has no date/adjust parameters. Its raw
            # klc_kl.js price path is audited; filter dates locally below.
            return ak.fund_etf_hist_sina(symbol=symbol)
        except Exception as exc:
            raise MarketDataProviderError(
                f"AkShare {endpoint} failed for {instrument.canonical_key}",
            ) from exc

    def _normalize(self, response: object, instrument: InstrumentId) -> pd.DataFrame:
        if not isinstance(response, pd.DataFrame):
            raise ProviderSchemaError(f"{instrument.canonical_key}: expected DataFrame, not {type(response).__name__}")
        if response.columns.has_duplicates:
            raise ProviderSchemaError(f"{instrument.canonical_key}: duplicate provider columns")
        # AkShare legitimately returns DataFrame() without columns for no data.
        # A frame with rows but zero columns is malformed, not an empty result.
        if len(response.index) == 0:
            return empty_history_frame()
        mapping = {}
        for target, aliases in _COLUMN_ALIASES.items():
            matches = [name for name in aliases if name in response.columns]
            if len(matches) != 1:
                raise ProviderSchemaError(
                    f"{instrument.canonical_key}: missing or ambiguous {target} column; expected one of {aliases}",
                )
            mapping[matches[0]] = target
        frame = response.loc[:, list(mapping)].rename(columns=mapping).copy(deep=True)
        try:
            frame["trade_date"] = frame["trade_date"].map(_daily_date)
            for column in ("open", "high", "low", "close", "volume"):
                if frame[column].map(lambda value: is_bool(value) or not isinstance(value, (str, Number))).any():
                    raise ValueError(f"{column} must contain numeric values, not booleans or objects")
                frame[column] = pd.to_numeric(frame[column], errors="raise")
            frame["instrument_key"] = instrument.canonical_key
            # Validate even out-of-range provider rows; do not hide malformed
            # data behind the local date filter. MarketDataSet owns its copy.
            return self._dataset(frame.loc[:, list(HISTORY_COLUMNS)]).data
        except (ValueError, TypeError, OverflowError) as exc:
            raise ProviderSchemaError(f"{instrument.canonical_key}: invalid historical bars: {exc}") from exc

    def _dataset(self, data: pd.DataFrame) -> MarketDataSet:
        return MarketDataSet(
            data=data, frequency=BarFrequency.DAILY, adjustment=AdjustmentMode.RAW,
            provider=self.provider_name, generated_at=datetime.now(timezone.utc),
        )
