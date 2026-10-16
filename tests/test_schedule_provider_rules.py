"""Tests for the task rules `ScheduleProvider` holds for every way a task changes.

`/task`, the agent and the web editor all add and spend through the provider,
so a rule kept here is kept by all three -- which is the point: the task limit
used to be checked by the slash command alone, and the agent went around it.

Reaching the provider pulls in the vendored dataclasses -- whose package
re-exports the scheduler, so `ortools` comes with them -- and, through
`core.personal_channels`, `disnake`. This suite needs all three present.
"""

import datetime
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
sys.modules.setdefault("settings", types.ModuleType("settings"))

import settings  # noqa: E402

from modules import schedule_provider  # noqa: E402
from modules.schedule_agent_tools import ScheduleAgentTools  # noqa: E402
from modules.schedule_exceptions import ScheduleItemNotFoundError, ScheduleLimitError  # noqa: E402
from modules.schedule_models import Task  # noqa: E402

USER = 42


def task(name: str = "Read", minutes: int = 30) -> Task:
    return Task(name=name, duration=datetime.timedelta(minutes=minutes))


class ProviderTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(schedule_provider, "get_data_dir", return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.provider = schedule_provider.ScheduleProvider()

    def limit(self, n: int):
        patcher = mock.patch.object(settings, "schedule_max_tasks_per_user", n, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestTaskLimit(ProviderTestCase):
    def test_a_task_past_the_limit_is_refused_and_not_stored(self):
        self.limit(2)
        self.provider.add_task(USER, task("One"))
        self.provider.add_task(USER, task("Two"))

        with self.assertRaises(ScheduleLimitError):
            self.provider.add_task(USER, task("Three"))

        self.assertEqual([t.name for t in self.provider.list_tasks(USER)], ["One", "Two"])

    def test_completed_tasks_do_not_count(self):
        self.limit(1)
        done = self.provider.add_task(USER, task("Done", minutes=30))
        self.provider.spend_task_time(USER, done, 30)

        self.provider.add_task(USER, task("Next"))
        self.assertEqual([t.name for t in self.provider.list_tasks(USER)], ["Next"])

    def test_the_limit_is_per_user(self):
        self.limit(1)
        self.provider.add_task(USER, task())
        self.provider.add_task(USER + 1, task())
        self.assertEqual(len(self.provider.list_tasks(USER + 1)), 1)

    def test_the_agent_is_held_to_it_too(self):
        self.limit(1)
        tools = ScheduleAgentTools(USER)
        tools.add_task(name="First", duration_min=30)

        with self.assertRaises(ScheduleLimitError):
            tools.add_task(name="Second", duration_min=30)

        self.assertEqual(len(self.provider.list_tasks(USER)), 1)


class TestSpendTaskTime(ProviderTestCase):
    def test_spending_nothing_or_less_is_refused_and_leaves_the_task_alone(self):
        task_id = self.provider.add_task(USER, task(minutes=60))

        for minutes in (0, -5):
            with self.subTest(minutes=minutes), self.assertRaisesRegex(ValueError, "minutes must be > 0"):
                self.provider.spend_task_time(USER, task_id, minutes)

        self.assertEqual(self.provider.get_task(USER, task_id).duration, datetime.timedelta(minutes=60))

    def test_the_minutes_are_checked_before_the_task_is_looked_for(self):
        # So `/task spend` says what is wrong with what was typed, whichever
        # id it was typed against.
        with self.assertRaisesRegex(ValueError, "minutes must be > 0"):
            self.provider.spend_task_time(USER, 999, 0)

    def test_a_task_that_is_not_there_says_so_in_its_own_type(self):
        # Its own type, so the web editor can answer 404 without looking the
        # task up first; still a ValueError, which is what callers caught before.
        with self.assertRaises(ScheduleItemNotFoundError) as caught:
            self.provider.spend_task_time(USER, 999, 10)
        self.assertIsInstance(caught.exception, ValueError)

    def test_the_agent_gets_the_same_answer(self):
        task_id = self.provider.add_task(USER, task(minutes=60))
        with self.assertRaisesRegex(ValueError, "minutes must be > 0"):
            ScheduleAgentTools(USER).spend_task_time(task_id, 0)


if __name__ == "__main__":
    unittest.main()
