"""Initial, deliberately incomplete rule boundary for Chinese equities."""

from datetime import tzinfo
from decimal import Decimal
from zoneinfo import ZoneInfo

from aquant.domain import Currency, InstrumentId, ManualOrderPlan, Market
from aquant.markets.base import MarketRuleBook


class ChinaEquityRuleBook(MarketRuleBook):
    """Validate a small set of reliable Chinese equity constraints.

    This is not a production trading-rules engine. It intentionally does not
    model board-lot sizes, T+1 settlement, price limits, or trading sessions.
    Those rules require explicit instrument-level data in a later phase.
    """

    @property
    def market(self) -> Market:
        return Market.CN

    @property
    def timezone(self) -> tzinfo:
        return ZoneInfo("Asia/Shanghai")

    @property
    def base_currency(self) -> Currency:
        return Currency.CNY

    def validate_instrument(self, instrument: InstrumentId) -> None:
        if instrument.market is not self.market:
            raise ValueError(
                f"ChinaEquityRuleBook requires market={self.market.value}; "
                f"received {instrument.market.value}"
            )

    def validate_quantity(self, quantity: Decimal) -> None:
        if not isinstance(quantity, Decimal):
            raise TypeError("quantity must be a Decimal")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if quantity != quantity.to_integral_value():
            raise ValueError("fractional share quantities are not supported")

    def validate_order_plan(self, order_plan: ManualOrderPlan) -> None:
        self.validate_instrument(order_plan.instrument_id)
        self.validate_quantity(order_plan.quantity)
        if order_plan.currency is not self.base_currency:
            raise ValueError(
                f"ChinaEquityRuleBook requires currency={self.base_currency.value}; "
                f"received {order_plan.currency.value}"
            )
