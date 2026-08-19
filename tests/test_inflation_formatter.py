import datetime
import sys
import unittest
from decimal import Decimal
from pathlib import Path

# Setup path so we can import from src
# TODO: fix paths
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from modules.inflation_formatter import (  # noqa: E402
    MAX_COMMENT_LENGTH,
    build_record_pages,
    build_single_record_page,
    find_rate_gaps,
    format_money,
    format_percent,
    format_record,
    trim_to_whole_lines,
)


def make_record(record_id: int, amount: str, date: datetime.date, comment: str = "") -> dict:
    return {
        "id": record_id,
        "amount": Decimal(amount),
        "date": date,
        "comment": comment,
        "adjusted_value": Decimal(amount) * Decimal("1.5"),
        "loss_percent": Decimal("50"),
    }


class TestFormatMoney(unittest.TestCase):
    def test_groups_thousands_with_spaces(self):
        self.assertEqual(format_money(Decimal("1234567.891")), "1 234 567.89")

    def test_appends_currency(self):
        self.assertEqual(format_money(Decimal("10"), "UAH"), "10.00 UAH")

    def test_keeps_decimal_precision(self):
        # The value that motivates keeping Decimal instead of float all the way
        # down: float(0.1) + float(0.2) would render as 0.30000000000000004.
        self.assertEqual(format_money(Decimal("0.1") + Decimal("0.2")), "0.30")

    def test_percent_is_signed(self):
        self.assertEqual(format_percent(Decimal("12.345")), "+12.35%")
        self.assertEqual(format_percent(Decimal("0")), "+0.00%")


class TestFormatRecord(unittest.TestCase):
    def test_includes_id_amount_and_date(self):
        line = format_record(make_record(7, "5000", datetime.date(2024, 3, 12)), "UAH")

        self.assertIn("ID 7.", line)
        self.assertIn("5 000.00 UAH", line)
        self.assertIn("12.03.2024", line)
        self.assertIn("7 500.00 UAH", line)

    def test_long_comment_is_truncated(self):
        # A single record must not be able to blow the 2000-character message limit.
        line = format_record(make_record(1, "10", datetime.date(2024, 1, 1), "x" * 900))

        self.assertNotIn("x" * (MAX_COMMENT_LENGTH + 1), line)
        self.assertIn("…", line)

    def test_comment_is_optional(self):
        without = format_record(make_record(1, "10", datetime.date(2024, 1, 1)))
        with_comment = format_record(make_record(1, "10", datetime.date(2024, 1, 1), "gift"))

        self.assertNotIn("|  |", without)
        self.assertIn("| gift", with_comment)


class TestBuildRecordPages(unittest.TestCase):
    def setUp(self):
        self.records = [make_record(i, "100", datetime.date(2024, 1, 1)) for i in range(1, 21)]

    def test_no_records_yields_no_pages(self):
        self.assertEqual(build_record_pages([]), [])

    def test_single_page_when_it_fits(self):
        pages = build_record_pages(self.records[:3])

        self.assertEqual(len(pages), 1)
        for record in self.records[:3]:
            self.assertIn(f"ID {record['id']}.", pages[0])

    def test_pages_respect_the_limit(self):
        pages = build_record_pages(self.records, page_limit=200)

        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), 200)

    def test_every_record_survives_pagination(self):
        pages = build_record_pages(self.records, page_limit=200)
        joined = "\n".join(pages)

        for record in self.records:
            self.assertIn(f"ID {record['id']}.", joined)

    def test_oversized_record_gets_its_own_page(self):
        records = [
            make_record(1, "100", datetime.date(2024, 1, 1)),
            make_record(2, "100", datetime.date(2024, 1, 1), "x" * MAX_COMMENT_LENGTH),
            make_record(3, "100", datetime.date(2024, 1, 1)),
        ]

        pages = build_record_pages(records, page_limit=100)

        self.assertEqual(len(pages), 3)
        self.assertIn("x" * (MAX_COMMENT_LENGTH - 1), pages[1])


