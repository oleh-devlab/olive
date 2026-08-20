import datetime
import sys
import types
import unittest
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

from tests.inflation_fixtures import (  # noqa: E402
    JAN,
    make_consumed,
    make_deposit,
    make_node,
    make_record,
    make_report,
    make_withdrawal,
)
from modules import inflation_report  # noqa: E402
from modules.inflation_report import (  # noqa: E402
    MESSAGE_LIMIT,
    VIEW_SUMMARY,
    VIEW_TREE,
    build_deposit_lines,
    build_deposit_marker,
    build_deposit_overview,
    build_deposits_warning,
    build_group_list,
    build_withdrawal_message,
    build_rates_warning,
    build_summary,
    build_view_pages,
)


class FakeProvider:
    """The provider calls this module makes, without any storage behind them."""

    def __init__(self, report: dict | None = None, has_rates: bool = True, gaps: list[str] | None = None):
        self.report = report or make_report([])
        self.has_rates = has_rates
        self.gaps = gaps or []

    def get_rate_status(self) -> tuple[bool, list[str]]:
        return self.has_rates, self.gaps

    def get_groups_report(self, owner_id, scope="user", *, detailed=True, collapse_interest=True) -> dict:
        return self.report

    def get_view_mode(self, owner_id, scope="user") -> str:
        return VIEW_TREE

    def get_collapse_interest(self, owner_id, scope="user") -> bool:
        return True


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


class TestBuildDepositLines(ReportTestCase):
    def test_a_group_without_a_deposit_says_nothing(self):
        self.assertEqual(build_deposit_lines(make_node("Salary", [make_record(1, "100")])), [])

    def test_a_missing_deposit_key_is_not_an_error(self):
        """Reports built before deposits existed have no `deposit` key at all."""
        node = make_node("Salary", [make_record(1, "100")])
        del node["deposit"]

        self.assertEqual(build_deposit_lines(node), [])

    def test_a_running_deposit_names_its_rate_and_both_numbers(self):
        node = make_node("Salary", [make_record(1, "100")], deposit=make_deposit())

        (line,) = build_deposit_lines(node)

        self.assertIn("15.00%", line)
        self.assertIn("01.01.2026", line)
        self.assertIn("1 234.50", line)
        self.assertIn("12 687.23", line)
        self.assertNotIn("MATURED", line)

    def test_a_matured_deposit_says_so_and_names_what_is_waiting(self):
        node = make_node("Salary", [make_record(1, "100")], deposit=make_deposit(matured=True))

        (line,) = build_deposit_lines(node)

        self.assertIn("MATURED", line)
        self.assertIn("12 687.23", line)


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

    def test_a_deposit_free_report_renders_exactly_as_before(self):
        report = make_report([make_node("Salary", [make_record(1, "100")])])
        self.use(FakeProvider(report))

        (page,) = build_view_pages(report, mode=VIEW_SUMMARY)

        self.assertEqual(len(page.split("\n")), 3)

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


class TestGroupListWithDeposits(ReportTestCase):
    def test_a_deposit_shows_in_the_listing(self):
        report = make_report([make_node("Salary", [make_record(1, "100")], deposit=make_deposit())])
        self.use(FakeProvider(report))

        self.assertIn("15.00%", build_group_list(1))

    def test_the_message_limit_still_holds(self):
        groups = [
            make_node(f"Group {i}", [make_record(i, "100")], group_id=i, deposit=make_deposit(matured=i % 2 == 0))
            for i in range(1, 60)
        ]
        self.use(FakeProvider(make_report(groups)))

        self.assertLessEqual(len(build_group_list(1)), MESSAGE_LIMIT)


