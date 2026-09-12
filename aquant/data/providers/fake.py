"""An in-memory provider for deterministic fixtures; no generated or live bars."""

from aquant.data.models import HistoryRequest, MarketDataSet

from .base import MarketDataProvider


class FakeMarketDataProvider(MarketDataProvider):
    """Filter a validated snapshot of caller-supplied bars.

    Frequency and adjustment must match the fixture; no resampling or price
    adjustment is performed. A copy is retained to isolate later edits to the
    caller's frame. Each response owns another copy. ``generated_at`` retains
    the fixture timestamp, so repeated requests have deterministic metadata
    and do not give old fixture data an apparently fresh timestamp.
    """

    def __init__(self, dataset: MarketDataSet) -> None:
        # Reconstruct to validate even if the caller has edited dataset.data.
        self._dataset = MarketDataSet(
            data=dataset.data,
            frequency=dataset.frequency,
            adjustment=dataset.adjustment,
            provider=dataset.provider,
            generated_at=dataset.generated_at,
        )

    @property
    def provider_name(self) -> str:
        return "fake"

    def get_history(self, request: HistoryRequest) -> MarketDataSet:
        if request.frequency != self._dataset.frequency:
            raise ValueError("requested frequency does not match the fixture frequency")
        if request.adjustment != self._dataset.adjustment:
            raise ValueError("requested adjustment does not match the fixture adjustment")

        keys = {instrument.canonical_key for instrument in request.instruments}
        frame = self._dataset.data
        selected = frame["instrument_key"].isin(keys) & frame["trade_date"].between(
            request.start_date, request.end_date, inclusive="both",
        )
        return MarketDataSet(
            data=frame.loc[selected],
            frequency=self._dataset.frequency,
            adjustment=self._dataset.adjustment,
            provider=self.provider_name,
            generated_at=self._dataset.generated_at,
        )
