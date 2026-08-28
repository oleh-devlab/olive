import sys
import unittest
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from modules.llm_limits_formatter import (  # noqa: E402
    DEFAULT_CHAR_LIMIT,
    MAX_NAME_LEN,
    render_limits,
)


def status(name: str, **overrides) -> dict:
    """One model's snapshot, in the shape `ModelConfig.get_status()` hands over."""
    snapshot = {
        "model": name,
        "minute_req": "0/15",
        "day_req": "0/1500",
        "week_req": "0/∞",
        "minute_tokens": "0/250000",
        "day_tokens": 0,
        "week_tokens": 0,
        "available": True,
    }
    return {**snapshot, **overrides}


def spent(name: str, requests: int, tokens: int, **overrides) -> dict:
    """A model that has spent the same `requests` and `tokens` in every window."""
    return status(
        name,
        **{
            "minute_req": f"{requests}/15",
            "day_req": f"{requests}/1500",
            "week_req": f"{requests}/∞",
            "minute_tokens": f"{tokens}/250000",
            "day_tokens": tokens,
            "week_tokens": tokens,
            **overrides,
        },
    )


def key(roles: list[str], statuses: list[dict]) -> dict:
    return {"roles": roles, "status_list": statuses}


def body(text: str) -> list[str]:
    """The table's own lines, with the code fence taken off."""
    return text.strip("`").strip("\n").split("\n")


def line_for(text: str, name: str) -> str:
    return next(line for line in body(text) if line[1:].startswith(name))


class TestShape(unittest.TestCase):
    def test_the_table_comes_back_fenced(self):
        text = render_limits([key(["default"], [spent("m-one", 1, 100)])])
        self.assertTrue(text.startswith("```\n"))
        self.assertTrue(text.endswith("\n```"))

    def test_a_model_takes_a_single_line(self):
        clients = [key(["default"], [spent(f"m-{i}", i + 1, 100) for i in range(7)])]
        lines = body(render_limits(clients))

        # Header, totals, a blank line, the key's own line, then one line per model.
        self.assertEqual(len(lines), 4 + 7)
        for i in range(7):
            self.assertIn(f"m-{i}", lines[4 + i])

    def test_the_key_is_titled_with_every_role_sharing_it(self):
        text = render_limits([key(["default", "private"], [spent("m-one", 1, 100)])])
        self.assertIn("Default, Private:", text)

    def test_nothing_to_report_renders_nothing(self):
        self.assertEqual(render_limits([]), "")

    def test_the_models_keep_their_configured_order(self):
        # Best first is how the client tries them, and how an operator reads them.
        clients = [key(["default"], [spent("pro", 1, 10), spent("flash", 2, 20), spent("lite", 3, 30)])]
        names = [line[1:].split()[0] for line in body(render_limits(clients))[4:]]
        self.assertEqual(names, ["pro", "flash", "lite"])


class TestMarkers(unittest.TestCase):
    def test_a_blocked_model_is_marked_and_a_ready_one_is_not(self):
        clients = [
            key(
                ["default"],
                [spent("ready", 1, 100), spent("blocked", 15, 100, available=False)],
            )
        ]
        lines = body(render_limits(clients))
        self.assertTrue(line_for("\n".join(lines), "ready").startswith(" "))
        self.assertTrue(line_for("\n".join(lines), "blocked").startswith("!"))

    def test_the_marker_replaces_a_status_line(self):
        text = render_limits([key(["default"], [spent("m-one", 1, 100, available=False)])])
        self.assertNotIn("Status", text)
        self.assertNotIn("Unavailable", text)


class TestIdleModels(unittest.TestCase):
    def test_untouched_models_fold_into_one_line(self):
        clients = [key(["default"], [spent("busy", 3, 300)] + [status(f"quiet-{i}") for i in range(4)])]
        text = render_limits(clients)

        self.assertIn("busy", text)
        self.assertIn("+4 idle", text)
        for i in range(4):
            self.assertNotIn(f" quiet-{i} ", text)

    def test_the_folded_line_names_what_it_can_and_counts_the_rest(self):
        clients = [key(["default"], [spent("busy", 3, 300)] + [status(f"quiet-{i}" * 3) for i in range(6)])]
        idle_line = next(line for line in body(render_limits(clients)) if "idle" in line)

        self.assertTrue(idle_line.startswith("  +6 idle: "))
        self.assertTrue(idle_line.rstrip().endswith(tuple(f"+{n}" for n in range(1, 6))))

    def test_a_blocked_model_is_never_idle(self):
        # It has nothing spent because its window rolled, but it is the one thing to see.
        clients = [key(["default"], [status("blocked", available=False), status("quiet")])]
        text = render_limits(clients)

        self.assertIn("blocked", text)
        self.assertIn("+1 idle", text)

    def test_listing_everything_is_one_flag_away(self):
        clients = [key(["default"], [status(f"quiet-{i}") for i in range(4)])]
        text = render_limits(clients, collapse_idle=False)

        self.assertNotIn("idle", text)
        for i in range(4):
            self.assertIn(f"quiet-{i}", text)


