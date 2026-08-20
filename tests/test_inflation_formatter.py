import datetime
import sys
import unittest
from decimal import Decimal
from pathlib import Path

# Setup path so we can import from src
# TODO: fix paths
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from tests.inflation_fixtures import (  # noqa: E402
    make_consumed,
    make_deposit,
    make_folded_record,
    make_node,
    make_record,
)
from modules.inflation_formatter import (  # noqa: E402
    LOT_SOURCE_DEPOSIT_INTEREST,
    MAX_COMMENT_LENGTH,
    Section,
    build_record_pages,
    build_single_record_page,
    deposit_fields,
    fold_consumed_lots,
    format_grand_total,
    format_group_heading,
    format_group_summary_line,
    format_money,
    format_percent,
    format_rate,
    format_record,
    indent_blocks,
    pack_blocks,
    pack_sections,
    pack_sections_single_page,
    pack_single_page,
    trim_to_whole_lines,
)


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


class TestPackBlocks(unittest.TestCase):
    """The packer `build_record_pages` and `build_single_record_page` sit on."""

    def blocks(self, count: int) -> list[str]:
        return [f"block-{n}" for n in range(count)]

    def test_nothing_in_nothing_out(self):
        self.assertEqual(pack_blocks([]), [])
        self.assertEqual(pack_single_page([]), ("", 0))

    def test_one_page_when_everything_fits(self):
        self.assertEqual(pack_blocks(self.blocks(3)), ["block-0\nblock-1\nblock-2"])

    def test_pages_respect_the_limit(self):
        pages = pack_blocks(self.blocks(20), page_limit=30)

        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), 30)

    def test_no_block_is_lost(self):
        blocks = self.blocks(20)
        joined = "\n".join(pack_blocks(blocks, page_limit=30))

        for block in blocks:
            self.assertIn(block, joined)

    def test_an_oversized_block_gets_its_own_page(self):
        # Nothing here can make it smaller, and dropping it would lose data.
        pages = pack_blocks(["a", "x" * 200, "b"], page_limit=50)

        self.assertEqual(pages, ["a", "x" * 200, "b"])

    def test_single_page_stops_at_the_limit_and_says_how_many_fit(self):
        page, shown = pack_single_page(self.blocks(20), page_limit=30)

        self.assertGreater(shown, 0)
        self.assertLess(shown, 20)
        self.assertLessEqual(len(page), 30)
        self.assertEqual(shown, len(page.split("\n")))

    def test_single_page_never_drops_an_oversized_first_block(self):
        # A page saying only "0 of 1 shown" would be worse than one long line.
        page, shown = pack_single_page(["x" * 200], page_limit=50)

        self.assertEqual((page, shown), ("x" * 200, 1))

    def test_the_record_wrappers_are_exactly_this_packer(self):
        # The two wrappers were extracted onto these packers; keep them honest.
        records = [make_record(i, "100", datetime.date(2024, 1, 1)) for i in range(1, 21)]
        blocks = [format_record(record, "UAH") for record in records]

        self.assertEqual(build_record_pages(records, "UAH", 200), pack_blocks(blocks, 200))
        self.assertEqual(build_single_record_page(records, "UAH", 200), pack_single_page(blocks, 200))


