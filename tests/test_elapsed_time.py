import sys
import unittest
from datetime import date, datetime
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from modules.elapsed_time import (  # noqa: E402
    Elapsed,
    add_months,
    decline,
    format_elapsed_breakdown,
    format_elapsed_total,
    measure,
    parse_date,
)

HOURS = ["година", "години", "годин"]
DAYS = ["день", "дні", "днів"]
YEARS = ["рік", "роки", "років"]
MONTHS = ["місяць", "місяці", "місяців"]


class TestParseDate(unittest.TestCase):
    def test_reads_every_accepted_spelling(self):
        for text in ("2024-01-15", "2024-01-15 18:30", "2024-01-15T18:30:45", "15.01.2024"):
            self.assertEqual(parse_date(text).date(), date(2024, 1, 15), text)

    def test_reads_the_time_when_it_is_given(self):
        self.assertEqual(parse_date("2024-01-15 18:30"), datetime(2024, 1, 15, 18, 30))

    def test_a_date_without_a_time_starts_at_midnight(self):
        self.assertEqual(parse_date("2024-01-15"), datetime(2024, 1, 15, 0, 0))

    def test_surrounding_whitespace_is_allowed(self):
        self.assertEqual(parse_date("  2024-01-15  "), datetime(2024, 1, 15))

    def test_accepts_date_and_datetime_objects(self):
        self.assertEqual(parse_date(date(2024, 1, 15)), datetime(2024, 1, 15))
        self.assertEqual(parse_date(datetime(2024, 1, 15, 7, 5)), datetime(2024, 1, 15, 7, 5))

    def test_drops_the_timezone_so_the_caller_attaches_its_own(self):
        # The comparison runs on the bot's clock; a half-aware pair would raise.
        aware = datetime.fromisoformat("2024-01-15T18:30:00+05:00")

        self.assertIsNone(parse_date(aware).tzinfo)

    def test_unreadable_values_return_none(self):
        # settings.py is hand-written: a typo must not take the cog's import down.
        for value in ("", "not a date", "2024-13-45", "15/01/2024", None, 1705276800, []):
            self.assertIsNone(parse_date(value), value)


class TestAddMonths(unittest.TestCase):
    def test_clamps_to_the_length_of_the_target_month(self):
        self.assertEqual(add_months(datetime(2024, 1, 31), 1), datetime(2024, 2, 29))
        self.assertEqual(add_months(datetime(2023, 1, 31), 1), datetime(2023, 2, 28))

    def test_crosses_the_year_boundary(self):
        self.assertEqual(add_months(datetime(2024, 11, 10), 3), datetime(2025, 2, 10))
        self.assertEqual(add_months(datetime(2024, 2, 10), -3), datetime(2023, 11, 10))

    def test_keeps_the_time_of_day(self):
        self.assertEqual(add_months(datetime(2024, 1, 10, 18, 30, 5), 14), datetime(2025, 3, 10, 18, 30, 5))


