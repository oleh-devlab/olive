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
    def __init__(self, id=None, deadline=None, name="Routine"):
        self.id, self.deadline, self.name = id, deadline, name


class FakeSkipped:
    """What the solver hands back for something it could not place."""

    def __init__(self, task):
        self.task = task


def deadline(day: int, hour: int = 23, minute: int = 59) -> datetime.datetime:
    return datetime.datetime(2026, 8, day, hour, minute)


class TestFormatSkippedRoutines(unittest.TestCase):
    def test_a_routine_is_named_once_with_the_days_it_missed(self):
        skipped = [FakeSkipped(FakeTask(f"r_23_{day}", deadline(day))) for day in (26, 27, 28)]

        self.assertEqual(format_skipped_routines(skipped), ["23: 26.08, 27.08, 28.08"])

    def test_the_day_is_named_without_the_clock_time(self):
        # A routine runs at most once a day, so the time said nothing.
        skipped = [FakeSkipped(FakeTask("r_5_1", deadline(26, 9, 15)))]

        self.assertEqual(format_skipped_routines(skipped), ["5: 26.08"])

    def test_a_day_is_not_listed_twice(self):
        skipped = [FakeSkipped(FakeTask("r_5_1", deadline(26))), FakeSkipped(FakeTask("r_5_2", deadline(26)))]

        self.assertEqual(format_skipped_routines(skipped), ["5: 26.08"])

    def test_past_the_threshold_the_days_collapse_into_a_count(self):
        skipped = [FakeSkipped(FakeTask(f"r_23_{day}", deadline(day))) for day in range(20, 25)]

        self.assertEqual(format_skipped_routines(skipped), ["23 (missed 5 times)"])

    def test_each_routine_gets_its_own_entry(self):
        skipped = [FakeSkipped(FakeTask("r_23_1", deadline(26))), FakeSkipped(FakeTask("r_7_1", deadline(27)))]

        self.assertEqual(format_skipped_routines(skipped), ["23: 26.08", "7: 27.08"])

    def test_a_routine_without_a_deadline_says_so(self):
        self.assertEqual(format_skipped_routines([FakeSkipped(FakeTask("r_9_1", None))]), ["9: no deadline"])

    def test_a_routine_the_expander_gave_no_id_falls_back_to_its_name(self):
        skipped = [FakeSkipped(FakeTask(None, deadline(26), name="Вчити англійську"))]

        self.assertEqual(format_skipped_routines(skipped), ["Вчити англійську: 26.08"])

    def test_nothing_skipped_says_nothing(self):
        self.assertEqual(format_skipped_routines([]), [])


if __name__ == "__main__":
    unittest.main()
