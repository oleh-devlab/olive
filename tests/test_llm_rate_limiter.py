import json
import sys
import unittest
from pathlib import Path
from typing import ClassVar

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from modules.llm_rate_limiter import ModelConfig, RateLimitExceeded  # noqa: E402

MINUTE = 60.0
DAY = 86400.0
WEEK = 604800.0

# An arbitrary non-zero epoch: nothing in the module aligns windows to wall-clock
# boundaries, so every window is measured from whatever "now" first touched it.
T0 = 1_000_000.0


def model(**kwargs) -> ModelConfig:
    """A model with small, easy-to-exhaust limits unless a test says otherwise."""
    defaults = {"name": "gemini-test", "rpm": 3, "rpd": 10, "rpw": 20, "tpm": 1000}
    return ModelConfig(**{**defaults, **kwargs})


def ladder_windows(m: ModelConfig) -> list[float]:
    """The windows of the rungs this model actually has, shortest first."""
    return [w for cap, w in ((m.rpm, MINUTE), (m.rpd, DAY), (m.rpw, WEEK)) if cap is not None]


def escalate(m: ModelConfig, steps: int, start: float = T0) -> float:
    """Apply `steps` 429s, rolling each penalty's own window on the way.

    A penalty holds until the window it was anchored to rolls, so the clock has to
    move past that window before the next 429 can advance the ladder. Returns the
    timestamp the last 429 was reported at.
    """
    windows = ladder_windows(m)
    t = start
    for step in range(steps):
        m.handle_429(t)
        if step + 1 < steps:
            t += windows[step % len(windows)]
    return t


class TestDefaults(unittest.TestCase):
    """The dataclass surface itself: what an operator gets when phrases.json omits a key."""

    def test_only_name_is_required(self):
        m = ModelConfig(name="gemini-2.5-flash")
        self.assertEqual(m.name, "gemini-2.5-flash")
        self.assertEqual(m.rpm, 15)
        self.assertEqual(m.rpd, 1500)
        self.assertIsNone(m.rpw)
        self.assertIsNone(m.tpm)
        self.assertEqual(m.max_context_tokens, 128000)
        self.assertIsNone(m.thinking_level)
        self.assertIsNone(m.thinking_budget)

    def test_counters_start_empty_and_windows_unopened(self):
        m = ModelConfig(name="m")
        self.assertEqual(m.to_dict()["minute_requests"], 0)
        self.assertIsNone(m.to_dict()["minute_window_start"])
        self.assertIsNone(m.to_dict()["day_window_start"])
        self.assertIsNone(m.to_dict()["week_window_start"])

    def test_repr_hides_internal_state(self):
        # Every counter is field(repr=False): the logs print configuration, not bookkeeping.
        m = model()
        m.record_request(T0)
        text = repr(m)
        self.assertIn("gemini-test", text)
        self.assertIn("rpm=3", text)
        for hidden in ("_minute_requests", "_day_requests", "_penalty_rung", "_minute_window_start"):
            self.assertNotIn(hidden, text)