class TestMeasure(unittest.TestCase):
    def test_counts_whole_hours_and_days(self):
        elapsed = measure(datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 3, 5, 59))

        self.assertEqual(elapsed.total_hours, 53)
        self.assertEqual(elapsed.total_days, 2)

    def test_a_partial_hour_is_not_counted_yet(self):
        self.assertEqual(measure(datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 0, 59)).total_hours, 0)

    def test_breaks_the_span_into_years_months_and_days(self):
        elapsed = measure(datetime(2022, 3, 15), datetime(2024, 6, 20))

        self.assertEqual((elapsed.years, elapsed.months, elapsed.days), (2, 3, 5))

    def test_the_day_of_the_month_not_yet_reached_borrows_a_month(self):
        elapsed = measure(datetime(2024, 1, 20), datetime(2024, 3, 10))

        # Not "2 months minus 10 days": one whole month, then the days since 20 February.
        self.assertEqual((elapsed.years, elapsed.months, elapsed.days), (0, 1, 19))

    def test_the_end_of_the_month_does_not_walk_the_count_forward(self):
        # 31 January to 1 March is a month and a day or two, never two months —
        # the subtract-the-fields approach has no February 30th to borrow from.
        elapsed = measure(datetime(2024, 1, 31), datetime(2024, 3, 1))

        self.assertEqual((elapsed.years, elapsed.months, elapsed.days), (0, 1, 1))

    def test_the_time_of_day_decides_the_last_whole_day(self):
        elapsed = measure(datetime(2024, 1, 15, 18, 0), datetime(2024, 2, 15, 9, 0))

        self.assertEqual((elapsed.years, elapsed.months, elapsed.days), (0, 0, 30))

    def test_an_exact_anniversary_has_no_remainder(self):
        elapsed = measure(datetime(2020, 2, 29), datetime(2024, 2, 29))

        self.assertEqual((elapsed.years, elapsed.months, elapsed.days), (4, 0, 0))

    def test_a_future_date_reads_as_zeros(self):
        # An operator who mistyped the year gets an unstarted counter, not minus signs.
        self.assertEqual(measure(datetime(2030, 1, 1), datetime(2024, 1, 1)), Elapsed(0, 0, 0, 0, 0))

    def test_the_same_moment_reads_as_zeros(self):
        self.assertEqual(measure(datetime(2024, 1, 1), datetime(2024, 1, 1)), Elapsed(0, 0, 0, 0, 0))


class TestDecline(unittest.TestCase):
    def test_picks_the_form_by_the_last_digits(self):
        cases = {1: "день", 2: "дні", 4: "дні", 5: "днів", 11: "днів", 14: "днів", 21: "день", 112: "днів"}

        for number, word in cases.items():
            self.assertEqual(decline(number, DAYS), f"{number} {word}")

    def test_zero_takes_the_plural_form(self):
        self.assertEqual(decline(0, DAYS), "0 днів")

    def test_a_short_forms_list_is_padded_rather_than_raising(self):
        # The forms come from phrases.json, which an operator edits by hand.
        self.assertEqual(decline(5, ["день"]), "5 день")
        self.assertEqual(decline(3, []), "3")


class TestFormatElapsedTotal(unittest.TestCase):
    def test_counts_in_hours_on_the_first_day(self):
        elapsed = measure(datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 2, 2, 0))

        self.assertEqual(format_elapsed_total(elapsed, HOURS, DAYS), "26 годин")

    def test_switches_to_days_once_there_is_more_than_one(self):
        elapsed = measure(datetime(2024, 1, 1), datetime(2024, 1, 3))

        self.assertEqual(format_elapsed_total(elapsed, HOURS, DAYS), "2 дні")

    def test_a_fresh_date_still_renders(self):
        self.assertEqual(
            format_elapsed_total(measure(datetime(2024, 1, 1), datetime(2024, 1, 1)), HOURS, DAYS), "0 годин"
        )


class TestFormatElapsedBreakdown(unittest.TestCase):
    def test_renders_all_three_units(self):
        elapsed = measure(datetime(2022, 3, 15), datetime(2024, 6, 20))

        self.assertEqual(format_elapsed_breakdown(elapsed, YEARS, MONTHS, DAYS), "2 роки 3 місяці 5 днів")

    def test_drops_the_empty_units(self):
        elapsed = measure(datetime(2022, 3, 15), datetime(2024, 3, 20))

        self.assertEqual(format_elapsed_breakdown(elapsed, YEARS, MONTHS, DAYS), "2 роки 5 днів")

    def test_keeps_the_days_when_everything_is_empty(self):
        elapsed = measure(datetime(2024, 1, 1), datetime(2024, 1, 1, 5))

        self.assertEqual(format_elapsed_breakdown(elapsed, YEARS, MONTHS, DAYS), "0 днів")

    def test_an_exact_anniversary_is_just_the_years(self):
        elapsed = measure(datetime(2020, 6, 1), datetime(2024, 6, 1))

        self.assertEqual(format_elapsed_breakdown(elapsed, YEARS, MONTHS, DAYS), "4 роки")


if __name__ == "__main__":
    unittest.main()
