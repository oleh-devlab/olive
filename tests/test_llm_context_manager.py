"""Tests for how much of a conversation stays on disk.

`llm_token_budget` opens the bot's database as soon as it is imported, so this
suite stands a stub in for it — nothing here touches `olive.sqlite3`.
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

# `core.database` connects and migrates at import time; the context manager only
# reaches it through the budget repository, which these tests do not exercise.
_stub = types.ModuleType("core.database")
_stub.db = None
sys.modules.setdefault("core.database", _stub)

from modules.llm_context_manager import LLMContextManager  # noqa: E402


class FakeBudget:
    context_tokens = 64000


def message(tokens: int, role: str = "user", is_result: bool = False) -> dict:
    entry = {"role": role, "parts": [{"text": "x" * tokens}], "tokens": tokens}
    if is_result:
        entry["interaction_step"] = {"type": "function_result"}

    return entry


class ContextTestCase(unittest.IsolatedAsyncioTestCase):
    database_token_limit = None

    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "context.json"

        self.manager = LLMContextManager(
            token_budget=FakeBudget(),
            context_file_name=str(self.path),
            budget_name="test",
            database_token_limit=self.database_token_limit,
        )

    def stored(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


class TestStoredLimit(ContextTestCase):
    database_token_limit = 100

    async def test_a_conversation_is_trimmed_to_the_limit(self):
        self.manager.database_context = {"1": [message(60) for _ in range(10)]}

        await self.manager.write_to_file()

        kept = self.stored()["1"]
        self.assertLessEqual(sum(m["tokens"] for m in kept), self.database_token_limit)
        self.assertTrue(kept)

    async def test_what_fits_is_left_alone(self):
        self.manager.database_context = {"1": [message(10)]}

        await self.manager.write_to_file()

        self.assertEqual(len(self.stored()["1"]), 1)

    async def test_every_conversation_is_measured_on_its_own(self):
        # The limit is per conversation, not per file: two of them make a file
        # of twice the size, which is what the settings comment warns about.
        self.manager.database_context = {"1": [message(60) for _ in range(5)], "2": [message(60) for _ in range(5)]}

        await self.manager.write_to_file()

        for messages in self.stored().values():
            self.assertLessEqual(sum(m["tokens"] for m in messages), self.database_token_limit)

    async def test_trimming_leaves_a_conversation_starting_on_a_user_message(self):
        self.manager.database_context = {
            "1": [message(60), message(60, role="model"), message(60, is_result=True), message(20)]
        }

        await self.manager.write_to_file()

        self.assertEqual(self.stored()["1"][0]["role"], "user")

    async def test_a_conversation_with_no_valid_start_left_is_dropped_entirely(self):
        # Nothing after the cut can open a context, so nothing can be kept.
        self.manager.database_context = {"1": [message(60), message(60, role="model"), message(60, role="model")]}

        await self.manager.write_to_file()

        self.assertEqual(self.stored()["1"], [])

    async def test_the_working_cache_keeps_its_own_budget(self):
        # The cache is trimmed by the prompt budget, not by the stored limit.
        self.manager.llm_context = {"1": [message(60) for _ in range(10)]}
        self.manager.database_context = {"1": [message(60) for _ in range(10)]}

        await self.manager.write_to_file()

        self.assertEqual(len(self.manager.llm_context["1"]), 10)


class TestWithoutALimit(ContextTestCase):
    async def test_nothing_is_dropped(self):
        self.manager.database_context = {"1": [message(60) for _ in range(10)]}

        await self.manager.write_to_file()

        self.assertEqual(len(self.stored()["1"]), 10)


if __name__ == "__main__":
    unittest.main()