class TestWindowResets(unittest.TestCase):
    """`_reset_windows_if_needed` runs before every read and write; these pin its edges."""

    def test_first_touch_opens_every_window_at_now(self):
        m = model()
        m.record_request(T0)
        state = m.to_dict()
        self.assertEqual(state["minute_window_start"], T0)
        self.assertEqual(state["day_window_start"], T0)
        self.assertEqual(state["week_window_start"], T0)

    def test_minute_window_holds_until_exactly_sixty_seconds(self):
        m = model()
        m.record_request(T0)
        m.record_request(T0 + 59.999)
        self.assertEqual(m.to_dict()["minute_requests"], 2)

        # 60.0 is the first instant that counts as expired (the check is `>=`).
        m.record_request(T0 + MINUTE)
        self.assertEqual(m.to_dict()["minute_requests"], 1)

    def test_day_window_holds_until_exactly_twenty_four_hours(self):
        m = model(rpm=10_000, rpd=10_000, rpw=10_000)
        m.record_request(T0)
        m.record_request(T0 + DAY - 0.001)
        self.assertEqual(m.to_dict()["day_requests"], 2)
        m.record_request(T0 + DAY)
        self.assertEqual(m.to_dict()["day_requests"], 1)

    def test_week_window_holds_until_exactly_seven_days(self):
        m = model(rpm=10_000, rpd=10_000, rpw=10_000)
        m.record_request(T0)
        m.record_request(T0 + WEEK - 0.001)
        self.assertEqual(m.to_dict()["week_requests"], 2)
        m.record_request(T0 + WEEK)
        self.assertEqual(m.to_dict()["week_requests"], 1)

    def test_expiring_minute_leaves_day_and_week_alone(self):
        m = model(rpm=10_000, rpd=10_000, rpw=10_000)
        for i in range(3):
            m.record_request(T0 + i * MINUTE)
        state = m.to_dict()
        self.assertEqual(state["minute_requests"], 1)
        self.assertEqual(state["day_requests"], 3)
        self.assertEqual(state["week_requests"], 3)

    def test_expiring_day_leaves_the_week_alone(self):
        m = model(rpm=10_000, rpd=10_000, rpw=10_000)
        m.record_request(T0)
        m.record_request(T0 + DAY)
        state = m.to_dict()
        self.assertEqual(state["day_requests"], 1)
        self.assertEqual(state["week_requests"], 2)

    def test_windows_restart_from_now_not_from_a_grid(self):
        # A window that expired long ago does not "catch up" in 60s steps: it is
        # simply reopened at the current instant.
        m = model()
        m.record_request(T0)
        m.record_request(T0 + 10 * MINUTE + 7.5)
        self.assertEqual(m.to_dict()["minute_window_start"], T0 + 10 * MINUTE + 7.5)

    def test_tokens_are_cleared_with_their_window(self):
        m = model(tpm=None)
        m.record_tokens(T0, 500)
        m.record_tokens(T0 + MINUTE, 1)
        state = m.to_dict()
        self.assertEqual(state["minute_tokens"], 1)
        self.assertEqual(state["day_tokens"], 501)
        self.assertEqual(state["week_tokens"], 501)

    def test_clock_jumping_backwards_reopens_every_window(self):
        # An NTP correction (or a container clock skew) must not freeze a model out
        # for the rest of the window: `now < window_start` counts as expired.
        m = model()
        m.record_request(T0)
        m.record_request(T0)
        m.record_request(T0)
        self.assertFalse(m.is_available(T0))

        self.assertTrue(m.is_available(T0 - 5))
        state = m.to_dict()
        self.assertEqual(state["minute_requests"], 0)
        self.assertEqual(state["day_requests"], 0)
        self.assertEqual(state["week_requests"], 0)
        self.assertEqual(state["minute_window_start"], T0 - 5)


class TestIsAvailable(unittest.TestCase):
    def test_a_fresh_model_is_available(self):
        self.assertTrue(model().is_available(T0))

    def test_the_minute_request_limit_blocks(self):
        m = model(rpm=2)
        m.record_request(T0)
        self.assertTrue(m.is_available(T0))
        m.record_request(T0)
        self.assertFalse(m.is_available(T0))

    def test_the_day_request_limit_blocks(self):
        m = model(rpm=1000, rpd=2)
        m.record_request(T0)
        m.record_request(T0)
        self.assertFalse(m.is_available(T0))

    def test_the_week_request_limit_blocks(self):
        m = model(rpm=1000, rpd=1000, rpw=2)
        m.record_request(T0)
        m.record_request(T0)
        self.assertFalse(m.is_available(T0))

    def test_no_minute_limit_means_the_minute_never_blocks(self):
        m = model(rpm=None, rpd=10**9, rpw=None)
        for _ in range(1000):
            m.record_request(T0)
        self.assertTrue(m.is_available(T0))

    def test_no_day_limit_means_the_day_never_blocks(self):
        m = model(rpm=10**9, rpd=None, rpw=None)
        for _ in range(1000):
            m.record_request(T0)
        self.assertTrue(m.is_available(T0))

    def test_a_model_with_no_request_limits_is_always_available(self):
        m = model(rpm=None, rpd=None, rpw=None, tpm=None)
        for _ in range(1000):
            m.record_request(T0)
        m.record_tokens(T0, 10**7)
        self.assertTrue(m.is_available(T0, anticipated_tokens=10**7))

    def test_no_weekly_limit_means_the_week_never_blocks(self):
        m = model(rpm=1000, rpd=1000, rpw=None)
        for _ in range(50):
            m.record_request(T0)
        self.assertTrue(m.is_available(T0))

    def test_anticipated_tokens_are_counted_against_tpm(self):
        m = model(tpm=1000)
        m.record_tokens(T0, 900)
        self.assertTrue(m.is_available(T0, anticipated_tokens=99))
        # Reaching the ceiling exactly already blocks (the check is `>=`).
        self.assertFalse(m.is_available(T0, anticipated_tokens=100))
        self.assertFalse(m.is_available(T0, anticipated_tokens=500))

    def test_anticipated_tokens_alone_can_block_an_idle_model(self):
        # A prompt bigger than the whole minute budget never gets sent to this model.
        self.assertFalse(model(tpm=1000).is_available(T0, anticipated_tokens=1000))

    def test_no_tpm_means_tokens_never_block(self):
        m = model(tpm=None)
        m.record_tokens(T0, 10_000_000)
        self.assertTrue(m.is_available(T0, anticipated_tokens=10_000_000))

    def test_only_the_minute_token_budget_gates_requests(self):
        # There is no daily/weekly token ceiling in the config; day_tokens is diagnostics only.
        m = model(tpm=1000)
        m.record_tokens(T0, 999)
        m.record_tokens(T0 + MINUTE, 999)  # minute rolls over, day/week keep ~2000
        self.assertGreater(m.to_dict()["day_tokens"], 1000)
        self.assertTrue(m.is_available(T0 + MINUTE))

    def test_a_blocked_model_frees_itself_when_the_window_rolls(self):
        m = model(rpm=2)
        m.record_request(T0)
        m.record_request(T0)
        self.assertFalse(m.is_available(T0 + 59))
        self.assertTrue(m.is_available(T0 + MINUTE))

    def test_a_day_limit_outlives_the_minute_window(self):
        m = model(rpm=2, rpd=2)
        m.record_request(T0)
        m.record_request(T0)
        self.assertFalse(m.is_available(T0 + MINUTE))
        self.assertTrue(m.is_available(T0 + DAY))


