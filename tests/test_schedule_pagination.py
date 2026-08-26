import datetime
import sys
import unittest
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from modules.schedule_timeline import ScheduleDay  # noqa: E402
from modules.schedule_pagination import (  # noqa: E402
    MESSAGE_LIMIT,
    MIN_PAGE_CHARS,
    PAGE_COUNTER_RESERVE,
    SchedulePage,
    build_notes,
    compress_id_runs,
    fit_items,
    frame_cost,
    invert_schedule_blocks,
    page_char_limit,
    paginate_days,
    trim_to_whole_lines,
)


def block(start: str, name: str, end: str) -> str:
    """A timeline block the way the formatter builds one: start on top, end below."""
    return f"{start}\n ├──> {name}\n{end}"


def make_day(date: datetime.date = datetime.date(2026, 1, 1), blocks: list[str] | None = None, **extra) -> ScheduleDay:
    return ScheduleDay(date=date, blocks=blocks or [], **extra)


class TestInvertScheduleBlocks(unittest.TestCase):
    def test_blocks_and_their_lines_are_both_reversed(self):
        blocks = ["08:00\nmorning\n09:00", "19:00\nevening\n20:00"]

        self.assertEqual(
            invert_schedule_blocks(blocks),
            ["20:00\nevening\n19:00", "09:00\nmorning\n08:00"],
        )

    def test_no_blocks_stay_no_blocks(self):
        self.assertEqual(invert_schedule_blocks([]), [])


class TestPaginateDays(unittest.TestCase):
    def test_a_day_that_fits_is_one_page_without_a_part_marker(self):
        day = make_day(blocks=[block("08:00", "morning", "09:00"), block("19:00", "evening", "20:00")])

        pages = paginate_days([day], char_limit=200)

        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].content.startswith("=== 01.01.2026 (Thursday) ===\n"))
        self.assertNotIn("Part", pages[0].content)

    def test_a_split_day_opens_on_its_morning(self):
        # The regression this module exists for: splitting the already-flipped
        # blocks used to put the end of the day on page 1 and its start on page 2.
        blocks = [
            block("08:00", "morning", "09:00"),
            block("13:00", "lunch", "14:00"),
            block("19:00", "evening", "20:00"),
        ]

        pages = paginate_days([make_day(blocks=blocks)], char_limit=70)

        self.assertEqual(len(pages), 3)
        self.assertIn("morning", pages[0].content)
        self.assertIn("lunch", pages[1].content)
        self.assertIn("evening", pages[2].content)

    def test_each_page_of_a_split_day_is_still_bottom_up(self):
        blocks = [
            block("08:00", "morning", "09:00"),
            block("10:00", "second", "11:00"),
            block("19:00", "evening", "20:00"),
        ]

        pages = paginate_days([make_day(blocks=blocks)], char_limit=100)

        self.assertEqual(len(pages), 2)
        first_body = pages[0].content.split("\n", 1)[1]
        # Latest block on top, and inside a block the end time above the start.
        self.assertEqual(
            first_body,
            "11:00\n ├──> second\n10:00\n09:00\n ├──> morning\n08:00",
        )

    def test_a_split_day_numbers_its_parts(self):
        blocks = [block("08:00", "morning", "09:00"), block("19:00", "evening", "20:00")]

        pages = paginate_days([make_day(blocks=blocks)], char_limit=60)

        self.assertEqual(
            [page.content.splitlines()[0] for page in pages],
            ["=== 01.01.2026 (Thursday) (Part 1/2) ===", "=== 01.01.2026 (Thursday) (Part 2/2) ==="],
        )

    def test_days_keep_their_order_and_each_page_carries_its_day(self):
        first = make_day(blocks=[block("08:00", "one", "09:00")])
        second = make_day(datetime.date(2026, 1, 2), blocks=[block("08:00", "two", "09:00")])

        pages = paginate_days([first, second], char_limit=200)

        self.assertEqual([page.date for page in pages], [datetime.date(2026, 1, 1), datetime.date(2026, 1, 2)])
        self.assertIn("01.01.2026", pages[0].content)
        self.assertIn("02.01.2026", pages[1].content)

    def test_every_page_of_a_day_carries_that_day_s_routines(self):
        blocks = [block("08:00", "morning", "09:00"), block("19:00", "evening", "20:00")]
        day = make_day(blocks=blocks, routine_ids={4, 7})

        pages = paginate_days([day], char_limit=60)

        self.assertEqual([page.routine_ids for page in pages], [{4, 7}, {4, 7}])

    def test_a_block_longer_than_the_budget_gets_a_page_of_its_own(self):
        blocks = [block("08:00", "short", "09:00"), block("10:00", "x" * 200, "11:00")]

        pages = paginate_days([make_day(blocks=blocks)], char_limit=80)

        self.assertEqual(len(pages), 2)
        self.assertIn("short", pages[0].content)
        self.assertIn("x" * 200, pages[1].content)

    def test_a_day_without_blocks_produces_no_page(self):
        self.assertEqual(paginate_days([make_day()], char_limit=200), [])

    def test_no_days_produce_no_pages(self):
        self.assertEqual(paginate_days([], char_limit=200), [])

    def test_a_page_defaults_to_no_routines_and_no_date(self):
        page = SchedulePage(content="text")

        self.assertEqual(page.routine_ids, set())
        self.assertIsNone(page.date)


