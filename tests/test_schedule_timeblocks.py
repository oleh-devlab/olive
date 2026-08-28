"""Tests for how a time block states its recurrence, on disk and on the way in.

A block used to be a `daily` bool, which could not say "weekly". It is now a
`repeat` word plus the weekdays it applies to, and three pieces have to agree on
that: what `ScheduleProvider` writes, what it reads back (including files still
carrying the old bool), and what the migration script does to those files.

Reaching the provider pulls in the vendored dataclasses -- whose package
re-exports the scheduler, so `ortools` comes with them -- and, through
`core.personal_channels`, `disnake`. This suite needs all three present.
"""

import datetime
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

# The provider imports the operator's `settings` module, absent from a checkout.
# Stubbed here rather than relying on another test module having done it first.
sys.modules.setdefault("settings", types.ModuleType("settings"))

from modules import schedule_provider  # noqa: E402
from modules.schedule_models import TimeBlock, block_repeat  # noqa: E402
from modules.schedule_validators import validate_timeblock_creation_data  # noqa: E402
from modules.schedule_exceptions import ScheduleValidationError  # noqa: E402
from scripts.migrate_timeblock_repeat import migrate_block, migrate_data_dir  # noqa: E402

START = datetime.datetime(2026, 8, 28, 18, 0)
END = datetime.datetime(2026, 8, 28, 20, 0)


class TestBlockRepeat(unittest.TestCase):
    """The one place that reads a recurrence back off the solver's pair of fields."""

    def test_a_plain_block_is_one_time(self):
        self.assertEqual(block_repeat(TimeBlock(start=START, end=END, daily=False)), "once")

    def test_a_daily_block_says_daily(self):
        self.assertEqual(block_repeat(TimeBlock(start=START, end=END, daily=True)), "daily")

    def test_weekdays_make_a_block_weekly(self):
        block = TimeBlock(start=START, end=END, weekdays=[0, 2])
        self.assertEqual(block_repeat(block), "weekly")

    def test_a_weekly_block_stops_claiming_to_be_daily(self):
        # The dataclass clears `daily` itself, so the two can never both be set.
        block = TimeBlock(start=START, end=END, daily=True, weekdays=[4])
        self.assertFalse(block.daily)
        self.assertEqual(block_repeat(block), "weekly")


class TestSerialization(unittest.TestCase):
    """What a stored block looks like, and what comes back out of it."""

    def test_a_daily_block_is_written_as_a_repeat_word(self):
        d = schedule_provider._timeblock_to_dict(TimeBlock(start=START, end=END, daily=True, id=1))
        self.assertEqual(d["repeat"], "daily")
        self.assertNotIn("daily", d)

    def test_a_one_time_block_is_written_as_once(self):
        d = schedule_provider._timeblock_to_dict(TimeBlock(start=START, end=END, daily=False, id=1))
        self.assertEqual(d["repeat"], "once")

    def test_only_a_weekly_block_stores_weekdays(self):
        weekly = schedule_provider._timeblock_to_dict(TimeBlock(start=START, end=END, weekdays=[1, 3], id=2))
        self.assertEqual(weekly["repeat"], "weekly")
        self.assertEqual(weekly["weekdays"], [1, 3])

        daily = schedule_provider._timeblock_to_dict(TimeBlock(start=START, end=END, daily=True, id=3))
        self.assertNotIn("weekdays", daily)

    def test_a_block_survives_the_round_trip(self):
        for block in (
            TimeBlock(start=START, end=END, daily=True, name="sleep", id=1),
            TimeBlock(start=START, end=END, daily=False, name="dentist", id=2),
            TimeBlock(start=START, end=END, weekdays=[0, 2, 4], name="gym", id=3),
        ):
            with self.subTest(repeat=block_repeat(block)):
                back = schedule_provider._dict_to_timeblock(schedule_provider._timeblock_to_dict(block))
                self.assertEqual(block_repeat(back), block_repeat(block))
                self.assertEqual(back.weekdays, block.weekdays)
                self.assertEqual((back.start, back.end, back.name, back.id), (START, END, block.name, block.id))


class TestReadingLegacyFiles(unittest.TestCase):
    """A file written before the `repeat` grammar still has to mean what it said."""

    def _read(self, d: dict) -> TimeBlock:
        return schedule_provider._dict_to_timeblock({"id": 1, "start": START.isoformat(), "end": END.isoformat(), **d})

    def test_the_old_true_reads_as_daily(self):
        self.assertEqual(block_repeat(self._read({"daily": True})), "daily")

    def test_the_old_false_reads_as_one_time(self):
        # Defaulting instead of reading would quietly turn this into a daily block.
        self.assertEqual(block_repeat(self._read({"daily": False})), "once")

    def test_repeat_wins_over_a_leftover_bool(self):
        self.assertEqual(block_repeat(self._read({"repeat": "once", "daily": True})), "once")

    def test_weekdays_are_ignored_unless_the_block_is_weekly(self):
        self.assertIsNone(self._read({"repeat": "daily", "weekdays": [1]}).weekdays)