class TestRecording(unittest.TestCase):
    def test_record_request_increments_all_three_counters(self):
        m = model()
        m.record_request(T0)
        state = m.to_dict()
        self.assertEqual((state["minute_requests"], state["day_requests"], state["week_requests"]), (1, 1, 1))

    def test_record_request_resets_before_it_increments(self):
        # The reservation lands in the *new* window, not on top of the expired one.
        m = model()
        m.record_request(T0)
        m.record_request(T0 + MINUTE)
        self.assertEqual(m.to_dict()["minute_requests"], 1)

    def test_record_request_can_overshoot_its_own_limit(self):
        # Nothing here enforces the ceiling; `is_available` is the gate, and the
        # caller is expected to ask first.
        m = model(rpm=1)
        m.record_request(T0)
        m.record_request(T0)
        self.assertEqual(m.to_dict()["minute_requests"], 2)
        self.assertFalse(m.is_available(T0))

    def test_record_tokens_increments_all_three_counters(self):
        m = model()
        m.record_tokens(T0, 250)
        state = m.to_dict()
        self.assertEqual((state["minute_tokens"], state["day_tokens"], state["week_tokens"]), (250, 250, 250))

    def test_record_tokens_accumulates(self):
        m = model()
        m.record_tokens(T0, 100)
        m.record_tokens(T0 + 1, 50)
        self.assertEqual(m.to_dict()["minute_tokens"], 150)

    def test_record_tokens_does_not_touch_request_counters(self):
        m = model()
        m.record_tokens(T0, 100)
        self.assertEqual(m.to_dict()["minute_requests"], 0)

    def test_the_day_and_week_token_counters_are_kept_apart(self):
        # They only ever differ once the day has rolled under a still-open week,
        # which is the one arrangement that tells the two counters apart at all.
        m = model(tpm=None)
        m.record_tokens(T0, 100)
        m.record_tokens(T0 + DAY, 5)
        state = m.to_dict()
        self.assertEqual(state["minute_tokens"], 5)
        self.assertEqual(state["day_tokens"], 5)
        self.assertEqual(state["week_tokens"], 105)

    def test_record_zero_tokens_is_harmless(self):
        m = model()
        m.record_tokens(T0, 0)
        self.assertEqual(m.to_dict()["minute_tokens"], 0)


