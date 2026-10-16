"""Tests for `parse_deadline`, the one reader of a deadline for every way a task changes.

`/task add` and the agent hand it a deadline as typed; the web editor hands it
back the ISO timestamp the provider stored. Both have to land on the same
moment, in the bot's timezone, or a task saved unchanged from the page would
drift.

`schedule_validators` reaches the provider, which pulls in the vendored
dataclasses -- and `ortools` with them -- and, through
`core.personal_channels`, `disnake`. This suite needs all three present.
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

# The validators import the operator's `settings` module, absent from a checkout.
sys.modules.setdefault("settings", types.ModuleType("settings"))

from core.time_utils import tz  # noqa: E402
from modules.schedule_exceptions import ScheduleValidationError  # noqa: E402
from modules.schedule_validators import parse_deadline  # noqa: E402

EVENING = datetime.datetime(2026, 10, 20, 18, 0, tzinfo=tz)


class TestParseDeadline(unittest.TestCase):
    def test_typed_and_stored_spellings_are_the_same_moment(self):
        for spelling in ("20.10.2026 18:00", "2026-10-20T18:00", EVENING.isoformat()):
            with self.subTest(spelling=spelling):
                self.assertEqual(parse_deadline(spelling), EVENING)

    def test_an_offset_is_kept_as_a_moment_and_moved_into_the_bots_timezone(self):
        parsed = parse_deadline("2026-10-20T15:00:00+00:00")
        self.assertEqual(parsed, datetime.datetime(2026, 10, 20, 15, 0, tzinfo=datetime.UTC))
        self.assertEqual(parsed.utcoffset(), EVENING.utcoffset())

    def test_no_deadline(self):
        for empty in (None, "", "none", "None"):
            with self.subTest(empty=empty):
                self.assertIsNone(parse_deadline(empty))

    def test_anything_else_is_refused(self):
        for junk in ("tomorrow", "20/10/2026 18:00", "2026-13-01T00:00"):
            with self.subTest(junk=junk), self.assertRaises(ScheduleValidationError):
                parse_deadline(junk)


if __name__ == "__main__":
    unittest.main()