class TestFrameCost(unittest.TestCase):
    def test_a_frame_costs_what_it_measures_plus_room_for_the_counter(self):
        self.assertEqual(frame_cost("x" * 200), 200 + PAGE_COUNTER_RESERVE)

    def test_a_page_source_header_is_paid_for_with_its_blank_line(self):
        self.assertEqual(frame_cost("x" * 200, "y" * 50), 200 + 50 + 2 + PAGE_COUNTER_RESERVE)

    def test_no_header_costs_nothing(self):
        self.assertEqual(frame_cost("x" * 200, ""), frame_cost("x" * 200))


class TestPageCharLimit(unittest.TestCase):
    def test_a_page_gets_what_the_frame_leaves(self):
        self.assertEqual(page_char_limit(500), MESSAGE_LIMIT - 500)

    def test_an_over_long_frame_cannot_starve_the_page(self):
        # The frame is trimmed instead — a page of nothing is not worth turning to.
        self.assertEqual(page_char_limit(MESSAGE_LIMIT + 100), MIN_PAGE_CHARS)


class TestFitItems(unittest.TestCase):
    def test_everything_that_fits_is_kept_whole(self):
        self.assertEqual(fit_items(["one", "two"], 100, ", "), "one, two")

    def test_the_overflow_note_says_what_was_cut(self):
        self.assertEqual(fit_items(["1", "2", "3", "4"], 8, ", ", lambda left: f"+{left}"), "1, 2, +2")

    def test_the_note_is_paid_for_out_of_the_budget(self):
        packed = fit_items([str(number) for number in range(50)], 20, ", ", lambda left: f"+{left}")

        self.assertLessEqual(len(packed), 20)
        self.assertTrue(packed.endswith(("+45", "+46", "+47")), packed)

    def test_a_budget_that_fits_nothing_leaves_the_note_alone(self):
        self.assertEqual(fit_items(["a very long item"], 6, ", ", lambda left: f"+{left}"), "+1")

    def test_a_budget_that_fits_not_even_the_note_gives_up(self):
        self.assertEqual(fit_items(["a very long item"], 1, ", ", lambda left: f"+{left}"), "")

    def test_no_items_produce_no_text(self):
        self.assertEqual(fit_items([], 100, ", ", lambda left: f"+{left}"), "")


class TestBuildNotes(unittest.TestCase):
    def test_nothing_skipped_says_nothing(self):
        self.assertEqual(build_notes([], [], 400), "")

    def test_both_lists_fit_on_one_line(self):
        notes = build_notes([7, 9], ["Morning run"], 400)

        self.assertEqual(notes, "\n*Didn't fit — tasks: 7, 9 · routines: Morning run*")

    def test_either_list_alone_drops_the_other_label(self):
        self.assertEqual(build_notes([7], [], 400), "\n*Didn't fit — tasks: 7*")
        self.assertEqual(build_notes([], ["Morning run"], 400), "\n*Didn't fit — routines: Morning run*")

    def test_a_run_of_skipped_ids_is_written_as_a_range(self):
        notes = build_notes([4, 5, 6, 7, 20], [], 400)

        self.assertEqual(notes, "\n*Didn't fit — tasks: 4-7, 20*")

    def test_the_notes_stay_inside_what_the_frame_leaves(self):
        frame_cost = 400

        notes = build_notes(list(range(400)), [f"Routine {number}" for number in range(80)], frame_cost)

        self.assertLessEqual(frame_cost + len(notes) + MIN_PAGE_CHARS, MESSAGE_LIMIT)

    def test_a_runaway_id_list_still_leaves_room_for_the_routines(self):
        notes = build_notes(list(range(400)), ["Morning run", "Reading"], 400)

        self.assertIn("Morning run", notes)
        self.assertIn("Reading", notes)

    def test_a_frame_that_leaves_no_room_drops_the_notes(self):
        self.assertEqual(build_notes([1, 2], ["Morning run"], MESSAGE_LIMIT), "")


class TestCompressIdRuns(unittest.TestCase):
    def test_a_run_collapses_into_a_range(self):
        self.assertEqual(compress_id_runs([1, 2, 3, 4]), ["1-4"])

    def test_gaps_break_a_run(self):
        self.assertEqual(compress_id_runs([1, 2, 4, 7, 8]), ["1-2", "4", "7-8"])

    def test_ids_are_sorted_and_deduplicated_first(self):
        self.assertEqual(compress_id_runs([9, 3, 1, 2, 3]), ["1-3", "9"])

    def test_lone_ids_stay_lone(self):
        self.assertEqual(compress_id_runs([5]), ["5"])

    def test_nothing_skipped_compresses_to_nothing(self):
        self.assertEqual(compress_id_runs([]), [])


class TestTrimToWholeLines(unittest.TestCase):
    def test_text_within_the_limit_is_untouched(self):
        self.assertEqual(trim_to_whole_lines("one\ntwo", 100), "one\ntwo")

    def test_trimming_stops_on_a_line_boundary(self):
        self.assertEqual(trim_to_whole_lines("one\ntwo\nthree", 9), "one\ntwo")

    def test_a_single_over_long_line_is_cut_mid_line(self):
        self.assertEqual(trim_to_whole_lines("x" * 40, 10), "x" * 10)


if __name__ == "__main__":
    unittest.main()