class TestRefund(unittest.TestCase):
    def test_refund_undoes_a_reservation(self):
        m = model()
        m.record_request(T0)
        m.refund_request(T0)
        state = m.to_dict()
        self.assertEqual((state["minute_requests"], state["day_requests"], state["week_requests"]), (0, 0, 0))

    def test_refund_never_goes_negative(self):
        m = model()
        m.refund_request(T0)
        m.refund_request(T0)
        state = m.to_dict()
        self.assertEqual((state["minute_requests"], state["day_requests"], state["week_requests"]), (0, 0, 0))

    def test_each_counter_clamps_on_its_own(self):
        # After a minute rollover the reservation being refunded belongs to an older
        # window: the day and week still owe it, the minute does not.
        m = model(rpm=1000, rpd=1000, rpw=1000)
        m.record_request(T0)
        m.record_request(T0 + MINUTE)  # minute := 1, day/week := 2
        m.refund_request(T0 + MINUTE)
        m.refund_request(T0 + MINUTE)
        state = m.to_dict()
        self.assertEqual(state["minute_requests"], 0)
        self.assertEqual(state["day_requests"], 0)
        self.assertEqual(state["week_requests"], 0)

    def test_a_refund_skips_a_window_that_rolled_since_the_request(self):
        # The call outlived its minute window. That window never counted the request,
        # so refunding there would hand back a slot it never took — while the day and
        # week windows, still open, do owe it.
        m = model(rpm=1000, rpd=1000, rpw=1000)
        m.record_request(T0)
        m.record_request(T0 + MINUTE)  # rolls the minute window, day and week stay
        m.refund_request(T0)
        state = m.to_dict()
        self.assertEqual(state["minute_requests"], 1)
        self.assertEqual(state["day_requests"], 1)
        self.assertEqual(state["week_requests"], 1)

    def test_a_refund_inside_its_own_windows_gives_everything_back(self):
        m = model(rpm=1000, rpd=1000, rpw=1000)
        m.record_request(T0)
        m.refund_request(T0)
        state = m.to_dict()
        self.assertEqual((state["minute_requests"], state["day_requests"], state["week_requests"]), (0, 0, 0))

    def test_a_refund_does_not_read_its_argument_as_the_current_time(self):
        # It is handed the moment the request was recorded, which is in the past.
        # Treating that as `now` would look like a backwards clock jump.
        m = model()
        m.record_request(T0)
        m.refund_request(T0 - WEEK)
        self.assertEqual(m.to_dict()["minute_window_start"], T0)

    def test_refund_does_not_roll_windows(self):
        # It takes no `now` at all, so a refund can never expire a window as a side effect.
        m = model()
        m.record_request(T0)
        before = m.to_dict()["minute_window_start"]
        m.refund_request(T0)
        self.assertEqual(m.to_dict()["minute_window_start"], before)

    def test_refund_leaves_tokens_and_penalties_alone(self):
        m = model()
        m.record_request(T0)
        m.record_tokens(T0, 300)
        m.handle_429(T0)
        m.refund_request(T0)
        state = m.to_dict()
        self.assertEqual(state["minute_tokens"], 300)
        self.assertEqual(state["penalty_limit"], "minute")


