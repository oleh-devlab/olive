import datetime
import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path

# Setup path so we can import from src
# TODO: fix paths
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

# `inflation_provider` reads `settings` at call time, and the module is created
# by the operator, so it does not exist in a checkout. Nothing here reaches the
# filesystem — the provider itself is replaced below — but the import has to
# resolve, so a bare stub stands in for it.
sys.modules.setdefault("settings", types.ModuleType("settings"))

from modules import inflation_report  # noqa: E402
from modules.inflation_report import (  # noqa: E402
    MESSAGE_LIMIT,
    VIEW_SUMMARY,
    VIEW_TREE,
    build_group_list,
    build_rates_warning,
    build_summary,
    build_view_pages,
)

JAN = datetime.date(2024, 1, 15)


def make_record(record_id: int, amount: str, comment: str = "") -> dict:
    return {
        "id": record_id,
        "amount": Decimal(amount),
        "date": JAN,
        "comment": comment,
        "group_id": None,
        "adjusted_value": Decimal(amount) * Decimal("1.5"),
        "loss_percent": Decimal("50"),
    }


def make_node(name: str, records: list[dict], group_id: int | None = 1) -> dict:
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


class FakeProvider:
    """The two provider calls this module makes, without any storage behind them."""

    def __init__(self, report: dict | None = None, has_rates: bool = True, gaps: list[str] | None = None):
        self.report = report or make_report([])
        self.has_rates = has_rates
        self.gaps = gaps or []

    def get_rate_status(self) -> tuple[bool, list[str]]:
        return self.has_rates, self.gaps

    def get_groups_report(self, owner_id, scope="user", *, detailed=True) -> dict:
        return self.report

    def get_view_mode(self, owner_id, scope="user") -> str:
        return VIEW_TREE


class ReportTestCase(unittest.TestCase):
    """Swaps in a fake provider for the duration of one test."""

    def use(self, provider: FakeProvider) -> FakeProvider:
        original = inflation_report.inflation_provider
        inflation_report.inflation_provider = provider
        self.addCleanup(setattr, inflation_report, "inflation_provider", original)

        return provider


class TestBuildRatesWarning(ReportTestCase):
    """The CPI warning is the only thing telling the reader the numbers are estimates."""

    def test_no_data_at_all_is_its_own_warning(self):
        self.use(FakeProvider(has_rates=False, gaps=[]))
        warning = build_rates_warning()

        self.assertIn("No CPI data at all", warning)

    def test_complete_history_says_nothing(self):
        self.use(FakeProvider(has_rates=True, gaps=[]))

        self.assertEqual(build_rates_warning(), "")

    def test_a_few_gaps_are_listed(self):
        self.use(FakeProvider(gaps=["2024-02", "2024-03"]))
        warning = build_rates_warning()

        self.assertIn("2024-02", warning)
        self.assertIn("2024-03", warning)

    def test_many_gaps_are_counted_instead_of_listed(self):
        # Listing them all would eat the message.
        gaps = [f"2024-{month:02d}" for month in range(1, 12)]
        self.use(FakeProvider(gaps=gaps))
        warning = build_rates_warning()

        self.assertIn(str(len(gaps)), warning)
        self.assertIn("2024-01", warning)
        self.assertNotIn("2024-07", warning)


class TestBuildSummary(ReportTestCase):
    def test_records_are_counted_across_every_node(self):
        # Not `len(report["records"])`: the group report has no such flat list.
        self.use(FakeProvider())
        report = make_report(
            [make_node("Salary", [make_record(1, "100"), make_record(2, "200")])],
            ungrouped=[make_record(3, "50")],
        )

        summary = build_summary(report)

        self.assertIn("`3`", summary)
        self.assertIn("`1`", summary)


