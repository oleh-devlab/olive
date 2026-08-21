"""Tests for the provider — the layer between the cogs and the vendored library.

What is tested here is the provider's own contribution, not the calculator's:
the per-scope limits, the stored preferences, the Discord-option adaptation, and
the isolation between owners. The arithmetic of deposits and inflation belongs to
`inflation_calculator` and has its own suite there.

Unlike the formatter and report suites this one runs the real library against a
real (temporary) directory, so it needs the submodule checked out.
"""

import datetime
import shutil
import sys
import tempfile
import types
import unittest
from decimal import Decimal
from pathlib import Path

# Setup path so we can import from src
# TODO: fix paths
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

# The provider reads `settings` at call time for every limit, and the module is
# written by the operator, so a checkout has none. Each test sets the attributes
# it cares about; `getattr` defaults cover the rest.
settings_stub = sys.modules.setdefault("settings", types.ModuleType("settings"))

from modules import inflation_provider as mod  # noqa: E402
from modules.inflation_calculator.modules.exceptions import ValidationError  # noqa: E402

JAN = datetime.date(2024, 1, 15)


class ProviderTestCase(unittest.TestCase):
    """A provider whose every file lives in a directory thrown away afterwards.

    `get_base_data_dir` is the single root: the record files hang off it through
    `get_data_dir`, and so do both channel registries. Patching it is therefore
    enough to keep a test out of the repository's own `data/`.
    """

    def setUp(self):
        self.data_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)

        original = mod.get_base_data_dir
        mod.get_base_data_dir = lambda: self.data_dir
        self.addCleanup(setattr, mod, "get_base_data_dir", original)

        # An operator's `inflation_data_dir` would point outside the temporary
        # root and undo the isolation above.
        if hasattr(settings_stub, "inflation_data_dir"):
            delattr(settings_stub, "inflation_data_dir")

        self.provider = mod.InflationProvider()

    def set_settings(self, **values):
        """Set operator settings for one test, restoring them afterwards.

        The previous value has to be read before the new one is written, or the
        cleanup restores what the test itself set and leaks it into the next one.
        """
        for name, value in values.items():
            self.addCleanup(self._restore, name, getattr(settings_stub, name, None), hasattr(settings_stub, name))
            setattr(settings_stub, name, value)

    @staticmethod
    def _restore(name, value, existed):
        if existed:
            setattr(settings_stub, name, value)
        else:
            delattr(settings_stub, name)

    def add_deposit_group(self, owner_id=1, amount="100000"):
        """A group holding one lot, under a year of monthly capitalization."""
        self.provider.create_group(owner_id, "Savings")
        self.provider.add_record(owner_id, amount, datetime.date(2024, 1, 1), group="Savings")
        self.provider.attach_deposit(
            owner_id,
            "Savings",
            annual_rate_percent="15",
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2025, 1, 1),
        )


class TestLimits(ProviderTestCase):
    """The per-scope caps, which live in the provider and nowhere else."""

    def test_record_limit_is_per_scope(self):
        self.set_settings(inflation_max_records_per_user=2, inflation_max_records_per_server=3)

        for i in range(2):
            self.provider.add_record(1, "100", JAN)
        with self.assertRaises(ValidationError):
            self.provider.add_record(1, "100", JAN)

        # The same owner id under the server scope is a different budget.
        for i in range(3):
            self.provider.add_record(1, "100", JAN, scope=mod.SERVER_SCOPE)
        with self.assertRaises(ValidationError):
            self.provider.add_record(1, "100", JAN, scope=mod.SERVER_SCOPE)

    def test_group_limit(self):
        self.set_settings(inflation_max_groups_per_user=1)

        self.provider.create_group(1, "First")
        with self.assertRaises(ValidationError):
            self.provider.create_group(1, "Second")

    def test_closing_a_deposit_cannot_smuggle_past_the_record_limit(self):
        """The interest lands as one record per period, so it has to be counted.

        The library credits them through its own `add_record`, which knows
        nothing about the bot's limits — without the provider's check a year of
        monthly capitalization would add twelve records to a full budget.
        """
        self.set_settings(inflation_max_records_per_user=5)
        self.add_deposit_group()

        with self.assertRaises(ValidationError):
            self.provider.close_deposit(1, "Savings")

        # Nothing may have happened: not a single record, and not the deposit.
        self.assertEqual(self.provider.count_records(1), 1)
        self.assertIsNotNone(self.provider.get_deposit_terms(1, "Savings"))

    def test_closing_a_deposit_within_the_limit_credits_the_interest(self):
        self.set_settings(inflation_max_records_per_user=50)
        self.add_deposit_group()

        result = self.provider.close_deposit(1, "Savings")

        self.assertGreater(result.net_interest, 0)
        self.assertGreater(self.provider.count_records(1), 1)
        self.assertIsNone(self.provider.get_deposit_terms(1, "Savings"))