class TestHandle429(unittest.TestCase):
    """The penalty ladder.

    A 429 costs the model the shortest limit it has, then the next one up, and after
    the longest it starts over at the shortest. Only limits that exist are rungs. A
    penalty holds until the window it was anchored to rolls, so `handle_429` takes
    `now` like every other method that mutates state.
    """

    def test_the_first_429_burns_the_shortest_limit(self):
        m = model(rpm=3, rpd=10, rpw=20)
        m.handle_429(T0)
        state = m.to_dict()
        self.assertEqual(state["penalty_limit"], "minute")
        self.assertEqual(state["minute_requests"], 3)
        self.assertEqual(state["day_requests"], 0)
        self.assertFalse(m.is_available(T0))

    def test_the_second_429_burns_the_day(self):
        m = model(rpm=3, rpd=10, rpw=20)
        escalate(m, 2)
        state = m.to_dict()
        self.assertEqual(state["penalty_limit"], "day")
        self.assertEqual(state["day_requests"], 10)

    def test_the_third_429_burns_the_week(self):
        m = model(rpm=3, rpd=10, rpw=20)
        escalate(m, 3)
        state = m.to_dict()
        self.assertEqual(state["penalty_limit"], "week")
        self.assertEqual(state["week_requests"], 20)

    def test_the_fourth_429_starts_over_at_the_shortest_limit(self):
        # The ladder wraps to the first rung, not to the previous one.
        m = model(rpm=3, rpd=10, rpw=20)
        t = escalate(m, 4)
        state = m.to_dict()
        self.assertEqual(state["penalty_limit"], "minute")
        self.assertEqual(state["minute_requests"], 3)
        self.assertFalse(m.is_available(t))

    def test_a_missing_limit_is_not_a_rung(self):
        # No weekly quota: the ladder is minute -> day -> minute, never a dead end.
        m = model(rpm=3, rpd=10, rpw=None)
        t = escalate(m, 3)
        state = m.to_dict()
        self.assertEqual(state["penalty_limit"], "minute")
        self.assertEqual(state["minute_requests"], 3)
        self.assertEqual(state["week_requests"], 0)
        self.assertFalse(m.is_available(t))

    def test_the_ladder_can_start_above_the_minute(self):
        m = model(rpm=None, rpd=10, rpw=20)
        m.handle_429(T0)
        state = m.to_dict()
        self.assertEqual(state["penalty_limit"], "day")
        self.assertEqual(state["day_requests"], 10)
        self.assertEqual(state["minute_requests"], 0)

    def test_a_single_rung_repeats_itself(self):
        m = model(rpm=None, rpd=None, rpw=20)
        t = escalate(m, 3)
        state = m.to_dict()
        self.assertEqual(state["penalty_limit"], "week")
        self.assertEqual(state["week_requests"], 20)
        self.assertFalse(m.is_available(t))

    def test_a_model_with_no_limits_at_all_cannot_be_penalised(self):
        m = model(rpm=None, rpd=None, rpw=None, tpm=None)
        m.handle_429(T0)
        self.assertIsNone(m.to_dict()["penalty_limit"])
        self.assertTrue(m.is_available(T0))

    def test_a_burst_of_concurrent_429s_costs_one_rung(self):
        # Several requests in flight fail together. The client refunds each reservation
        # before reporting its 429, which is exactly what a counter-based guard misses.
        m = model(rpm=15, rpd=10)
        m.is_available(T0)
        for _ in range(3):
            m.record_request(T0)
        for _ in range(3):
            m.refund_request(T0)
            m.handle_429(T0)
        state = m.to_dict()
        self.assertEqual(state["penalty_limit"], "minute")
        self.assertEqual(state["day_requests"], 0)

    def test_a_burst_cannot_refund_the_penalty_away(self):
        # Each failing request refunds its reservation before reporting its 429. The
        # first one fills the limit to the brim; the refunds that follow must not
        # drain it back under the cap and hand the model straight out again.
        m = model(rpm=15, rpd=10)
        m.is_available(T0)
        for _ in range(3):
            m.record_request(T0)
        for _ in range(3):
            m.refund_request(T0)
            m.handle_429(T0)
        self.assertEqual(m.to_dict()["minute_requests"], 15)
        self.assertFalse(m.is_available(T0))

    def test_the_burst_re_fill_never_lowers_a_counter(self):
        # Reservations made before the penalty landed can push the counter past the
        # cap. Topping the penalty back up must not forget them.
        m = model(rpm=15)
        m.handle_429(T0)
        for _ in range(5):
            m.record_request(T0)
        m.handle_429(T0)
        self.assertEqual(m.to_dict()["minute_requests"], 20)

    def test_a_burst_cannot_climb_the_ladder(self):
        m = model(rpm=15, rpd=10, rpw=20)
        for _ in range(10):
            m.handle_429(T0)
        self.assertEqual(m.to_dict()["penalty_limit"], "minute")

    def test_only_a_rolled_window_lets_the_ladder_advance(self):
        m = model(rpm=3, rpd=10)
        m.handle_429(T0)
        m.handle_429(T0 + 59)  # still the same minute window: one event
        self.assertEqual(m.to_dict()["penalty_limit"], "minute")
        m.handle_429(T0 + MINUTE)  # the window rolled
        self.assertEqual(m.to_dict()["penalty_limit"], "day")

    def test_a_penalty_holds_for_its_own_window_not_the_shortest_one(self):
        # A request still in flight when the day rung was applied can fail minutes
        # later. The minute window has rolled by then; the day penalty has not, and
        # that late failure is still part of the event already paid for.
        m = model(rpm=3, rpd=10, rpw=20)
        escalate(m, 2)  # on the day rung, anchored to the day window
        m.handle_429(T0 + 5 * MINUTE)
        self.assertEqual(m.to_dict()["penalty_limit"], "day")

    def test_a_penalty_is_anchored_to_its_window_not_to_the_moment_it_lands(self):
        # The 429 arrives partway into the window. Everything until that window
        # rolls is still the same event, however late in the window it started.
        m = model(rpm=3, rpd=10)
        m.is_available(T0)  # opens the minute window at T0
        m.handle_429(T0 + 30)
        self.assertEqual(m.to_dict()["penalty_window_start"], T0)
        m.handle_429(T0 + 40)
        self.assertEqual(m.to_dict()["penalty_limit"], "minute")

    def test_the_penalty_never_lowers_a_counter(self):
        # Restored state can hold more requests than the current config allows.
        m = model(rpm=3)
        m.load_from_dict({"minute_requests": 99, "minute_window_start": T0})
        m.handle_429(T0)
        self.assertEqual(m.to_dict()["minute_requests"], 99)

    def test_handle_429_opens_the_window_it_anchors_to(self):
        # It takes `now`, so a penalty on a never-used model is not dropped by the
        # next timestamped call the way it used to be.
        m = model(rpm=3)
        m.handle_429(T0)
        self.assertEqual(m.to_dict()["minute_window_start"], T0)
        self.assertFalse(m.is_available(T0))

    def test_a_penalised_model_recovers_when_its_window_rolls(self):
        m = model(rpm=3)
        m.handle_429(T0)
        self.assertFalse(m.is_available(T0 + 59))
        self.assertTrue(m.is_available(T0 + MINUTE))

    def test_a_penalty_on_a_limit_that_is_gone_starts_over(self):
        # Restored state can name a limit the current config no longer has.
        m = model(rpm=3, rpd=10, rpw=None)
        m.load_from_dict({"penalty_limit": "week"})
        m.handle_429(T0)
        self.assertEqual(m.to_dict()["penalty_limit"], "minute")

    def test_a_penalty_keeps_its_place_when_the_ladder_composition_changes(self):
        # An operator adds a minute limit between restarts. The restored penalty
        # names the day, so the next 429 goes to the week — the rung it was on does
        # not silently become a different limit because the ladder grew beneath it.
        m = model(rpm=3, rpd=10, rpw=20)
        m.load_from_dict({"penalty_limit": "day", "penalty_window_start": T0 - DAY})
        m.handle_429(T0)
        state = m.to_dict()
        self.assertEqual(state["penalty_limit"], "week")
        self.assertEqual(state["week_requests"], 20)

    def test_a_model_the_provider_keeps_refusing_is_probed_rarely(self):
        """The regression test for the log this was written for.

        A model whose quota is gone answers 429 forever. Every request used to be
        spent on it, because the ladder ran out of rungs and stopped penalising.
        """
        m = model(rpm=15, rpd=1500, rpw=None)
        t, probes, requests = T0, 0, 0
        for _ in range(8640):  # a request every 30s for three days
            requests += 1
            if m.is_available(t):
                probes += 1
                m.record_request(t)
                m.refund_request(T0)
                m.handle_429(t)
            t += 30
        self.assertEqual(requests, 8640)
        self.assertGreater(probes, 0)  # it must keep probing, or it can never recover
        self.assertLess(probes, 10)  # but not spend every request on a dead model (it is 6)