class TestValidation(unittest.TestCase):
    """What the slash command and the agent are allowed to ask for."""

    def test_a_weekly_block_keeps_its_days(self):
        block = validate_timeblock_creation_data("18:00", "20:00", "weekly", "gym", [0, 2])
        self.assertEqual(block.weekdays, [0, 2])
        self.assertFalse(block.daily)

    def test_a_daily_block_needs_no_days(self):
        self.assertTrue(validate_timeblock_creation_data("18:00", "20:00", "daily").daily)

    def test_weekly_without_days_is_refused(self):
        with self.assertRaises(ScheduleValidationError):
            validate_timeblock_creation_data("18:00", "20:00", "weekly")

    def test_days_outside_the_week_are_refused(self):
        with self.assertRaises(ScheduleValidationError):
            validate_timeblock_creation_data("18:00", "20:00", "weekly", "", [7])

    def test_days_on_a_block_that_does_not_recur_on_them_are_refused(self):
        with self.assertRaises(ScheduleValidationError):
            validate_timeblock_creation_data("18:00", "20:00", "daily", "", [0])

    def test_an_unknown_repeat_is_refused_as_such(self):
        # Not as "invalid time format", which is what the parser below reports.
        with self.assertRaises(ScheduleValidationError) as caught:
            validate_timeblock_creation_data("18:00", "20:00", "monthly")
        self.assertIn("Repeat must be", str(caught.exception))

    def test_a_block_crossing_midnight_still_ends_after_it_starts(self):
        block = validate_timeblock_creation_data("23:00", "01:00", "weekly", "", [4])
        self.assertGreater(block.end, block.start)


class TestMigrationScript(unittest.TestCase):
    def test_a_daily_bool_becomes_a_repeat_word(self):
        block = {"id": 1, "daily": True}
        self.assertTrue(migrate_block(block))
        self.assertEqual(block, {"id": 1, "repeat": "daily"})

    def test_a_false_bool_becomes_once(self):
        block = {"id": 1, "daily": False}
        migrate_block(block)
        self.assertEqual(block["repeat"], "once")

    def test_a_block_with_neither_key_reads_as_daily(self):
        # What the old provider defaulted a hand-edited file to.
        block = {"id": 1}
        migrate_block(block)
        self.assertEqual(block["repeat"], "daily")

    def test_running_it_twice_changes_nothing_the_second_time(self):
        block = {"id": 1, "daily": False}
        migrate_block(block)
        self.assertFalse(migrate_block(block))
        self.assertEqual(block["repeat"], "once")

    def test_a_leftover_bool_is_dropped_rather_than_left_to_contradict(self):
        block = {"id": 1, "repeat": "weekly", "weekdays": [0], "daily": True}
        # Counted as a change, so the file it came from actually gets rewritten.
        self.assertTrue(migrate_block(block))
        self.assertNotIn("daily", block)

    def test_an_already_migrated_block_is_left_alone(self):
        block = {"id": 1, "repeat": "weekly", "weekdays": [0]}
        self.assertFalse(migrate_block(block))
        self.assertEqual(block, {"id": 1, "repeat": "weekly", "weekdays": [0]})

    def test_a_whole_data_directory_is_rewritten_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            path = data_dir / "42_schedule.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [],
                        "time_blocks": [
                            {"id": 1, "start": START.isoformat(), "end": END.isoformat(), "daily": True},
                            {"id": 2, "start": START.isoformat(), "end": END.isoformat(), "daily": False},
                        ],
                        "routines": [],
                    }
                ),
                encoding="utf-8",
            )

            files, blocks = migrate_data_dir(data_dir)
            self.assertEqual((files, blocks), (1, 2))

            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([b["repeat"] for b in migrated["time_blocks"]], ["daily", "once"])
            self.assertNotIn("daily", migrated["time_blocks"][0])

    def test_an_unreadable_file_does_not_stop_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "1_schedule.json").write_text("{not json", encoding="utf-8")
            (data_dir / "2_schedule.json").write_text(
                json.dumps({"time_blocks": [{"id": 1, "daily": True}]}), encoding="utf-8"
            )

            files, blocks = migrate_data_dir(data_dir)
            self.assertEqual((files, blocks), (1, 1))


class TestProviderStoresWhatItIsGiven(unittest.TestCase):
    """The one path that actually touches a user's file."""

    def test_a_weekly_block_comes_back_out_of_the_file_it_went_into(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(schedule_provider, "get_data_dir", return_value=Path(tmp)),
        ):
            provider = schedule_provider.ScheduleProvider()
            provider.add_time_block(42, TimeBlock(start=START, end=END, weekdays=[1, 3], name="gym"))

            stored = json.loads((Path(tmp) / "42_schedule.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["time_blocks"][0]["repeat"], "weekly")
            self.assertEqual(stored["time_blocks"][0]["weekdays"], [1, 3])

            (block,) = provider.list_time_blocks(42)
            self.assertEqual(block.weekdays, [1, 3])
            self.assertFalse(block.daily)


if __name__ == "__main__":
    unittest.main()