class TestPackSections(unittest.TestCase):
    """Group headings and the records filed under them."""

    def sections(self, count: int, blocks_each: int) -> list[Section]:
        return [
            Section(
                header=f"HEAD{index}",
                blocks=[f"block-{index}-{n}" for n in range(blocks_each)],
                continued_header=f"HEAD{index} (cont.)",
            )
            for index in range(count)
        ]

    def test_no_sections_yields_no_pages(self):
        self.assertEqual(pack_sections([]), [])

    def test_one_page_when_everything_fits(self):
        pages = pack_sections(self.sections(2, 2))

        self.assertEqual(len(pages), 1)
        self.assertIn("HEAD0", pages[0])
        self.assertIn("HEAD1", pages[0])

    def test_a_spilling_section_repeats_its_header(self):
        # Otherwise the reader sees records on page two with no idea whose they are.
        pages = pack_sections(self.sections(1, 20), page_limit=60)

        self.assertGreater(len(pages), 1)
        self.assertTrue(pages[0].startswith("HEAD0"))
        for page in pages[1:]:
            self.assertTrue(page.startswith("HEAD0 (cont.)"))

    def test_a_header_is_never_the_last_line_of_a_page(self):
        # A heading stranded at the bottom of a page is worse than a short page.
        pages = pack_sections(self.sections(4, 3), page_limit=70)

        for page in pages:
            self.assertFalse(page.split("\n")[-1].startswith("HEAD"))

    def test_every_block_survives(self):
        sections = self.sections(3, 4)
        joined = "\n".join(pack_sections(sections, page_limit=50))

        for section in sections:
            for block in section.blocks:
                self.assertIn(block, joined)

    def test_an_empty_section_is_still_announced(self):
        # An empty group is worth seeing: it explains where nothing landed.
        pages = pack_sections([Section(header="HEAD", blocks=[])])

        self.assertEqual(pages, ["HEAD"])

    def test_single_page_variant_counts_blocks_not_headers(self):
        page, shown = pack_sections_single_page(self.sections(3, 3), page_limit=80)

        self.assertGreater(shown, 0)
        self.assertLess(shown, 9)
        self.assertLessEqual(len(page), 80)
        self.assertEqual(shown, len([line for line in page.split("\n") if line.startswith("block-")]))


class TestGroupRendering(unittest.TestCase):
    def setUp(self):
        self.records = [
            make_record(1, "100", datetime.date(2024, 1, 1)),
            make_record(2, "200", datetime.date(2024, 2, 1)),
        ]
        self.node = make_node("Salary", self.records)

    def test_heading_carries_the_name_the_count_and_the_totals(self):
        heading = format_group_heading(self.node, "UAH")

        self.assertIn("[Salary] (2)", heading)
        self.assertIn("300.00", heading)
        self.assertIn("450.00 UAH", heading)
        self.assertEqual(len(heading.split("\n")), 2)

    def test_a_name_override_localises_the_ungrouped_bucket(self):
        # The library names it "(ungrouped)" in English; the bot has its own word.
        ungrouped = make_node("(ungrouped)", self.records, group_id=None)

        self.assertIn("[без групи]", format_group_heading(ungrouped, "UAH", "без групи"))

    def test_summary_line_is_one_line_with_the_same_numbers(self):
        line = format_group_summary_line(self.node, "UAH")
        heading = format_group_heading(self.node, "UAH")

        self.assertNotIn("\n", line)
        self.assertEqual(line, " ".join(part.strip() for part in heading.split("\n")))

    def test_grand_total_sums_every_node(self):
        report = {
            "groups": [self.node],
            "ungrouped": make_node("(ungrouped)", [make_record(3, "50", datetime.date(2024, 3, 1))], group_id=None),
            "total_nominal": Decimal("350"),
            "total_adjusted": Decimal("525"),
            "loss_percent": Decimal("50"),
        }

        total = format_grand_total(report, "TOTAL", "UAH")

        self.assertIn("[TOTAL] (3)", total)
        self.assertIn("525.00 UAH", total)

    def test_indenting_pushes_every_line_of_a_block_in(self):
        # A record is two lines; indenting only the first would break the column.
        block = format_record(self.records[0], "UAH")
        indented = indent_blocks([block])[0]

        self.assertEqual(len(indented.split("\n")), 2)
        for line in indented.split("\n"):
            self.assertTrue(line.startswith("    "))


if __name__ == "__main__":
    unittest.main()


class TestFormatRate(unittest.TestCase):
    def test_no_explicit_sign(self):
        """A deposit rate is not a delta, so it must not carry `format_percent`'s `+`."""
        self.assertEqual(format_rate(Decimal("15")), "15.00%")
        self.assertEqual(format_percent(Decimal("15")), "+15.00%")

    def test_rounds_half_up(self):
        self.assertEqual(format_rate(Decimal("16.075")), "16.08%")

    def test_accepts_plain_numbers(self):
        self.assertEqual(format_rate(18), "18.00%")
        self.assertEqual(format_rate("0"), "0.00%")


