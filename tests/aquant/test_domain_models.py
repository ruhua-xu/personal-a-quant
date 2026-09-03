from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from aquant.domain import (
    Account,
    AssetType,
    CashBalance,
    Currency,
    Exchange,
    InstrumentId,
    ManualExecution,
    ManualOrderPlan,
    ManualOrderStatus,
    Market,
    OrderSide,
    Position,
    RiskSeverity,
    RiskViolation,
    SecurityScore,
    StrategyRun,
    StrategyRunStatus,
    TargetPosition,
)


UTC_NOW = datetime(2026, 9, 3, 1, 2, 3, tzinfo=timezone.utc)
SHANGHAI_NOW = datetime(2026, 9, 3, 9, 2, 3, tzinfo=ZoneInfo("Asia/Shanghai"))


def cn_etf(symbol: str = "510300") -> InstrumentId:
    return InstrumentId(
        market=Market.CN,
        exchange=Exchange.XSHG,
        symbol=symbol,
        asset_type=AssetType.ETF,
        currency=Currency.CNY,
    )


def us_stock(symbol: str = "AAPL") -> InstrumentId:
    return InstrumentId(
        market=Market.US,
        exchange=Exchange.XNAS,
        symbol=symbol,
        asset_type=AssetType.STOCK,
        currency=Currency.USD,
    )


def manual_order_plan(**overrides: object) -> ManualOrderPlan:
    values: dict[str, object] = {
        "plan_id": "plan-1",
        "account_id": "account-cn",
        "strategy_run_id": "run-1",
        "instrument_id": cn_etf(),
        "side": OrderSide.BUY,
        "quantity": Decimal("200"),
        "reference_price": Decimal("4.125"),
        "currency": Currency.CNY,
        "status": ManualOrderStatus.DRAFT,
        "created_at": SHANGHAI_NOW,
        "reason": "rebalance candidate for human review",
    }
    values.update(overrides)
    return ManualOrderPlan(**values)


def manual_execution(**overrides: object) -> ManualExecution:
    values: dict[str, object] = {
        "execution_id": "execution-1",
        "order_plan_id": "plan-1",
        "account_id": "account-cn",
        "instrument_id": cn_etf(),
        "side": OrderSide.BUY,
        "executed_quantity": Decimal("200"),
        "executed_price": Decimal("4.120"),
        "fees": Decimal("0.50"),
        "currency": Currency.CNY,
        "executed_at": SHANGHAI_NOW,
        "external_reference": None,
    }
    values.update(overrides)
    return ManualExecution(**values)


def test_cn_etf_instrument_has_canonical_key_and_trims_symbol() -> None:
    instrument = cn_etf(" 510300 ")

    assert instrument.symbol == "510300"
    assert instrument.canonical_key == "CN:XSHG:510300"


def test_us_stock_instrument_has_canonical_key() -> None:
    assert us_stock().canonical_key == "US:XNAS:AAPL"


def test_same_symbol_in_different_markets_has_distinct_identity() -> None:
    cn_instrument = cn_etf("510300")
    us_instrument = us_stock("510300")

    assert cn_instrument != us_instrument
    assert cn_instrument.canonical_key != us_instrument.canonical_key


def test_empty_instrument_symbol_is_rejected() -> None:
    with pytest.raises(ValidationError):
        cn_etf("   ")


def test_account_supports_non_cny_base_currency_without_broker_sdk() -> None:
    account = Account(
        account_id="account-us",
        display_name="US research account",
        base_currency=Currency.USD,
        broker="  Manual Broker Label  ",
    )

    assert account.base_currency is Currency.USD
    assert account.broker == "Manual Broker Label"


def test_cash_balance_preserves_decimal_precision() -> None:
    precise_sum = Decimal("0.1") + Decimal("0.2")
    balance = CashBalance(
        account_id="account-cn",
        currency=Currency.CNY,
        settled_cash=precise_sum,
        available_cash=Decimal("0.3"),
        frozen_cash=Decimal("0"),
    )

    assert isinstance(balance.settled_cash, Decimal)
    assert balance.settled_cash == Decimal("0.3")