class TestRecordSuccess(unittest.TestCase):
    def test_success_clears_the_penalty(self):
        m = model()
        m.handle_429(T0)
        m.record_success()
        state = m.to_dict()
        self.assertIsNone(state["penalty_limit"])
        self.assertIsNone(state["penalty_window_start"])

    def test_success_does_not_unblock_a_penalised_model(self):
        # Only the clock returns capacity; a success merely forgets the escalation.
        m = model(rpm=3)
        m.handle_429(T0)
        m.record_success()
        self.assertEqual(m.to_dict()["minute_requests"], 3)
        self.assertFalse(m.is_available(T0))

    def test_the_ladder_starts_over_after_a_success(self):
        m = model(rpm=3, rpd=10)
        escalate(m, 2)  # sitting on the day rung
        m.record_success()
        m.handle_429(T0 + 2 * DAY)
        state = m.to_dict()
        self.assertEqual(state["penalty_limit"], "minute")
        self.assertEqual(state["minute_requests"], 3)

    def test_success_on_a_clean_model_is_a_no_op(self):
        m = model()
        m.record_request(T0)
        m.record_success()
        self.assertEqual(m.to_dict()["minute_requests"], 1)


class TestPersistence(unittest.TestCase):
    """`llm_limits_state{role}.json` is written and re-read through these two methods."""

    EXPECTED_KEYS: ClassVar[set[str]] = {
        "minute_requests",
        "day_requests",
        "week_requests",
        "minute_tokens",
        "day_tokens",
        "week_tokens",
        "minute_window_start",
        "day_window_start",
        "week_window_start",
        "penalty_limit",
        "penalty_window_start",
    }

    def test_to_dict_has_exactly_the_persisted_keys(self):
        self.assertEqual(set(model().to_dict()), self.EXPECTED_KEYS)

    def test_to_dict_reports_live_state(self):
        m = model()
        m.record_request(T0)
        m.record_tokens(T0, 42)
        m.handle_429(T0)
        state = m.to_dict()
        self.assertEqual(state["week_tokens"], 42)
        self.assertEqual(state["penalty_limit"], "minute")
        self.assertEqual(state["day_window_start"], T0)

    def test_state_survives_a_round_trip_through_json(self):
        # Deliberately asymmetric: the day window sits under a still-open week, so
        # no day counter shares a value with its week twin. A state where the two
        # match round-trips cleanly even if the pair is swapped on the way through.
        m = model(rpm=10_000, rpd=10_000, rpw=10_000)
        m.record_request(T0)
        m.record_tokens(T0, 100)
        m.record_request(T0 + DAY)
        m.record_tokens(T0 + DAY, 5)
        m.handle_429(T0 + DAY)

        state = m.to_dict()
        self.assertNotEqual(state["day_requests"], state["week_requests"])
        self.assertNotEqual(state["day_tokens"], state["week_tokens"])
        self.assertNotEqual(state["day_window_start"], state["week_window_start"])

        restored = model(rpm=10_000, rpd=10_000, rpw=10_000)
        restored.load_from_dict(json.loads(json.dumps(state)))

        self.assertEqual(restored.to_dict(), state)
        self.assertEqual(restored.get_status(T0 + DAY), m.get_status(T0 + DAY))

    def test_the_anchor_survives_a_restart_so_a_burst_is_not_re_escalated(self):
        # The bot can be restarted mid-burst: the reloaded penalty must still know
        # which window it was applied in, or the rest of the burst climbs a rung.
        m = model(rpm=3, rpd=10)
        m.handle_429(T0)

        restored = model(rpm=3, rpd=10)
        restored.load_from_dict(json.loads(json.dumps(m.to_dict())))
        self.assertEqual(restored.to_dict()["penalty_window_start"], T0)

        restored.handle_429(T0 + 30)
        self.assertEqual(restored.to_dict()["penalty_limit"], "minute")

    def test_a_restored_model_keeps_its_remaining_window(self):
        m = model(rpm=2)
        m.record_request(T0)
        m.record_request(T0)

        restored = model(rpm=2)
        restored.load_from_dict(m.to_dict())
        self.assertFalse(restored.is_available(T0 + 30))
        self.assertTrue(restored.is_available(T0 + MINUTE))

    def test_missing_keys_fall_back_to_a_clean_slate(self):
        # A state file written by an older build simply loses the fields it lacks.
        m = model()
        m.record_request(T0)
        m.handle_429(T0)
        m.load_from_dict({})
        self.assertEqual(m.to_dict(), ModelConfig(name="x").to_dict())

    def test_a_partial_payload_only_overwrites_what_it_carries(self):
        m = model()
        m.load_from_dict({"day_requests": 7, "penalty_limit": "day"})
        state = m.to_dict()
        self.assertEqual(state["day_requests"], 7)
        self.assertEqual(state["penalty_limit"], "day")
        self.assertEqual(state["minute_requests"], 0)
        self.assertIsNone(state["day_window_start"])

    def test_a_state_file_from_before_the_ladder_still_loads(self):
        # `consecutive_429s` is what the penalty level used to be called; operators
        # have those files on disk and they must not reset on upgrade.
        m = model()
        m.load_from_dict({"consecutive_429s": 2})
        self.assertEqual(m.to_dict()["penalty_limit"], "day")

    def test_a_legacy_level_past_the_end_loads_as_the_longest_limit(self):
        # The old counter could climb past its own ladder. Anything above the top of
        # it meant the weekly rung, and must not read back as "no penalty at all".
        m = model()
        m.load_from_dict({"consecutive_429s": 5})
        self.assertEqual(m.to_dict()["penalty_limit"], "week")

    def test_unknown_keys_are_ignored(self):
        m = model()
        m.load_from_dict({"minute_requests": 1, "requests_per_fortnight": 99})
        self.assertEqual(m.to_dict()["minute_requests"], 1)

    def test_loading_does_not_carry_configuration(self):
        # Only counters are persisted: limits always come from phrases.json.
        m = model(rpm=3)
        m.load_from_dict(model(rpm=999).to_dict())
        self.assertEqual(m.rpm, 3)

    def test_a_state_file_older_than_its_windows_is_discarded_on_use(self):
        m = model(rpm=2)
        m.record_request(T0)
        m.record_request(T0)

        restored = model(rpm=2)
        restored.load_from_dict(m.to_dict())
        self.assertTrue(restored.is_available(T0 + WEEK + 1))
        state = restored.to_dict()
        self.assertEqual(state["minute_requests"], 0)
        self.assertEqual(state["day_requests"], 0)
        self.assertEqual(state["week_requests"], 0)


