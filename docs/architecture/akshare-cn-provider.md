# Phase 2C: AkShare CN daily adapter

## Interface audit

The current Provider contract is locked to AkShare **1.18.94**.
`requirements_api.txt`, `requirements.txt` and `requirements_mac.txt` all pin
`akshare==1.18.94`. No other dependency versions or legacy data implementations
were changed for this lock.

Each valid `get_history` request checks locally installed distribution metadata
before importing/calling AkShare. Only the exact version `1.18.94` is accepted,
including when AkShare is already imported in the process. A version mismatch,
missing installation or unreadable version raises `MarketDataProviderError`
with installed and supported/tested version information. The check is not cached.
Importing `aquant.data.providers` and constructing the provider do not import
AkShare or read its version metadata, so they work without AkShare installed.

An AkShare upgrade must re-audit the selected interfaces, re-run schema and RAW
semantic verification (including the ETF raw endpoint evidence below), and run
the offline provider/store and legacy regression tests. Change the three pins,
runtime version guard and documentation together only after that verification;
passing mocked conversion tests alone does not establish new upstream semantics.

The legacy module uses `stock_zh_a_hist(..., adjust="qfq")`. That is not reused:
in the inspected version, `stock_zh_a_hist` guesses the exchange from the symbol,
and `fund_etf_hist_em` also determines/falls back between exchanges internally.
The new adapter uses public interfaces that accept an explicit exchange prefix:

| Asset | AkShare interface | Parameters / RAW meaning |
| --- | --- | --- |
| STOCK | `stock_zh_a_daily` | `symbol=sh/sz + symbol`, `start_date/end_date=YYYYMMDD`, `adjust=""` explicitly selects unadjusted prices |
| ETF | `fund_etf_hist_sina` | `symbol=sh/sz + symbol`; no date or adjustment parameters exist |

ETF RAW mapping is based on the inspected implementation: it decodes Sina's
`realstock/company/{symbol}/hisdata_klc2/klc_kl.js`, the same raw price endpoint
used by `stock_zh_a_daily` before its explicit adjustment branches. The ETF path
only converts types and sorts dates; it applies no adjustment factors. This is
a source-code-based interpretation, not a claim that the ETF docs explicitly
promise an `adjust` option. Changes to this path require rechecking RAW semantics.

References: [stock interface](https://akshare.akfamily.xyz/data/stock/stock.html),
[ETF interface](https://akshare.akfamily.xyz/data/fund/fund_public.html),
[stock source](https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_zh_a_sina.py),
[ETF source](https://github.com/akfamily/akshare/blob/main/akshare/fund/fund_etf_sina.py).

## Input and output boundary

`get_history(HistoryRequest)` accepts only explicit `InstrumentId` values.
ChinaEquityRuleBook checks CN, XSHG/XSHE and CNY. The adapter checks STOCK/ETF,
six ASCII digits, DAILY and RAW. XSHG maps to `sh`, XSHE maps to `sz`; symbol
digits never decide the exchange. The entire request is checked before fetching.
FORWARD/BACKWARD and all unsupported frequencies fail with ValueError.

Both selected APIs currently return English columns. The adapter also defines
the requested Chinese aliases as a normalization contract, tested independently
of the actual English interface fixtures (not as a claim about Sina's output).

| Standard column | Accepted provider column |
| --- | --- |
| instrument_key | Explicit input InstrumentId.canonical_key; never copied from provider labels |
| trade_date | date / 日期 |
| open | open / 开盘 |
| high | high / 最高 |
| low | low / 最低 |
| close | close / 收盘 |
| volume | volume / 成交量 |

Each field must have exactly one alias. Extra fields (amount, turnover, names,
provider codes, etc.) are discarded. Dates become Python date; numeric fields
are converted without filling missing values or manufacturing bars. Results
are validated and stably sorted using MarketDataSet and locally filtered with
both date endpoints inclusive. Metadata is `provider=akshare_cn_sina` and a UTC
aware generation time. The adapter does not persist data.

## Errors and limits

- Zero-row DataFrame: standard empty dataset; filtered-out valid bars: empty.
- None/non-DataFrame, missing/ambiguous columns, malformed dates/OHLCV,
  duplicates or failed MarketDataSet invariants: ProviderSchemaError.
- Network errors, unavailable functions/imports and other AkShare exceptions:
  MarketDataProviderError, with the original exception retained as its cause.
- A failed instrument fails the complete request, never a partial success.
- Volume is preserved numerically in the selected source's units, not silently
  scaled. Stock docs describe shares; ETF docs label lots. No uniform volume
  unit across assets/providers is asserted; review this before volume-based
  research. This phase does not change the existing data contract.
- Sina ETF returns all available history for one requested instrument; only
  the requested interval is returned by the adapter. There is no universe scan,
  background download, retry/fallback service or scheduler. Neither selected
  public API exposes a timeout argument; this adapter does not patch AkShare's
  HTTP transport or claim a bounded request duration.
- Upstream stock processing includes rounding/filling/deduplication. This adapter
  validates the returned data, not underlying website completeness or accuracy.
- Default tests mock AkShare and block network access; no live availability test
  or historical-data download was performed during this phase.