class TestBuildWithdrawalMessage(ReportTestCase):
    def make_lots(self, count: int, source: str = "manual") -> list[dict]:
        return [
            make_consumed(i, JAN + datetime.timedelta(days=i), "100", "0", source=source) for i in range(1, count + 1)
        ]

    def test_a_small_withdrawal_lists_every_lot(self):
        message = build_withdrawal_message(make_withdrawal(self.make_lots(3)))

        self.assertEqual(message.count("took"), 3)

    def test_eating_every_record_still_fits_into_one_message(self):
        """A withdrawal can consume the owner's whole limit, and Discord
        refuses an over-long message instead of truncating it — with the money
        already gone, that would leave no confirmation at all."""
        message = build_withdrawal_message(make_withdrawal(self.make_lots(200)))

        self.assertLessEqual(len(message), MESSAGE_LIMIT)

    def test_a_truncated_listing_says_how_much_it_left_out(self):
        message = build_withdrawal_message(make_withdrawal(self.make_lots(200)))

        self.assertIn("200", message)
        self.assertTrue(message.startswith("Withdrew"))

    def test_interest_lots_are_folded(self):
        message = build_withdrawal_message(make_withdrawal(self.make_lots(12, source="deposit_interest")))

        self.assertIn("12 interest record(s)", message)

    def test_the_warning_survives_a_truncated_listing(self):
        """It names real money at risk, so it must not lose to the listing."""
        message = build_withdrawal_message(make_withdrawal(self.make_lots(200), warning="Breaking it costs 500 UAH."))

        self.assertIn("Breaking it costs 500 UAH.", message)
        self.assertLessEqual(len(message), MESSAGE_LIMIT)

    def test_phrases_so_long_they_leave_no_room_still_produce_a_valid_message(self):
        import core.cache

        core.cache._phrases["global"] = {"inflation": {"withdrawn": "x" * 2500 + "{consumed}"}}
        self.addCleanup(core.cache._phrases.clear)

        message = build_withdrawal_message(make_withdrawal(self.make_lots(5), warning="At risk: 10 UAH."))

        self.assertLessEqual(len(message), MESSAGE_LIMIT)
        self.assertIn("At risk: 10 UAH.", message)


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


class TestGroupListMarker(ReportTestCase):
    def test_the_marker_stays_on_the_group_line(self):
        """A full deposit line would cost `/inflation_groups list` two thirds of
        the groups it can fit; the marker is a suffix, not a second line."""
        node = make_node("Salary", [make_record(1, "100")], deposit=make_deposit())
        marker = build_deposit_marker(node, None)

        self.assertNotIn("\n", marker)
        self.assertLess(len(marker), 40)
        self.assertIn("15.00%", marker)

    def test_it_keeps_most_of_the_listing_s_capacity(self):
        def shown(with_deposit):
            groups = [
                make_node(
                    f"Group {i}",
                    [make_record(i, "100")],
                    group_id=i,
                    deposit=make_deposit() if with_deposit else None,
                )
                for i in range(1, 60)
            ]
            self.use(FakeProvider(make_report(groups)))
            return build_group_list(1).count("[Group")

        without = shown(False)
        self.assertGreaterEqual(shown(True), without // 2)

    def test_a_matured_deposit_is_marked_differently(self):
        groups = [make_node("Salary", [make_record(1, "100")], deposit=make_deposit(matured=True))]
        self.use(FakeProvider(make_report(groups)))

        self.assertIn("MATURED", build_group_list(1))


class TestBuildDepositOverview(ReportTestCase):
    def test_no_deposits_says_so(self):
        self.use(FakeProvider(make_report([make_node("Salary", [make_record(1, "100")])])))

        self.assertIn("No group", build_deposit_overview(1))

    def test_every_group_under_a_deposit_is_listed(self):
        groups = [
            make_node("Salary", [make_record(1, "100")], group_id=1, deposit=make_deposit()),
            make_node("Savings", [make_record(2, "100")], group_id=2, deposit=make_deposit(matured=True)),
            make_node("Cash", [make_record(3, "100")], group_id=3),
        ]
        self.use(FakeProvider(make_report(groups)))

        overview = build_deposit_overview(1)

        self.assertIn("Salary", overview)
        self.assertIn("Savings", overview)
        self.assertNotIn("Cash", overview)

    def test_a_server_full_of_deposits_still_fits(self):
        groups = [
            make_node(f"Group {i}", [make_record(i, "100")], group_id=i, deposit=make_deposit()) for i in range(1, 51)
        ]
        self.use(FakeProvider(make_report(groups)))

        self.assertLessEqual(len(build_deposit_overview(1)), MESSAGE_LIMIT)