class TestGetStatus(unittest.TestCase):
    """What `/llm_limits` and the diagnostics log render."""

    def test_the_snapshot_shape(self):
        m = model(rpm=3, rpd=10, rpw=20, tpm=1000)
        m.record_request(T0)
        m.record_tokens(T0, 250)
        self.assertEqual(
            m.get_status(T0),
            {
                "model": "gemini-test",
                "minute_req": "1/3",
                "day_req": "1/10",
                "week_req": "1/20",
                "minute_tokens": "250/1000",
                "day_tokens": 250,
                "week_tokens": 250,
                "available": True,
            },
        )

    def test_absent_limits_render_as_infinity(self):
        status = ModelConfig(name="m", rpm=None, rpd=None, rpw=None, tpm=None).get_status(T0)
        self.assertEqual(status["minute_req"], "0/∞")
        self.assertEqual(status["day_req"], "0/∞")
        self.assertEqual(status["week_req"], "0/∞")
        self.assertEqual(status["minute_tokens"], "0/∞")

    def test_availability_tracks_the_counters(self):
        m = model(rpm=1)
        self.assertTrue(m.get_status(T0)["available"])
        m.record_request(T0)
        self.assertFalse(m.get_status(T0)["available"])

    def test_availability_ignores_anticipated_tokens(self):
        # The snapshot answers "is it usable at all", not "will this prompt fit".
        m = model(tpm=1000)
        m.record_tokens(T0, 999)
        self.assertTrue(m.get_status(T0)["available"])

    def test_reading_the_status_rolls_stale_windows(self):
        m = model(rpm=3)
        m.record_request(T0)
        m.record_tokens(T0, 250)
        status = m.get_status(T0 + MINUTE)
        self.assertEqual(status["minute_req"], "0/3")
        self.assertEqual(status["minute_tokens"], "0/1000")
        self.assertEqual(status["day_req"], "1/10")
        self.assertEqual(status["day_tokens"], 250)

    def test_the_snapshot_keeps_day_and_week_tokens_apart(self):
        m = model(tpm=None)
        m.record_tokens(T0, 100)
        m.record_tokens(T0 + DAY, 5)
        status = m.get_status(T0 + DAY)
        self.assertEqual(status["day_tokens"], 5)
        self.assertEqual(status["week_tokens"], 105)

    def test_a_penalised_model_reads_as_full(self):
        m = model(rpm=3)
        m.handle_429(T0)
        status = m.get_status(T0)
        self.assertEqual(status["minute_req"], "3/3")
        self.assertFalse(status["available"])


