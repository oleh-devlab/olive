"""Tests for `modules.llm_client`: how a ModelConfig is built and how it is driven.

`llm_client` imports the Google GenAI SDK at module level. Where the SDK is installed
it is imported normally; where it is not, a stub stands in so this suite still runs on
a bare checkout. Either way nothing here reaches the network — the tests call a static
method or drive `get_interaction` against a fake API object.
"""

import logging
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))


def _stub_google_genai() -> None:
    """Stand in for the SDK so the module under test can be imported without it."""
    google = sys.modules.setdefault("google", types.ModuleType("google"))
    genai = types.ModuleType("google.genai")
    genai_types = types.ModuleType("google.genai.types")
    genai_types.HttpOptions = genai_types.HttpRetryOptions = lambda **kwargs: None
    genai.Client = object
    genai.types = genai_types
    google.genai = genai
    sys.modules["google.genai"] = genai
    sys.modules["google.genai.types"] = genai_types


try:  # pragma: no cover - which branch runs depends on the environment, not the test
    import google.genai  # noqa: F401
except BaseException:
    # Not just ImportError: a native dependency of the SDK can abort the import with
    # a pyo3 PanicException, which does not inherit from Exception. Any failure to
    # bring in an SDK these tests never call is a reason to stub it, not to fail.
    for name in [n for n in sys.modules if n == "google" or n.startswith("google.")]:
        del sys.modules[name]
    _stub_google_genai()

import core.cache  # noqa: E402
import modules.llm_client as llm_client  # noqa: E402
from modules.llm_client import LLMClient  # noqa: E402
from modules.llm_rate_limiter import ModelConfig  # noqa: E402

T0 = 1_000_000.0

# The module under test logs every refusal; the tests provoke those on purpose.
logging.getLogger("modules.llm_client").propagate = False


class FakeAPIError(Exception):
    """What the SDK raises: the code is an attribute, not just text."""

    def __init__(self, code: int, message: str = "You exceeded your current quota"):
        super().__init__(f"Error code: {code} - {message}")
        self.code = code
        self.message = message


