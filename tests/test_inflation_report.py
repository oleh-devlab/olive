"""Tests for the report itself — the message a reader watches in their channel."""

import unittest

from tests.inflation_fixtures import (
    FakeProvider,
    RenderingTestCase,
    make_deposit,
    make_node,
    make_record,
    make_report,
)
import sys
import types

# Reached through `inflation_provider`, which imports the operator's `settings`
# module — absent from a checkout. Stubbed here rather than relying on another
# test module having done it first, so this one runs on its own.
sys.modules.setdefault("settings", types.ModuleType("settings"))

from modules import inflation_report  # noqa: E402
from modules.inflation_formatter import MESSAGE_LIMIT  # noqa: E402
from modules.inflation_provider import VIEW_SUMMARY, VIEW_TREE  # noqa: E402
from modules.inflation_report import (  # noqa: E402
    build_deposits_warning,
    build_rates_warning,
    build_summary,
    build_view_pages,
)


class ReportTestCase(RenderingTestCase):
    """Every test here reaches the provider through `inflation_report`."""

    modules = (inflation_report,)


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


class TestDepositsInViews(ReportTestCase):
    def test_the_tree_hangs_the_deposit_under_the_group_heading(self):
        report = make_report([make_node("Salary", [make_record(1, "100")], deposit=make_deposit())])
        self.use(FakeProvider(report))

        (page,) = build_view_pages(report, mode=VIEW_TREE)

        # The heading itself is two lines — the label, then the sub-totals.
        label, totals, deposit, *_ = page.split("\n")
        self.assertIn("[Salary]", label)
        self.assertIn("->", totals)
        self.assertIn("15.00%", deposit)

    def test_the_summary_carries_it_too(self):
        report = make_report([make_node("Salary", [make_record(1, "100")], deposit=make_deposit())])
        self.use(FakeProvider(report))

        (page,) = build_view_pages(report, mode=VIEW_SUMMARY)

        self.assertIn("[Salary] (1)", page)
        self.assertIn("15.00%", page)

    def test_a_spilled_group_repeats_its_deposit_with_the_heading(self):
        """The deposit belongs to the heading, so it must follow it onto page two."""
        records = [make_record(i, "100", comment="x" * 100) for i in range(1, 40)]
        report = make_report([make_node("Salary", records, deposit=make_deposit())])
        self.use(FakeProvider(report))

        pages = build_view_pages(report, mode=VIEW_TREE)

        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertIn("15.00%", page)


class TestBuildDepositsWarning(ReportTestCase):
    def test_nothing_matured_means_no_warning(self):
        report = make_report([make_node("Salary", [make_record(1, "100")], deposit=make_deposit())])

        self.assertEqual(build_deposits_warning(report), "")

    def test_no_deposits_at_all_means_no_warning(self):
        report = make_report([make_node("Salary", [make_record(1, "100")])])

        self.assertEqual(build_deposits_warning(report), "")

    def test_a_matured_deposit_is_named(self):
        report = make_report([make_node("Salary", [make_record(1, "100")], deposit=make_deposit(matured=True))])

        self.assertIn("Salary", build_deposits_warning(report))

    def test_the_warning_reaches_the_summary_in_both_modes(self):
        """The summary is the one part `tree` and `summary` always share."""
        report = make_report([make_node("Salary", [make_record(1, "100")], deposit=make_deposit(matured=True))])
        self.use(FakeProvider(report))

        self.assertIn("Salary", build_summary(report))

    def test_it_sits_alongside_the_rates_warning_rather_than_replacing_it(self):
        report = make_report([make_node("Salary", [make_record(1, "100")], deposit=make_deposit(matured=True))])
        self.use(FakeProvider(report, has_rates=False))

        summary = build_summary(report)

        self.assertIn("Salary", summary)
        self.assertIn(build_rates_warning(), summary)


class TestDepositsWarningIsBounded(ReportTestCase):
    def test_a_handful_of_matured_deposits_are_named(self):
        groups = [
            make_node(f"G{i}", [make_record(i, "100")], group_id=i, deposit=make_deposit(matured=True))
            for i in range(1, 4)
        ]

        self.assertIn("G1", build_deposits_warning(make_report(groups)))

    def test_a_server_full_of_them_falls_back_to_a_count(self):
        """Fifty group names would blow the message limit on the warning alone."""
        groups = [
            make_node(
                f"Group number {i} with a longish name",
                [make_record(i, "100")],
                group_id=i,
                deposit=make_deposit(matured=True),
            )
            for i in range(1, 51)
        ]
        report = make_report(groups)
        self.use(FakeProvider(report))

        warning = build_deposits_warning(report)

        self.assertIn("50", warning)
        self.assertNotIn("Group number 1 with", warning)
        self.assertLessEqual(len(build_summary(report)), MESSAGE_LIMIT)


if __name__ == "__main__":
    unittest.main()