class TestRateLimitExceeded(unittest.TestCase):
    def test_it_is_a_plain_exception_carrying_its_message(self):
        self.assertTrue(issubclass(RateLimitExceeded, Exception))
        with self.assertRaises(RateLimitExceeded) as ctx:
            raise RateLimitExceeded("All configured models have exceeded their rate limits")
        self.assertEqual(str(ctx.exception), "All configured models have exceeded their rate limits")


class TestClientFlows(unittest.TestCase):
    """The call sequences `llm_client.get_interaction` actually performs."""

    def test_a_successful_call_reserves_then_bills_then_clears(self):
        m = model(rpm=3, tpm=1000)
        self.assertTrue(m.is_available(T0, anticipated_tokens=200))
        m.record_request(T0)
        m.record_tokens(T0, 220)
        m.record_success()
        state = m.to_dict()
        self.assertEqual(state["minute_requests"], 1)
        self.assertEqual(state["minute_tokens"], 220)
        self.assertIsNone(state["penalty_limit"])

    def test_a_failed_call_gives_the_reservation_back(self):
        m = model(rpm=3)
        m.record_request(T0)
        m.refund_request(T0)  # any exception path in the client
        self.assertEqual(m.to_dict()["minute_requests"], 0)
        self.assertTrue(m.is_available(T0))

    def test_a_429_refunds_and_then_blocks_the_model(self):
        m = model(rpm=3)
        m.record_request(T0)
        m.refund_request(T0)
        m.handle_429(T0)
        self.assertFalse(m.is_available(T0))

    def test_the_next_model_takes_over_when_the_first_is_out(self):
        best = model(name="best", rpm=1)
        cheap = model(name="cheap", rpm=1)
        chain = [best, cheap]

        chosen = next(mc for mc in chain if mc.is_available(T0))
        chosen.record_request(T0)
        chosen.handle_429(T0)

        remaining = [mc for mc in chain if mc.is_available(T0)]
        self.assertEqual([mc.name for mc in remaining], ["cheap"])

    def test_every_model_blocked_is_what_the_client_reports_as_exhausted(self):
        chain = [model(name="best", rpm=1), model(name="cheap", rpm=1)]
        for mc in chain:
            mc.handle_429(T0)
        self.assertFalse(any(mc.is_available(T0) for mc in chain))
        self.assertTrue(all(mc.is_available(T0 + MINUTE) for mc in chain))

    def test_exhaustion_is_per_model_not_shared(self):
        best, cheap = model(name="best", rpm=1), model(name="cheap", rpm=1)
        best.record_request(T0)
        self.assertFalse(best.is_available(T0))
        self.assertTrue(cheap.is_available(T0))


if __name__ == "__main__":
    unittest.main()