class TestDepositFields(unittest.TestCase):
    def test_every_field_is_a_string(self):
        fields = deposit_fields(make_deposit(), "UAH")

        self.assertTrue(all(isinstance(value, str) for value in fields.values()))

    def test_money_carries_the_currency(self):
        fields = deposit_fields(make_deposit(), "UAH")

        self.assertEqual(fields["earned"], "1 234.50 UAH")
        self.assertEqual(fields["projected"], "12 687.23 UAH")
        self.assertEqual(fields["projected_total"], "112 687.23 UAH")
        self.assertEqual(fields["at_risk"], "1 000.00 UAH")

    def test_rates_are_unsigned(self):
        fields = deposit_fields(make_deposit())

        self.assertEqual(fields["rate"], "15.00%")
        self.assertEqual(fields["effective_rate"], "16.08%")

    def test_dates_use_the_report_format(self):
        fields = deposit_fields(make_deposit())

        self.assertEqual(fields["start_date"], "01.01.2025")
        self.assertEqual(fields["end_date"], "01.01.2026")

    def test_missing_comment_is_empty_not_none(self):
        deposit = make_deposit()
        deposit["comment"] = None

        self.assertEqual(deposit_fields(deposit)["comment"], "")


class TestFormatCollapsedRecord(unittest.TestCase):
    def test_folded_row_shows_the_count_and_the_date_range(self):
        text = format_record(
            make_folded_record("12687.23", datetime.date(2025, 2, 1), datetime.date(2026, 1, 1)), "UAH"
        )

        self.assertIn("x12.", text)
        self.assertIn("01.02.2025…01.01.2026", text)
        self.assertIn("12 687.23 UAH", text)

    def test_folded_row_never_prints_a_null_id(self):
        """`id` is present and None on a folded row, so `.get(id, '?')` would leak `None`."""
        text = format_record(make_folded_record("100", datetime.date(2025, 2, 1), datetime.date(2026, 1, 1)))

        self.assertNotIn("None", text)
        self.assertNotIn("ID", text)

    def test_folded_row_keeps_the_adjusted_line(self):
        text = format_record(make_folded_record("100", datetime.date(2025, 2, 1), datetime.date(2026, 1, 1)), "UAH")

        self.assertIn("-> 150.00 UAH (+50.00%)", text)

    def test_single_record_is_unchanged(self):
        text = format_record(make_record(7, "100", datetime.date(2025, 2, 1), "lunch"), "UAH")

        self.assertIn("ID 7.", text)
        self.assertIn("01.02.2025", text)
        self.assertNotIn("x1.", text)

    def test_record_without_a_count_key_still_renders(self):
        """Records built before the library tagged them carry no `count`."""
        record = make_record(7, "100", datetime.date(2025, 2, 1))
        del record["count"]

        self.assertIn("ID 7.", format_record(record))


class TestFoldConsumedLots(unittest.TestCase):
    def make_interest(self, count: int) -> list[dict]:
        return [
            make_consumed(None, datetime.date(2025, month, 1), "100", "0", source=LOT_SOURCE_DEPOSIT_INTEREST)
            for month in range(1, count + 1)
        ]

    def test_interest_lots_fold_into_one_entry(self):
        folded = fold_consumed_lots(self.make_interest(12))

        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0]["count"], 12)
        self.assertEqual(folded[0]["taken"], Decimal("1200"))
        self.assertEqual(folded[0]["first_date"], datetime.date(2025, 1, 1))
        self.assertEqual(folded[0]["last_date"], datetime.date(2025, 12, 1))

    def test_manual_lots_are_passed_through(self):
        manual = [
            make_consumed(1, datetime.date(2024, 1, 1), "500", "0"),
            make_consumed(2, datetime.date(2024, 6, 1), "200", "300"),
        ]

        folded = fold_consumed_lots(manual + self.make_interest(3))

        self.assertEqual(len(folded), 3)
        self.assertEqual([entry["id"] for entry in folded[:2]], [1, 2])
        self.assertEqual(folded[-1]["count"], 3)

    def test_one_interest_lot_is_not_worth_folding(self):
        one = self.make_interest(1)

        self.assertEqual(fold_consumed_lots(one), one)

    def test_nothing_consumed_folds_to_nothing(self):
        self.assertEqual(fold_consumed_lots([]), [])

    def test_the_original_list_is_not_mutated(self):
        consumed = self.make_interest(3)

        fold_consumed_lots(consumed)

        self.assertEqual(len(consumed), 3)