class TestBuildViewPages(ReportTestCase):
    def setUp(self):
        self.use(FakeProvider())

    def test_without_groups_the_tree_is_the_plain_list_it_used_to_be(self):
        # Nobody who never made a group should see their report change shape.
        report = make_report([], ungrouped=[make_record(1, "100"), make_record(2, "200")])
        pages = build_view_pages(report, None, VIEW_TREE)

        self.assertEqual(len(pages), 1)
        self.assertIn("ID 1.", pages[0])
        self.assertNotIn("[", pages[0])

    def test_with_groups_records_sit_under_a_heading(self):
        report = make_report([make_node("Salary", [make_record(1, "100")])])
        page = build_view_pages(report, None, VIEW_TREE)[0]

        self.assertIn("[Salary] (1)", page)
        self.assertIn("ID 1.", page)

    def test_an_empty_report_has_no_pages_to_show(self):
        # `render_page` then says "no records yet" instead.
        self.assertEqual(build_view_pages(make_report([]), None, VIEW_TREE), [])

    def test_summary_is_one_line_per_node_plus_the_total(self):
        report = make_report(
            [make_node("Salary", [make_record(1, "100")]), make_node("Gifts", [make_record(2, "200")], group_id=2)],
            ungrouped=[make_record(3, "50")],
        )
        page = build_view_pages(report, None, VIEW_SUMMARY)[0]
        lines = page.split("\n")

        self.assertIn("[Salary] (1)", lines[0])
        self.assertIn("[Gifts] (1)", lines[1])
        self.assertIn("[(ungrouped)] (1)", lines[2])
        self.assertIn("[TOTAL] (3)", lines[-1])

    def test_summary_hides_an_empty_ungrouped_bucket(self):
        report = make_report([make_node("Salary", [make_record(1, "100")])])
        page = build_view_pages(report, None, VIEW_SUMMARY)[0]

        self.assertNotIn("ungrouped", page)


class TestBuildGroupList(ReportTestCase):
    def report_with(self, count: int, name_length: int = 8) -> dict:
        return make_report(
            [
                make_node(
                    ("G" * (name_length - 3)) + f"{index:03d}", [make_record(index, "1234567.89")], group_id=index
                )
                for index in range(1, count + 1)
            ]
        )

    def test_no_groups_says_so_even_when_there_are_records(self):
        # The question is "which groups do I have"; the ungrouped bucket is not
        # one, and `/inflation report` is where its records are.
        self.use(FakeProvider(make_report([], ungrouped=[make_record(1, "100")])))

        self.assertIn("No groups yet", build_group_list(1))

    def test_every_group_is_listed_when_they_fit(self):
        self.use(FakeProvider(self.report_with(3)))
        listing = build_group_list(1)

        for index in range(1, 4):
            self.assertIn(f"{index:03d}", listing)
        self.assertNotIn("Only the first", listing)

    def test_too_many_groups_are_cut_and_said_to_be_cut(self):
        self.use(FakeProvider(self.report_with(50, name_length=100)))
        listing = build_group_list(1)

        self.assertLessEqual(len(listing), MESSAGE_LIMIT)
        self.assertIn("Only the first", listing)
        self.assertTrue(listing.startswith("```text"))

    def test_the_limit_holds_even_when_the_phrases_are_rewritten(self):
        # `phrases.json` is hand-edited per guild: the wrapper and the note can
        # be any length, so their cost has to be measured, not assumed.
        import core.cache

        core.cache._phrases["global"] = {
            "inflation": {
                "group_list": "**" + ("Бюджетні групи " * 20) + "**\n```text\n{groups}\n```",
                "group_list_truncated": "*" + ("не вміщується " * 30) + "({shown}/{total})*",
            }
        }
        self.addCleanup(core.cache._phrases.clear)

        self.use(FakeProvider(self.report_with(50, name_length=100)))

        self.assertLessEqual(len(build_group_list(1)), MESSAGE_LIMIT)

    def test_phrases_so_long_they_leave_no_room_still_produce_a_valid_message(self):
        import core.cache

        core.cache._phrases["global"] = {
            "inflation": {
                "group_list": "x" * 2500 + "{groups}",
                "group_list_truncated": "y" * 2500,
            }
        }
        self.addCleanup(core.cache._phrases.clear)

        self.use(FakeProvider(self.report_with(50, name_length=100)))
        listing = build_group_list(1)

        # The code fence must never be cut open, so the listing goes entirely.
        self.assertLessEqual(len(listing), MESSAGE_LIMIT)
        self.assertNotIn("x", listing)


if __name__ == "__main__":
    unittest.main()
