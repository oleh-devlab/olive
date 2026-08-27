"""Tests for what the solver's time costs add up to.

The module reaches a database, so this suite gives it a temporary one, built by
the real migrations — nothing here touches the bot's own `olive.sqlite3`.
"""

import datetime
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from database.migrations import MigrationRunner  # noqa: E402
from modules import schedule_stats  # noqa: E402


class FakeDatabase:
    """The slice of `DatabaseManager` the stats module uses."""

    def __init__(self, connection: sqlite3.Connection):
        self.conn = connection

    def execute(self, query: str, params: tuple = ()) -> list:
        with self.conn:
            return self.conn.cursor().execute(query, params).fetchall()


class StatsTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

        connection = sqlite3.connect(Path(self.directory.name) / "test.sqlite3")
        self.addCleanup(connection.close)
        MigrationRunner(connection).migrate()

        schedule_stats.use(FakeDatabase(connection))
        self.addCleanup(schedule_stats.use, None)

        self.connection = connection

    def store(self, day: datetime.date, user_id: int, seconds: float, solves: int = 1):
        """A row as some earlier day would have left it."""
        with self.connection:
            self.connection.execute(
                "INSERT INTO schedule_solve_stats (day, user_id, seconds, solves) VALUES (?, ?, ?, ?)",
                (day.isoformat(), user_id, seconds, solves),
            )

    def days_ago(self, days: int) -> datetime.date:
        return datetime.date.fromisoformat(schedule_stats.today()) - datetime.timedelta(days=days)


class TestRecord(StatsTestCase):
    def test_a_solve_lands_on_today_under_its_user(self):
        schedule_stats.record(7, 1.5)

        rows = self.connection.execute("SELECT day, user_id, seconds, solves FROM schedule_solve_stats").fetchall()

        self.assertEqual(rows, [(schedule_stats.today(), 7, 1.5, 1)])

    def test_the_day_s_row_is_added_to_rather_than_repeated(self):
        for seconds in (1.5, 0.5, 2.0):
            schedule_stats.record(7, seconds)

        rows = self.connection.execute("SELECT seconds, solves FROM schedule_solve_stats").fetchall()

        self.assertEqual(rows, [(4.0, 3)])

    def test_two_users_keep_their_own_rows(self):
        schedule_stats.record(7, 1.0)
        schedule_stats.record(9, 2.0)

        self.assertEqual(schedule_stats.totals(), (3.0, 2))
        self.assertEqual(len(self.connection.execute("SELECT * FROM schedule_solve_stats").fetchall()), 2)

    def test_a_solve_that_never_ran_is_not_counted(self):
        # No tasks and no routines means the solver was never asked anything.
        schedule_stats.record(7, 0.0)

        self.assertEqual(schedule_stats.totals(), (0.0, 0))

    def test_a_database_that_refuses_the_write_does_not_cost_the_solve(self):
        class Broken:
            def execute(self, query, params=()):
                raise sqlite3.OperationalError("disk is full")

        schedule_stats.use(Broken())

        with self.assertLogs("modules.schedule_stats", level="ERROR"):
            schedule_stats.record(7, 1.0)  # says so in the log and carries on


class TestTotals(StatsTestCase):
    def setUp(self):
        super().setUp()
        self.store(self.days_ago(0), 7, 10.0, solves=2)
        self.store(self.days_ago(3), 7, 20.0, solves=4)
        self.store(self.days_ago(20), 9, 30.0, solves=6)
        self.store(self.days_ago(200), 9, 40.0, solves=8)

    def test_everything_ever_recorded(self):
        self.assertEqual(schedule_stats.totals(), (100.0, 20))

    def test_today_alone(self):
        self.assertEqual(schedule_stats.totals(days=1), (10.0, 2))

    def test_a_week_reaches_back_six_days_and_includes_today(self):
        self.assertEqual(schedule_stats.totals(days=7), (30.0, 6))

    def test_a_month_reaches_further(self):
        self.assertEqual(schedule_stats.totals(days=30), (60.0, 12))

    def test_nothing_recorded_is_zero_rather_than_nothing(self):
        self.connection.execute("DELETE FROM schedule_solve_stats")

        self.assertEqual(schedule_stats.totals(), (0.0, 0))


class TestTopUsers(StatsTestCase):
    def setUp(self):
        super().setUp()
        self.store(self.days_ago(0), 7, 5.0)
        self.store(self.days_ago(1), 7, 5.0)
        self.store(self.days_ago(0), 9, 30.0)
        self.store(self.days_ago(40), 11, 100.0)

    def test_the_dearest_user_comes_first(self):
        self.assertEqual([user for user, _, _ in schedule_stats.top_users()], [11, 9, 7])

    def test_a_user_s_days_are_added_up(self):
        self.assertIn((7, 10.0, 2), schedule_stats.top_users())

    def test_the_list_is_cut_where_asked(self):
        self.assertEqual(len(schedule_stats.top_users(limit=2)), 2)

    def test_a_window_leaves_out_who_was_quiet_in_it(self):
        recent = [user for user, _, _ in schedule_stats.top_users(days=7)]

        self.assertEqual(recent, [9, 7])

    def test_nobody_recorded_is_nobody_listed(self):
        self.connection.execute("DELETE FROM schedule_solve_stats")

        self.assertEqual(schedule_stats.top_users(), [])


class TestCountingSince(StatsTestCase):
    def test_the_first_day_anything_was_recorded(self):
        self.store(self.days_ago(200), 7, 1.0)
        self.store(self.days_ago(3), 7, 1.0)

        self.assertEqual(schedule_stats.counting_since(), self.days_ago(200).isoformat())

    def test_nothing_recorded_yet_says_nothing(self):
        self.assertIsNone(schedule_stats.counting_since())


class TestFormatting(unittest.TestCase):
    def test_seconds_keep_a_decimal(self):
        self.assertEqual(schedule_stats.format_duration(2.44), "2.4s")

    def test_a_minute_is_where_seconds_stop_being_useful(self):
        self.assertEqual(schedule_stats.format_duration(60), "1m 00s")
        self.assertEqual(schedule_stats.format_duration(200), "3m 20s")

    def test_an_hour_drops_the_seconds(self):
        self.assertEqual(schedule_stats.format_duration(3900), "1h 05m")

    def test_nothing_spent_reads_as_zero(self):
        self.assertEqual(schedule_stats.format_duration(0), "0.0s")

    def test_a_day_is_shown_the_way_the_schedule_shows_days(self):
        self.assertEqual(schedule_stats.format_day("2026-08-26"), "26.08.2026")

    def test_no_day_at_all_says_so(self):
        self.assertEqual(schedule_stats.format_day(None), "N/A")

    def test_a_day_that_is_not_one_is_passed_through(self):
        self.assertEqual(schedule_stats.format_day("whenever"), "whenever")


if __name__ == "__main__":
    unittest.main()
