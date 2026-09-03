"""Abstract boundary for market-specific validation rules."""

from abc import ABC, abstractmethod
from datetime import tzinfo
from decimal import Decimal

from aquant.domain import Currency, InstrumentId, ManualOrderPlan, Market


class MarketRuleBook(ABC):
    """Minimal market-rule contract used by future order-planning use cases.

    Implementations validate domain values and raise ``ValueError`` when a
    value violates a rule they explicitly support. Rules not represented by
    this interface are not implied or guessed.
    """

    @property
    @abstractmethod
    def market(self) -> Market:
        """Market governed by this rulebook."""

    @property
    @abstractmethod
    def timezone(self) -> tzinfo:
        """Timezone used by this market."""

    @property
    @abstractmethod
    def base_currency(self) -> Currency:
        """Default currency used by this market rulebook."""

    @abstractmethod
    def validate_instrument(self, instrument: InstrumentId) -> None:
        """Validate that an instrument belongs to the supported market."""

    @abstractmethod
    def validate_quantity(self, quantity: Decimal) -> None:
        """Validate only quantity rules explicitly supported by the rulebook."""

    @abstractmethod
    def validate_order_plan(self, order_plan: ManualOrderPlan) -> None:
        """Validate the supported market constraints on a manual plan."""
