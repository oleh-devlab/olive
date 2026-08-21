"""Shared builders for the dicts the inflation library hands the bot.

`inflation_formatter` and `inflation_report` are tested against the same three
shapes — a described record, a group node, a whole report — so the fixtures live
here rather than being kept in step by hand in two files.

Nothing here imports `settings` or touches the filesystem: `test_inflation_formatter`
runs without either, and importing these must not change that.
"""

import datetime
import sys
import unittest
from decimal import Decimal
from pathlib import Path

# Setup path so we can import from src
# TODO: fix paths
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from modules.inflation_formatter import LOT_SOURCE_DEPOSIT_INTEREST  # noqa: E402

JAN = datetime.date(2024, 1, 15)


def make_record(
    record_id: int,
    amount: str,
    date: datetime.date = JAN,
    comment: str = "",
    group_id: int | None = None,
) -> dict:
    """One row of a node's `records`, as `describe_record` returns it."""
    return {
        "id": record_id,
        "amount": Decimal(amount),
        "date": date,
        "comment": comment,
        "group_id": group_id,
        "source": "manual",
        "count": 1,
        "adjusted_value": Decimal(amount) * Decimal("1.5"),
        "loss_percent": Decimal("50"),
    }


def make_folded_record(amount: str, first: datetime.date, last: datetime.date, count: int = 12) -> dict:
    """The summary row `collapse_interest_rows` puts in place of several lots.

    Its `id` is present and None — the trap `format_record` has to sidestep.
    """
    return {
        "id": None,
        "amount": Decimal(amount),
        "date": first,
        "first_date": first,
        "last_date": last,
        "comment": f"deposit interest x{count}",
        "group_id": 1,
        "source": LOT_SOURCE_DEPOSIT_INTEREST,
        "count": count,
        "adjusted_value": Decimal(amount) * Decimal("1.5"),
        "loss_percent": Decimal("50"),
    }


def make_deposit(matured: bool = False) -> dict:
    """The `deposit` block `describe_group_deposit` hangs on a group node.

    The effective rate carries a third decimal on purpose: rendering it is
    supposed to round, and a value that needs no rounding would not prove it.
    """
    return {
        "annual_rate_percent": Decimal("15"),
        "capitalization": "monthly",
        "start_date": datetime.date(2025, 1, 1),
        "end_date": datetime.date(2026, 1, 1),
        "comment": "Monobank",
        "matured": matured,
        "net_interest_so_far": Decimal("1234.5"),
        "balance_so_far": Decimal("101234.5"),
        "projected_net_interest": Decimal("12687.23"),
        "projected_final_amount": Decimal("112687.23"),
        "effective_annual_rate_percent": Decimal("16.075"),
        "at_risk_if_broken_now": Decimal("1000"),
    }


def make_node(name: str, records: list[dict], group_id: int | None = 1, deposit: dict | None = None) -> dict:
    """A group node in the shape `InflationCalculator.get_groups_report()` returns."""
    return {
        "id": group_id,
        "name": name,
        "comment": "",
        "records_count": len(records),
        "total_nominal": sum((r["amount"] for r in records), Decimal("0")),
        "total_adjusted": sum((r["adjusted_value"] for r in records), Decimal("0")),
        "loss_percent": Decimal("50"),
        "oldest_date": min((r["date"] for r in records), default=None),
        "records": records,
        "deposit": deposit,
    }


def make_report(groups: list[dict], ungrouped: list[dict] | None = None) -> dict:
    """A report in the shape `InflationCalculator.get_groups_report()` returns."""
    ungrouped_node = make_node("(ungrouped)", ungrouped or [], group_id=None)
    nodes = [*groups, ungrouped_node]

    return {
        "total_nominal": sum((n["total_nominal"] for n in nodes), Decimal("0")),
        "total_adjusted": sum((n["total_adjusted"] for n in nodes), Decimal("0")),
        "loss_percent": Decimal("50"),
        "oldest_date": min((n["oldest_date"] for n in nodes if n["oldest_date"]), default=None),
        "groups": groups,
        "ungrouped": ungrouped_node,
    }


def make_consumed(record_id, date: datetime.date, taken: str, remaining: str, source: str = "manual") -> dict:
    """One entry of the `consumed` list `InflationCalculator.withdraw()` returns."""
    return {
        "id": record_id,
        "date": date,
        "source": source,
        "taken": Decimal(taken),
        "remaining": Decimal(remaining),
    }


def make_withdrawal(consumed: list[dict], warning: str | None = None) -> dict:
    """What `InflationCalculator.withdraw()` returns."""
    return {
        "amount": sum((entry["taken"] for entry in consumed), Decimal("0")),
        "group_id": 1,
        "consumed": consumed,
        "warning": warning,
    }


class FakeProvider:
    """Every provider call the rendering layers make, with no storage behind them."""

    def __init__(self, report: dict | None = None, has_rates: bool = True, gaps: list[str] | None = None):
        self.report = report or make_report([])
        self.has_rates = has_rates
        self.gaps = gaps or []

    def get_rate_status(self) -> tuple[bool, list[str]]:
        return self.has_rates, self.gaps

    def get_groups_report(self, owner_id, scope="user", *, detailed=True, collapse_interest=True) -> dict:
        return self.report

    def get_view_mode(self, owner_id, scope="user") -> str:
        return "tree"

    def get_collapse_interest(self, owner_id, scope="user") -> bool:
        return True


class RenderingTestCase(unittest.TestCase):
    """Swaps in a fake provider for the duration of one test.

    `inflation_report` and `inflation_replies` each hold their own reference to
    the provider singleton, so a subclass names the modules its tests reach
    through and every one of them is patched together.
    """

    modules: tuple = ()

    def use(self, provider: FakeProvider) -> FakeProvider:
        for module in self.modules:
            original = module.inflation_provider
            module.inflation_provider = provider
            self.addCleanup(setattr, module, "inflation_provider", original)

        return provider
