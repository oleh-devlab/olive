"""Tests for what the engine makes of the solver's output before anyone sees it.

Unlike the other schedule suites this one imports `schedule_engine`, so it needs
the vendored scheduler and `ortools` present.
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

# The engine imports the operator's `settings` module, absent from a checkout.
# Stubbed here rather than relying on another test module having done it first.
sys.modules.setdefault("settings", types.ModuleType("settings"))

from modules.schedule_engine import format_skipped_routines  # noqa: E402


class FakeTask:
    """One day's copy of a routine, the way the expander hands it to the solver."""

    def __init__(self, routine_id=None, deadline=None, name="Routine"):
        self.routine_id, self.deadline, self.name = routine_id, deadline, name
        self.id = f"r_{routine_id}_2026-08-26" if routine_id else None


class FakeSkipped:
    """What the solver hands back for something it could not place."""

    def __init__(self, task):
        self.task = task


def deadline(day: int, hour: int = 23, minute: int = 59) -> datetime.datetime:
    return datetime.datetime(2026, 8, day, hour, minute)


class TestFormatSkippedRoutines(unittest.TestCase):
    def test_a_routine_is_named_once_with_the_days_it_missed(self):
        skipped = [FakeSkipped(FakeTask(23, deadline(day), "Вчити англійську")) for day in (26, 27, 28)]

        self.assertEqual(format_skipped_routines(skipped), ["Вчити англійську: 26.08, 27.08, 28.08"])

    def test_a_routine_is_named_rather_than_numbered(self):
        # The id groups the days; what the reader is shown is the name.
        skipped = [FakeSkipped(FakeTask(23, deadline(26), "Ранкова зарядка"))]

        self.assertEqual(format_skipped_routines(skipped), ["Ранкова зарядка: 26.08"])

    def test_a_name_that_holds_a_parenthetical_keeps_it(self):
        skipped = [FakeSkipped(FakeTask(23, deadline(26), "Англійська (з репетитором)"))]

        self.assertEqual(format_skipped_routines(skipped), ["Англійська (з репетитором): 26.08"])

    def test_the_day_is_named_without_the_clock_time(self):
        # A routine runs at most once a day, so the time said nothing.
        skipped = [FakeSkipped(FakeTask(5, deadline(26, 9, 15), "Зарядка"))]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка: 26.08"])

    def test_a_day_is_not_listed_twice(self):
        skipped = [FakeSkipped(FakeTask(5, deadline(26), "Зарядка")), FakeSkipped(FakeTask(5, deadline(26), "Зарядка"))]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка: 26.08"])

    def test_past_the_threshold_the_days_collapse_into_a_count(self):
        skipped = [FakeSkipped(FakeTask(23, deadline(day), "Зарядка")) for day in range(20, 25)]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка (missed 5 times)"])

    def test_each_routine_gets_its_own_entry(self):
        skipped = [
            FakeSkipped(FakeTask(23, deadline(26), "Зарядка")),
            FakeSkipped(FakeTask(7, deadline(27), "Читання")),
        ]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка: 26.08", "Читання: 27.08"])

    def test_two_routines_sharing_a_name_stay_apart(self):
        skipped = [
            FakeSkipped(FakeTask(23, deadline(26), "Зарядка")),
            FakeSkipped(FakeTask(7, deadline(27), "Зарядка")),
        ]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка: 26.08", "Зарядка: 27.08"])

    def test_a_routine_without_a_deadline_says_so(self):
        skipped = [FakeSkipped(FakeTask(9, None, "Зарядка"))]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка: no deadline"])

    def test_a_routine_with_no_id_of_its_own_groups_by_name(self):
        skipped = [FakeSkipped(FakeTask(None, deadline(day), "Вчити англійську")) for day in (26, 27)]

        self.assertEqual(format_skipped_routines(skipped), ["Вчити англійську: 26.08, 27.08"])

    def test_nothing_skipped_says_nothing(self):
        self.assertEqual(format_skipped_routines([]), [])


if __name__ == "__main__":
    unittest.main()