class FakeClock:
    """A clock the test moves by hand, standing in for the `time` module."""

    def __init__(self, start: float = T0):
        self.t = start

    def time(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeInteractions:
    """`client.aio.interactions`: each call consumes the next scripted outcome."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.models_called: list[str] = []

    async def create(self, **kwargs):
        self.models_called.append(kwargs["model"])
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            outcome = outcome()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def build_client(models: list[ModelConfig], outcomes) -> LLMClient:
    """An LLMClient around a fake API. `__init__` would construct a real SDK client."""
    client = LLMClient.__new__(LLMClient)
    client.models = models
    client.state_file = Path("unused-in-tests.json")
    interactions = FakeInteractions(outcomes)
    client.client = types.SimpleNamespace(aio=types.SimpleNamespace(interactions=interactions))
    client.interactions = interactions
    return client


def watch_availability(m: ModelConfig, log: list) -> ModelConfig:
    """Record the timestamp `m` is asked about, then answer as usual."""
    original = m.is_available

    def wrapper(now, anticipated_tokens=0):
        log.append((m.name, now))
        return original(now, anticipated_tokens=anticipated_tokens)

    m.is_available = wrapper
    return m


class TestLoadModelsConfig(unittest.TestCase):
    """`phrases.json` → `olive` → `models` becomes the list of ModelConfig."""

    def setUp(self):
        saved = core.cache._phrases
        self.addCleanup(setattr, core.cache, "_phrases", saved)

    def use_config(self, olive: dict) -> None:
        core.cache._phrases = {"global": {"olive": olive}}

    def test_no_configurable_field_is_dropped(self):
        """The regression test for the outage: `rpw` was never passed to ModelConfig.

        The weekly rung was unreachable for every model, whatever phrases.json said,
        so this checks the whole shape rather than that one field — the next limit
        added must not be forgotten the same way.
        """
        configured = {
            "name": "gemini-test",
            "rpm": 1,
            "rpd": 2,
            "rpw": 3,
            "tpm": 4,
            "max_context_tokens": 5,
            "thinking_level": "low",
            "thinking_budget": 6,
        }
        self.use_config({"models": [configured]})
        (m,) = LLMClient._load_models_config()
        for key, value in configured.items():
            self.assertEqual(getattr(m, key), value, f"'{key}' never reached the model")

    def test_a_configured_weekly_limit_reaches_the_ladder(self):
        self.use_config({"models": [{"name": "m", "rpm": 2, "rpd": 4, "rpw": 6}]})
        (m,) = LLMClient._load_models_config()
        for _ in range(6):
            m.record_request(T0)
        self.assertFalse(m.is_available(T0))

    def test_omitted_limits_fall_back_to_the_defaults(self):
        self.use_config({"models": [{"name": "m"}]})
        (m,) = LLMClient._load_models_config()
        self.assertEqual((m.rpm, m.rpd), (15, 1500))
        self.assertIsNone(m.rpw)
        self.assertIsNone(m.tpm)

    def test_an_explicit_null_switches_a_limit_off(self):
        self.use_config({"models": [{"name": "m", "rpm": None, "rpd": None}]})
        (m,) = LLMClient._load_models_config()
        self.assertIsNone(m.rpm)
        self.assertIsNone(m.rpd)
        for _ in range(10_000):
            m.record_request(T0)
        self.assertTrue(m.is_available(T0))

    def test_models_keep_their_configured_order(self):
        # The order is the fallback order, best first, so it is part of the contract.
        self.use_config({"models": [{"name": "best"}, {"name": "cheap"}]})
        self.assertEqual([m.name for m in LLMClient._load_models_config()], ["best", "cheap"])

    def test_entries_without_a_name_are_skipped(self):
        self.use_config({"models": [{"rpm": 5}, {"name": "m"}, "not-a-dict"]})
        self.assertEqual([m.name for m in LLMClient._load_models_config()], ["m"])

    def test_the_legacy_single_model_key_still_works(self):
        self.use_config({"model_name": "gemma-legacy"})
        (m,) = LLMClient._load_models_config()
        self.assertEqual(m.name, "gemma-legacy")

    def test_an_empty_model_list_falls_back_to_the_legacy_key(self):
        self.use_config({"models": [], "model_name": "gemma-legacy"})
        (m,) = LLMClient._load_models_config()
        self.assertEqual(m.name, "gemma-legacy")

    def test_no_configuration_at_all_still_yields_a_model(self):
        self.use_config({})
        self.assertEqual(len(LLMClient._load_models_config()), 1)


class TestClockDiscipline(unittest.IsolatedAsyncioTestCase):
    """`get_interaction` must read the clock per attempt, not once per call."""

    async def test_each_attempt_is_judged_on_a_fresh_clock(self):
        # The first model's request outlasts a rate-limit window. A timestamp cached
        # before it would reach the second model already stale, and a stale `now` is
        # what the limiter reads as a backwards clock jump.
        clock = FakeClock(T0)
        seen: list = []
        best = watch_availability(ModelConfig(name="gemini-best", rpm=15), seen)
        cheap = watch_availability(ModelConfig(name="gemini-cheap", rpm=15), seen)

        def slow_refusal():
            clock.advance(70)
            return FakeAPIError(429)

        client = build_client([best, cheap], [slow_refusal, types.SimpleNamespace(usage=None)])
        with mock.patch.object(llm_client, "time", clock):
            await client.get_interaction([{"type": "text", "text": "hi"}], anticipated_tokens=10)

        self.assertEqual([name for name, _ in seen], ["gemini-best", "gemini-cheap"])
        self.assertEqual(dict(seen)["gemini-best"], T0)
        self.assertEqual(dict(seen)["gemini-cheap"], T0 + 70, "the second model was judged on a stale clock")

    async def test_a_penalty_is_not_wiped_by_the_call_that_earned_it(self):
        # The penalty lands on a window opened 70s after the call started. Nothing in
        # the rest of that call may hand the model back out.
        clock = FakeClock(T0)
        best = ModelConfig(name="gemini-best", rpm=15)
        cheap = ModelConfig(name="gemini-cheap", rpm=15)

        def slow_refusal():
            clock.advance(70)
            return FakeAPIError(429)

        client = build_client([best, cheap], [slow_refusal, types.SimpleNamespace(usage=None)])
        with mock.patch.object(llm_client, "time", clock):
            await client.get_interaction([{"type": "text", "text": "hi"}], anticipated_tokens=10)

        self.assertEqual(best.to_dict()["penalty_limit"], "minute")
        self.assertFalse(best.is_available(clock.time()))
        self.assertIsNone(cheap.to_dict()["penalty_limit"])

    async def test_the_refused_model_is_skipped_on_the_next_call(self):
        clock = FakeClock(T0)
        best = ModelConfig(name="gemini-best", rpm=15)
        cheap = ModelConfig(name="gemini-cheap", rpm=15)
        client = build_client(
            [best, cheap],
            [FakeAPIError(429), types.SimpleNamespace(usage=None), types.SimpleNamespace(usage=None)],
        )
        with mock.patch.object(llm_client, "time", clock):
            await client.get_interaction([{"type": "text", "text": "hi"}], anticipated_tokens=10)
            clock.advance(5)
            await client.get_interaction([{"type": "text", "text": "hi"}], anticipated_tokens=10)

        self.assertEqual(client.interactions.models_called, ["gemini-best", "gemini-cheap", "gemini-cheap"])


if __name__ == "__main__":
    unittest.main()
