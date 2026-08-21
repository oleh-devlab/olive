"""Tests for the localized fragments a report is described with.

No provider is involved: every function here takes a report node and a guild id
and returns text, so the tests hand it a node directly.
"""

import sys
import types
import unittest

from tests.inflation_fixtures import make_deposit, make_node, make_record

# `inflation_phrases` reaches the view-mode constants through `inflation_provider`,
# which imports the operator's `settings` module — absent from a checkout.
sys.modules.setdefault("settings", types.ModuleType("settings"))

from modules.inflation_phrases import build_deposit_lines, build_deposit_marker  # noqa: E402


class TestBuildDepositLines(unittest.TestCase):
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


class TestDepositMarker(unittest.TestCase):
    def test_the_marker_stays_on_the_group_line(self):
        """A full deposit line would cost `/inflation_groups list` two thirds of
        the groups it can fit; the marker is a suffix, not a second line."""
        node = make_node("Salary", [make_record(1, "100")], deposit=make_deposit())
        marker = build_deposit_marker(node, None)

        self.assertNotIn("\n", marker)
        self.assertLess(len(marker), 40)
        self.assertIn("15.00%", marker)


if __name__ == "__main__":
    unittest.main()
