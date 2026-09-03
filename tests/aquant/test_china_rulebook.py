import ast
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aquant.domain import (
    AssetType,
    Currency,
    Exchange,
    InstrumentId,
    ManualOrderPlan,
    ManualOrderStatus,
    Market,
    OrderSide,
)
from aquant.markets import ChinaEquityRuleBook, MarketRuleBook


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_IMPORT_ROOTS = {
    "akshare",
    "akquant",
    "api",
    "ashare",
    "fastapi",
    "finnhub",
    "react",
    "typescript",
    "yfinance",
}


def cn_etf() -> InstrumentId:
    return InstrumentId(
        market=Market.CN,
        exchange=Exchange.XSHG,
        symbol="510300",
        asset_type=AssetType.ETF,
        currency=Currency.CNY,
    )


def us_stock() -> InstrumentId:
    return InstrumentId(
        market=Market.US,
        exchange=Exchange.XNAS,
        symbol="AAPL",
        asset_type=AssetType.STOCK,
        currency=Currency.USD,
    )


def test_china_rulebook_exposes_explicit_market_defaults() -> None:
    rulebook = ChinaEquityRuleBook()

    assert isinstance(rulebook, MarketRuleBook)
    assert rulebook.market is Market.CN
    assert rulebook.base_currency is Currency.CNY
    assert getattr(rulebook.timezone, "key", None) == "Asia/Shanghai"


def test_china_rulebook_accepts_cn_instrument() -> None:
    ChinaEquityRuleBook().validate_instrument(cn_etf())


def test_china_rulebook_rejects_us_instrument() -> None:
    with pytest.raises(ValueError, match="requires market=CN"):
        ChinaEquityRuleBook().validate_instrument(us_stock())


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-1")])
def test_china_rulebook_rejects_non_positive_quantity(quantity: Decimal) -> None:
    with pytest.raises(ValueError, match="positive"):
        ChinaEquityRuleBook().validate_quantity(quantity)


def test_china_rulebook_accepts_positive_whole_share_quantity() -> None:
    ChinaEquityRuleBook().validate_quantity(Decimal("1"))


def test_china_rulebook_rejects_fractional_share_quantity() -> None:
    with pytest.raises(ValueError, match="fractional"):
        ChinaEquityRuleBook().validate_quantity(Decimal("1.5"))


def test_china_rulebook_validates_only_supported_manual_plan_rules() -> None:
    plan = ManualOrderPlan(
        plan_id="plan-1",
        account_id="account-cn",
        strategy_run_id="run-1",
        instrument_id=cn_etf(),
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        reference_price=Decimal("4.123"),
        currency=Currency.CNY,
        status=ManualOrderStatus.PENDING_REVIEW,
        created_at=datetime(2026, 9, 3, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        reason="human review required",
    )

    ChinaEquityRuleBook().validate_order_plan(plan)


def test_domain_and_markets_do_not_import_forbidden_dependencies() -> None:
    violations: list[str] = []
    source_roots = [PROJECT_ROOT / "aquant" / "domain", PROJECT_ROOT / "aquant" / "markets"]

    for source_root in source_roots:
        for source_file in source_root.rglob("*.py"):
            tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
            for node in ast.walk(tree):
                imported_roots: list[str] = []
                if isinstance(node, ast.Import):
                    imported_roots = [alias.name.split(".", 1)[0].lower() for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_roots = [node.module.split(".", 1)[0].lower()]

                for imported_root in imported_roots:
                    if imported_root in FORBIDDEN_IMPORT_ROOTS:
                        violations.append(f"{source_file.relative_to(PROJECT_ROOT)}: {imported_root}")

    assert violations == []