class TestColumns(unittest.TestCase):
    def test_a_window_repeating_the_one_below_it_stays_out(self):
        # A fresh week has counted exactly what the day has, twice over.
        text = render_limits([key(["default"], [spent("m-one", 3, 300)])])

        self.assertIn("rpm", text)
        self.assertIn("rpd", text)
        self.assertNotIn("rpw", text)
        self.assertNotIn("tpd", text)
        self.assertNotIn("tpw", text)

    def test_a_window_with_a_cap_of_its_own_is_always_shown(self):
        text = render_limits([key(["default"], [spent("m-one", 3, 300, week_req="3/10000")])])
        self.assertIn("rpw", text)
        self.assertIn("3/10000", text)

    def test_a_window_that_has_come_apart_is_shown(self):
        # Yesterday's tokens are still inside the week but no longer inside the day.
        text = render_limits([key(["default"], [spent("m-one", 3, 300, week_tokens=9000)])])

        self.assertIn("tpw", text)
        self.assertIn("9k", text)
        self.assertNotIn("tpd", text)

    def test_an_absent_cap_is_written_as_infinity_only_where_the_column_has_caps(self):
        clients = [key(["default"], [spent("capped", 1, 10, week_req="1/500"), spent("open", 2, 20)])]
        text = render_limits(clients)

        self.assertIn("1/500", line_for(text, "capped"))
        self.assertIn("2/∞", line_for(text, "open"))


class TestTotals(unittest.TestCase):
    def test_the_totals_add_every_key_up(self):
        clients = [
            key(["default"], [spent("m-one", 3, 300), spent("m-two", 4, 400)]),
            key(["private"], [spent("m-one", 5, 500)]),
        ]
        totals = line_for(render_limits(clients), "Total")

        self.assertIn("12", totals)  # 3 + 4 + 5 requests
        self.assertIn("1.2k", totals)  # 300 + 400 + 500 tokens

    def test_the_totals_claim_no_caps_of_their_own(self):
        # Caps belong to a model under one API key; they do not add up across keys.
        totals = line_for(render_limits([key(["default"], [spent("m-one", 3, 300)])]), "Total")
        self.assertNotIn("/", totals)
        self.assertNotIn("∞", totals)

    def test_the_totals_stay_the_pool_s_even_when_the_table_is_trimmed(self):
        clients = [key(["default"], [spent(f"m-{i}", 2, 200, available=False) for i in range(12)])]
        full = line_for(render_limits(clients), "Total")
        trimmed = render_limits(clients, char_limit=260)

        self.assertIn("not shown", trimmed)
        self.assertEqual(line_for(trimmed, "Total").split()[:3], full.split()[:3])


class TestNames(unittest.TestCase):
    def test_the_family_every_model_shares_is_lifted_into_the_header(self):
        clients = [key(["default"], [spent("gemini-2.5-pro", 1, 10), spent("gemini-2.0-flash", 2, 20)])]
        text = render_limits(clients)

        self.assertIn("gemini-*", text)
        self.assertIn(" 2.5-pro", text)
        self.assertIn(" 2.0-flash", text)
        self.assertNotIn(" gemini-2.5-pro", text)

    def test_models_from_different_families_keep_their_full_names(self):
        clients = [key(["default"], [spent("gemini-2.5-pro", 1, 10), spent("gemma-3-27b", 2, 20)])]
        text = render_limits(clients)

        self.assertIn("gemini-2.5-pro", text)
        self.assertIn("gemma-3-27b", text)
        self.assertIn("model", text)

    def test_a_single_model_is_left_alone(self):
        text = render_limits([key(["default"], [spent("gemini-2.5-pro", 1, 10)])])
        self.assertIn("gemini-2.5-pro", text)

    def test_a_family_is_left_whole_when_cutting_it_off_would_leave_a_stub(self):
        clients = [key(["default"], [spent("model-name-a", 1, 10), spent("model-name-b", 2, 20)])]
        text = render_limits(clients)

        self.assertIn("model-name-a", text)
        self.assertNotIn("model-name-*", text)

    def test_a_family_is_cut_at_a_name_boundary_and_never_inside_a_version(self):
        clients = [key(["default"], [spent("gemini-2.5-pro", 1, 10), spent("gemini-2.0-pro", 2, 20)])]
        text = render_limits(clients)

        self.assertIn("gemini-*", text)
        self.assertIn(" 2.5-pro", text)

    def test_a_long_name_is_cut_through_the_middle_so_both_ends_survive(self):
        long_names = ["prefix-2.5-flash-preview-lite", "prefix-2.0-flash-preview-lite"]
        clients = [key(["default"], [spent(name, i + 1, 10) for i, name in enumerate(long_names)])]
        rendered = [line[1:].split()[0] for line in body(render_limits(clients))[4:]]

        self.assertEqual(len(set(rendered)), 2, "the cut must not turn two models into one")
        for shortened in rendered:
            self.assertLessEqual(len(shortened), MAX_NAME_LEN)
            self.assertIn("…", shortened)
            self.assertTrue(shortened.endswith("-lite"))