def test_position_rejects_available_quantity_above_quantity() -> None:
    with pytest.raises(ValidationError, match="available_quantity cannot exceed quantity"):
        Position(
            account_id="account-cn",
            instrument_id=cn_etf(),
            quantity=Decimal("100"),
            available_quantity=Decimal("101"),
            average_cost=Decimal("4.00"),
            cost_currency=Currency.CNY,
        )


def test_position_rejects_negative_quantity() -> None:
    with pytest.raises(ValidationError):
        Position(
            account_id="account-cn",
            instrument_id=cn_etf(),
            quantity=Decimal("-1"),
            available_quantity=Decimal("0"),
            average_cost=Decimal("4.00"),
            cost_currency=Currency.CNY,
        )


@pytest.mark.parametrize("weight", [Decimal("-0.01"), Decimal("1.01")])
def test_target_position_rejects_weight_outside_long_only_range(weight: Decimal) -> None:
    with pytest.raises(ValidationError):
        TargetPosition(
            account_id="account-cn",
            strategy_run_id="run-1",
            instrument_id=cn_etf(),
            target_weight=weight,
        )


def test_target_position_preserves_decimal_weight() -> None:
    target = TargetPosition(
        account_id="account-cn",
        strategy_run_id="run-1",
        instrument_id=cn_etf(),
        target_weight=Decimal("0.30"),
    )

    assert target.target_weight == Decimal("0.30")
    assert isinstance(target.target_weight, Decimal)


def test_security_score_accepts_utc_aware_datetime() -> None:
    score = SecurityScore(
        strategy_key="quality",
        strategy_version="1.0",
        instrument_id=cn_etf(),
        score=Decimal("88.5"),
        as_of=UTC_NOW,
    )

    assert score.as_of == UTC_NOW


def test_security_score_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        SecurityScore(
            strategy_key="quality",
            strategy_version="1.0",
            instrument_id=cn_etf(),
            score=Decimal("88.5"),
            as_of=datetime(2026, 9, 3, 1, 2, 3),
        )


def test_strategy_run_accepts_shanghai_aware_datetimes() -> None:
    run = StrategyRun(
        run_id="run-1",
        strategy_key="quality",
        strategy_version="1.0",
        status=StrategyRunStatus.CREATED,
        as_of=SHANGHAI_NOW,
        created_at=UTC_NOW,
        data_version="cn-etf-2026-09-02-v1",
        parameters={"top_n": 10},
    )

    assert run.as_of == SHANGHAI_NOW
    assert run.data_version == "cn-etf-2026-09-02-v1"
    assert run.parameters == {"top_n": 10}


@pytest.mark.parametrize("field", ["as_of", "created_at"])
def test_strategy_run_rejects_naive_datetimes(field: str) -> None:
    values = {
        "run_id": "run-1",
        "strategy_key": "quality",
        "strategy_version": "1.0",
        "status": StrategyRunStatus.CREATED,
        "as_of": SHANGHAI_NOW,
        "created_at": UTC_NOW,
        "data_version": "cn-etf-2026-09-02-v1",
        "parameters": {},
    }
    values[field] = datetime(2026, 9, 3, 1, 2, 3)

    with pytest.raises(ValidationError):
        StrategyRun(**values)


def test_manual_order_plan_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        manual_order_plan(created_at=datetime(2026, 9, 3, 9, 2, 3))


def test_manual_order_plan_estimated_amount_is_decimal_and_read_only() -> None:
    plan = manual_order_plan()

    assert plan.estimated_amount == Decimal("825.000")
    assert isinstance(plan.estimated_amount, Decimal)

    with pytest.raises(ValidationError):
        manual_order_plan(estimated_amount=Decimal("1"))


def test_manual_execution_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        manual_execution(executed_at=datetime(2026, 9, 3, 9, 2, 3))


def test_manual_execution_rejects_negative_fee() -> None:
    with pytest.raises(ValidationError):
        manual_execution(fees=Decimal("-0.01"))


def test_risk_violation_keeps_structured_metadata() -> None:
    violation = RiskViolation(
        code="POSITION_LIMIT",
        severity=RiskSeverity.BLOCK,
        message="Target position exceeds the configured limit",
        field="target_weight",
        metadata={"limit": "0.25"},
    )

    assert violation.severity is RiskSeverity.BLOCK
    assert violation.metadata == {"limit": "0.25"}
