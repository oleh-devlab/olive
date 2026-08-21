"""Tests for the one-message answers to a slash command.

Each of these is refused by Discord rather than truncated if it runs long, so
what most of them check is that the message fits whatever the phrases and the
listing cost between them.
"""

import datetime
import unittest

from tests.inflation_fixtures import (
    JAN,
    FakeProvider,
    RenderingTestCase,
    make_consumed,
    make_deposit,
    make_node,
    make_record,
    make_report,
    make_withdrawal,
)
from modules import inflation_replies
from modules.inflation_formatter import MESSAGE_LIMIT
from modules.inflation_replies import (
    build_deposit_overview,
    build_group_list,
    build_withdrawal_message,
)


class RepliesTestCase(RenderingTestCase):
    """Every test here reaches the provider through `inflation_replies`."""

    modules = (inflation_replies,)


class TestBuildGroupList(RepliesTestCase):
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


class TestGroupListWithDeposits(RepliesTestCase):
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


class TestBuildWithdrawalMessage(RepliesTestCase):
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


class TestBuildDepositOverview(RepliesTestCase):
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


class TestGroupListCapacity(RepliesTestCase):
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


if __name__ == "__main__":
    unittest.main()