class TestDeposits(ProviderTestCase):
    """Building the library's terms out of what a slash command can carry."""

    def test_terms_are_built_from_strings(self):
        self.provider.create_group(1, "Savings")
        self.provider.attach_deposit(
            1,
            "Savings",
            annual_rate_percent="15.5",
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2025, 1, 1),
            capitalization="quarterly",
            tax_percent="18",
            early_withdrawal_rate_percent="1",
            comment="Monobank",
        )

        terms = self.provider.get_deposit_terms(1, "Savings")
        self.assertEqual(terms.annual_rate_percent, Decimal("15.5"))
        self.assertEqual(terms.capitalization.value, "quarterly")
        self.assertEqual(terms.tax_percent, Decimal("18"))
        self.assertEqual(terms.comment, "Monobank")

    def test_bad_terms_raise_validation_error(self):
        self.provider.create_group(1, "Savings")

        with self.assertRaises(ValidationError):
            self.provider.attach_deposit(
                1,
                "Savings",
                annual_rate_percent="15",
                start_date=datetime.date(2025, 1, 1),
                end_date=datetime.date(2024, 1, 1),
            )

    def test_detach_drops_the_deposit_without_crediting(self):
        self.add_deposit_group()

        self.provider.detach_deposit(1, "Savings")

        self.assertIsNone(self.provider.get_deposit_terms(1, "Savings"))
        self.assertEqual(self.provider.count_records(1), 1)

    def test_no_deposit_reads_as_none_rather_than_raising(self):
        self.provider.create_group(1, "Savings")

        self.assertIsNone(self.provider.get_deposit_terms(1, "Savings"))
        self.assertIsNone(self.provider.get_deposit_projection(1, "Savings"))

    def test_round_each_period_defaults_to_true(self):
        self.provider.create_group(1, "Savings")
        self.provider.attach_deposit(
            1,
            "Savings",
            annual_rate_percent="15",
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2025, 1, 1),
        )

        self.assertTrue(self.provider.get_deposit_terms(1, "Savings").round_each_period)

    def test_round_each_period_off_matches_a_bank_that_rounds_only_at_payout(self):
        # A kopiyka a year, and only visible when periods stop being rounded
        # one at a time -- the case the upstream fix exists for.
        self.provider.create_group(1, "Savings")
        self.provider.add_record(1, "220.12", datetime.date(2025, 8, 12), group="Savings")
        self.provider.attach_deposit(
            1,
            "Savings",
            annual_rate_percent="16",
            start_date=datetime.date(2025, 8, 12),
            end_date=datetime.date(2026, 8, 12),
            round_each_period=False,
        )

        self.assertFalse(self.provider.get_deposit_terms(1, "Savings").round_each_period)
        rounded = self.provider.get_deposit_projection(1, "Savings", datetime.date(2026, 8, 12))
        self.provider.detach_deposit(1, "Savings")
        self.provider.attach_deposit(
            1,
            "Savings",
            annual_rate_percent="16",
            start_date=datetime.date(2025, 8, 12),
            end_date=datetime.date(2026, 8, 12),
        )
        unrounded = self.provider.get_deposit_projection(1, "Savings", datetime.date(2026, 8, 12))

        self.assertNotEqual(rounded.final_amount, unrounded.final_amount)


class TestGroupReferences(ProviderTestCase):
    """A Discord option is always a string; the library wants an id or a name."""

    def test_a_digits_only_option_selects_by_id(self):
        created = self.provider.create_group(1, "Savings")
        self.provider.add_record(1, "100", JAN, group=str(created["id"]))

        self.assertEqual(self.provider.list_records(1)[0]["group_id"], created["id"])

    def test_an_empty_option_means_ungrouped(self):
        self.provider.create_group(1, "Savings")
        self.provider.add_record(1, "100", JAN, group="")

        self.assertIsNone(self.provider.list_records(1)[0]["group_id"])

    def test_find_group_hands_out_a_copy(self):
        """The calculator returns its live dict, and a cog would mutate storage."""
        self.provider.create_group(1, "Savings")

        found = self.provider.find_group(1, "Savings")
        found["name"] = "Renamed"

        self.assertEqual(self.provider.find_group(1, "Savings")["name"], "Savings")

    def test_find_group_returns_none_for_an_unknown_reference(self):
        self.assertIsNone(self.provider.find_group(1, "Nothing"))


class TestWithdraw(ProviderTestCase):
    """The provider's pass-through, and the warning a deposit adds to it."""

    def test_oldest_lots_go_first(self):
        self.provider.create_group(1, "Savings")
        for day in (1, 2, 3):
            self.provider.add_record(1, "100", datetime.date(2024, 1, day), group="Savings")

        result = self.provider.withdraw(1, "150", "Savings")

        self.assertEqual(result["consumed"][0]["date"], datetime.date(2024, 1, 1))
        self.assertEqual(result["consumed"][0]["taken"], Decimal("100"))
        self.assertEqual(result["consumed"][1]["taken"], Decimal("50"))
        # The lot spent in full is gone; the split one and the untouched one stay.
        self.assertEqual(self.provider.count_records(1), 2)

    def test_a_running_deposit_warns_but_never_blocks(self):
        """Only a deposit still running has interest left to forfeit."""
        today = datetime.date.today()
        self.provider.create_group(1, "Savings")
        self.provider.add_record(1, "100000", today - datetime.timedelta(days=200), group="Savings")
        self.provider.attach_deposit(
            1,
            "Savings",
            annual_rate_percent="15",
            start_date=today - datetime.timedelta(days=200),
            end_date=today + datetime.timedelta(days=200),
        )

        result = self.provider.withdraw(1, "1000", "Savings")

        self.assertEqual(result["amount"], Decimal("1000"))
        self.assertIsNotNone(result["warning"])

    def test_a_matured_deposit_costs_nothing_to_take_money_out_of(self):
        self.add_deposit_group()

        self.assertIsNone(self.provider.withdraw(1, "1000", "Savings")["warning"])

    def test_withdrawing_more_than_the_group_holds_raises(self):
        self.provider.create_group(1, "Savings")
        self.provider.add_record(1, "100", JAN, group="Savings")

        with self.assertRaises(ValidationError):
            self.provider.withdraw(1, "500", "Savings")