class TestBuildSingleRecordPage(unittest.TestCase):
    """The server report has no second page, so what does not fit is dropped."""

    def setUp(self):
        self.records = [make_record(i, "100", datetime.date(2024, 1, 1)) for i in range(1, 21)]

    def test_no_records_yields_an_empty_page(self):
        self.assertEqual(build_single_record_page([]), ("", 0))

    def test_shows_every_record_when_they_fit(self):
        page, shown = build_single_record_page(self.records[:3])

        self.assertEqual(shown, 3)
        for record in self.records[:3]:
            self.assertIn(f"ID {record['id']}.", page)

    def test_stops_at_the_limit_and_reports_how_many_fit(self):
        page, shown = build_single_record_page(self.records, page_limit=200)

        self.assertGreater(shown, 0)
        self.assertLess(shown, len(self.records))
        self.assertLessEqual(len(page), 200)

    def test_keeps_the_records_it_shows_whole(self):
        page, shown = build_single_record_page(self.records, page_limit=200)

        # Two lines per record, and the last one is not cut in half.
        self.assertEqual(len(page.split("\n")), shown * 2)
        for record in self.records[:shown]:
            self.assertIn(f"ID {record['id']}.", page)

    def test_shows_an_oversized_first_record_anyway(self):
        records = [make_record(1, "100", datetime.date(2024, 1, 1), "x" * MAX_COMMENT_LENGTH)]

        page, shown = build_single_record_page(records, page_limit=50)

        # A page saying only "0 of 1 records shown" would be worse than one
        # record over the budget, so the first record is never dropped.
        self.assertEqual(shown, 1)
        self.assertIn("ID 1.", page)


class TestTrimToWholeLines(unittest.TestCase):
    def test_short_text_is_returned_untouched(self):
        self.assertEqual(trim_to_whole_lines("one\ntwo", 100), "one\ntwo")

    def test_cuts_on_a_line_boundary(self):
        # Cutting mid-line would break the markdown the summary is written in.
        self.assertEqual(trim_to_whole_lines("one\ntwo\nthree", 9), "one\ntwo")

    def test_a_single_oversized_line_is_cut_hard(self):
        # There is no line boundary to fall back to here.
        self.assertEqual(trim_to_whole_lines("x" * 20, 5), "xxxxx")

    def test_never_returns_more_than_the_limit(self):
        text = "\n".join("line " + "y" * i for i in range(30))

        for limit in (1, 5, 40, 200):
            self.assertLessEqual(len(trim_to_whole_lines(text, limit)), limit)


class TestFindRateGaps(unittest.TestCase):
    today = datetime.date(2024, 6, 15)

    def rates(self, *keys: str) -> dict:
        return dict.fromkeys(keys, Decimal("1.01"))

    def test_empty_rates_report_no_gaps(self):
        # "No data at all" is a different condition and is reported separately.
        self.assertEqual(find_rate_gaps({}, today=self.today), [])

    def test_complete_history_has_no_gaps(self):
        rates = self.rates("2024-01", "2024-02", "2024-03", "2024-04", "2024-05")

        self.assertEqual(find_rate_gaps(rates, today=self.today), [])

    def test_reports_missing_months(self):
        rates = self.rates("2024-01", "2024-04", "2024-05")

        self.assertEqual(find_rate_gaps(rates, today=self.today), ["2024-02", "2024-03"])

    def test_current_month_is_not_a_gap(self):
        # June CPI is not published while June is still running.
        rates = self.rates("2024-05")

        self.assertEqual(find_rate_gaps(rates, today=self.today), [])

    def test_months_before_the_oldest_rate_are_not_gaps(self):
        rates = self.rates("2024-04", "2024-05")

        self.assertEqual(find_rate_gaps(rates, today=self.today), [])

    def test_gap_spanning_a_year_boundary(self):
        rates = self.rates("2023-11", "2024-02", "2024-03", "2024-04", "2024-05")

        self.assertEqual(find_rate_gaps(rates, today=self.today), ["2023-12", "2024-01"])


if __name__ == "__main__":
    unittest.main()