class TestNumbers(unittest.TestCase):
    def test_token_counts_are_written_short(self):
        clients = [key(["default"], [spent("m-one", 1, 4_100, week_tokens=2_500_000)])]
        text = render_limits(clients)

        self.assertIn("4.1k", text)
        self.assertIn("2.5M", text)

    def test_a_round_thousand_drops_its_decimal(self):
        text = render_limits([key(["default"], [spent("m-one", 1, 15_000)])])
        self.assertIn("15k", text)
        self.assertNotIn("15.0k", text)

    def test_request_counts_are_left_as_they_are(self):
        text = render_limits([key(["default"], [spent("m-one", 1200, 10, day_req="1200/14400")])])
        self.assertIn("1200/14400", text)


class TestAlignment(unittest.TestCase):
    def test_every_column_lands_in_the_same_place_on_every_line(self):
        clients = [
            key(["default"], [spent("m-one", 1, 10), spent("m-two", 15, 250_000, available=False)]),
            key(["private"], [spent("a-much-longer-name", 1200, 3_400_000, day_req="1200/14400")]),
        ]
        model_lines = [line for line in body(render_limits(clients)) if "/" in line]

        self.assertGreater(len(model_lines), 1)
        slashes = {tuple(i for i, char in enumerate(line) if char == "/") for line in model_lines}
        self.assertEqual(len(slashes), 1, f"columns drifted between lines: {model_lines}")


class TestFittingIntoTheEmbed(unittest.TestCase):
    def _clients(self, models: int = 9, keys: int = 2) -> list[dict]:
        return [
            key([f"role-{k}"], [spent(f"model-name-{i}-x", i + 1, 100 * (i + 1)) for i in range(models)])
            for k in range(keys)
        ]

    def test_a_full_table_is_nowhere_near_the_embed_limit(self):
        self.assertLess(len(render_limits(self._clients(), collapse_idle=False)), DEFAULT_CHAR_LIMIT)

    def test_folding_the_idle_models_away_is_what_is_given_up_first(self):
        clients = self._clients(models=9)
        clients[0]["status_list"] += [status("quiet-one"), status("quiet-two")]
        full = render_limits(clients, collapse_idle=False)

        fitted = render_limits(clients, collapse_idle=False, char_limit=len(full) - 1)
        self.assertIn("idle", fitted)
        self.assertLessEqual(len(fitted), len(full) - 1)

    def test_columns_go_before_models_do(self):
        clients = self._clients()
        for line in clients[0]["status_list"]:
            line["week_req"] = "1/9999"  # give the week column a reason to be there

        full = render_limits(clients)
        self.assertIn("rpw", full)

        fitted = render_limits(clients, char_limit=len(full) - 1)
        self.assertNotIn("rpw", fitted)
        self.assertNotIn("not shown", fitted)
        self.assertEqual(len([line for line in body(fitted) if "/" in line]), 18)

    def test_models_are_only_ever_dropped_last_and_are_counted_when_they_are(self):
        fitted = render_limits(self._clients(), char_limit=300)

        self.assertLessEqual(len(fitted), 300)
        self.assertRegex(fitted, r"\+\d+ not shown")

    def test_a_budget_nothing_fits_in_is_still_cut_to_size(self):
        self.assertLessEqual(len(render_limits(self._clients(), char_limit=40)), 40)


class TestLabels(unittest.TestCase):
    def test_every_word_of_it_can_be_translated(self):
        labels = {"total": "Разом", "idle": "без запитів", "hidden": "приховано", "models": "модель"}
        clients = [key(["default"], [spent("m-one", 1, 10), status("m-two")])]
        text = render_limits(clients, labels=labels)

        self.assertIn("Разом", text)
        self.assertIn("+1 без запитів", text)
        self.assertIn("модель", text)

    def test_a_missing_or_broken_labels_section_falls_back_rather_than_raises(self):
        clients = [key(["default"], [spent("m-one", 1, 10)])]
        for labels in (None, {}, [], "Total", {"total": "Разом"}):
            with self.subTest(labels=labels):
                self.assertIn("rpm", render_limits(clients, labels=labels))


class TestSnapshotTolerance(unittest.TestCase):
    def test_a_hand_edited_state_file_does_not_take_the_embed_down(self):
        broken = status("m-one", minute_req="lots/many", day_tokens=None, week_req="")
        text = render_limits([key(["default"], [broken])])
        self.assertIn("m-one", text)

    def test_a_key_with_no_models_is_shown_as_the_empty_thing_it_is(self):
        text = render_limits([key(["default"], []), key(["private"], [spent("m-one", 1, 10)])])
        self.assertIn("Default:", text)
        self.assertIn("m-one", text)


if __name__ == "__main__":
    unittest.main()