class TestPreferences(ProviderTestCase):
    """View mode and interest folding, both stored in the channel registry."""

    def register(self, owner_id=1, scope=mod.USER_SCOPE):
        """A registry entry, which is what a preference hangs off."""
        registry = self.provider._registry(scope)
        registry.register(owner_id, display_channel_id=123, guild_id=7)

    def test_defaults_when_the_owner_has_no_entry(self):
        self.assertEqual(self.provider.get_view_mode(1), mod.VIEW_TREE)
        self.assertTrue(self.provider.get_collapse_interest(1))

    def test_saving_a_preference_needs_a_channel(self):
        self.assertFalse(self.provider.set_view_mode(1, mod.VIEW_SUMMARY))
        self.assertFalse(self.provider.set_collapse_interest(1, False))

    def test_preferences_round_trip(self):
        self.register()

        self.assertTrue(self.provider.set_view_mode(1, mod.VIEW_SUMMARY))
        self.assertTrue(self.provider.set_collapse_interest(1, False))

        self.assertEqual(self.provider.get_view_mode(1), mod.VIEW_SUMMARY)
        self.assertFalse(self.provider.get_collapse_interest(1))

    def test_a_preference_does_not_evict_the_channel_ids(self):
        """The registry keeps keys it does not know, which is what this relies on."""
        self.register()
        self.provider.set_view_mode(1, mod.VIEW_SUMMARY)
        self.provider.set_collapse_interest(1, False)

        self.assertEqual(self.provider.get_report_channel_id(1), 123)

    def test_an_unknown_view_mode_raises(self):
        self.register()

        with self.assertRaises(ValidationError):
            self.provider.set_view_mode(1, "spreadsheet")

    def test_the_operator_default_applies_until_an_owner_overrides_it(self):
        self.set_settings(inflation_collapse_interest=False)
        self.assertFalse(self.provider.get_collapse_interest(1))

        self.register()
        self.provider.set_collapse_interest(1, True)
        self.assertTrue(self.provider.get_collapse_interest(1))

    def test_preferences_are_kept_per_scope(self):
        self.register(scope=mod.USER_SCOPE)
        self.register(scope=mod.SERVER_SCOPE)

        self.provider.set_view_mode(1, mod.VIEW_SUMMARY, mod.USER_SCOPE)

        self.assertEqual(self.provider.get_view_mode(1, mod.SERVER_SCOPE), mod.VIEW_TREE)


class TestScopeIsolation(ProviderTestCase):
    """A user's budget and a guild's must never be the same file."""

    def test_records_do_not_leak_between_scopes(self):
        self.provider.add_record(1, "100", JAN)
        self.provider.add_record(1, "200", JAN, scope=mod.SERVER_SCOPE)

        self.assertEqual(self.provider.count_records(1), 1)
        self.assertEqual(self.provider.count_records(1, mod.SERVER_SCOPE), 1)
        self.assertEqual(self.provider.list_records(1)[0]["amount"], Decimal("100"))

    def test_an_unknown_scope_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            mod.get_scope_records_dir("guild")


class TestCollapseInterest(ProviderTestCase):
    """The folding the report asks for is the library's, wired through here."""

    def test_the_report_folds_interest_only_when_asked(self):
        self.set_settings(inflation_max_records_per_user=50)
        self.add_deposit_group()
        self.provider.close_deposit(1, "Savings")

        expanded = self.provider.get_groups_report(1, collapse_interest=False)["groups"][0]
        folded = self.provider.get_groups_report(1, collapse_interest=True)["groups"][0]

        self.assertGreater(len(expanded["records"]), len(folded["records"]))
        # Folding is a rendering choice, so the money may not move.
        self.assertEqual(expanded["total_adjusted"], folded["total_adjusted"])
        self.assertEqual(expanded["records_count"], folded["records_count"])

    def test_folding_is_on_by_default(self):
        self.set_settings(inflation_max_records_per_user=50)
        self.add_deposit_group()
        self.provider.close_deposit(1, "Savings")

        default = self.provider.get_groups_report(1)["groups"][0]
        folded = self.provider.get_groups_report(1, collapse_interest=True)["groups"][0]

        self.assertEqual(len(default["records"]), len(folded["records"]))


if __name__ == "__main__":
    unittest.main()
