import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Setup path so we can import from src
# TODO: fix paths
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

import core.cache  # noqa: E402
from core import llm_config  # noqa: E402


class LLMConfigTestCase(unittest.TestCase):
    """Writes a config (and its prompt files) into a temporary directory and loads it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(core.cache._llm_config.clear)

    def write_config(self, config: dict) -> Path:
        path = self.base / "llm_config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def write_prompt(self, name: str, text: str) -> str:
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return name

    def load(self, config: dict):
        self.assertTrue(llm_config.load_llm_config(self.write_config(config)))


class TestLoading(LLMConfigTestCase):
    def test_guild_section_is_merged_over_global(self):
        self.load(
            {
                "global": {"priorities": {"response_gate": ["cheap"]}, "instructions": {"system": "global"}},
                "42": {"instructions": {"system": "guild"}},
            }
        )

        self.assertEqual(llm_config.get_instruction("system", 42), "guild")
        # Not overridden by the guild, so it still answers from global.
        self.assertEqual(llm_config.get_priority("response_gate", 42), ["cheap"])

    def test_unknown_guild_falls_back_to_global(self):
        self.load({"global": {"instructions": {"system": "global"}}})

        self.assertEqual(llm_config.get_instruction("system", 999), "global")

    def test_missing_file_keeps_the_previous_config(self):
        self.load({"global": {"instructions": {"system": "loaded"}}})

        with self.assertLogs(llm_config.logger, "ERROR"):
            self.assertFalse(llm_config.load_llm_config(self.base / "nothing-here.json"))

        self.assertEqual(llm_config.get_instruction("system"), "loaded")

    def test_broken_json_keeps_the_previous_config(self):
        self.load({"global": {"instructions": {"system": "loaded"}}})

        broken = self.base / "broken.json"
        broken.write_text("{not json", encoding="utf-8")

        with self.assertLogs(llm_config.logger, "ERROR"):
            self.assertFalse(llm_config.load_llm_config(broken))

        self.assertEqual(llm_config.get_instruction("system"), "loaded")

    def test_a_json_list_is_refused(self):
        path = self.base / "list.json"
        path.write_text("[]", encoding="utf-8")

        with self.assertLogs(llm_config.logger, "ERROR"):
            self.assertFalse(llm_config.load_llm_config(path))


class TestModels(LLMConfigTestCase):
    def test_models_are_returned_in_order(self):
        self.load({"global": {"models": [{"name": "best"}, {"name": "cheap"}]}})

        self.assertEqual([m["name"] for m in llm_config.get_models()], ["best", "cheap"])

    def test_entries_without_a_name_are_dropped(self):
        self.load({"global": {"models": [{"rpm": 5}, "gemma", {"name": "kept"}]}})

        self.assertEqual([m["name"] for m in llm_config.get_models()], ["kept"])

    def test_missing_models_is_an_empty_list(self):
        self.load({"global": {}})

        self.assertEqual(llm_config.get_models(), [])


class TestInstructions(LLMConfigTestCase):
    def test_instruction_can_be_a_file(self):
        name = self.write_prompt("prompts/system.md", "From a file.\n")
        self.load({"global": {"instructions": {"system": {"file": name}}}})

        self.assertEqual(llm_config.get_instruction("system"), "From a file.")

    def test_an_edited_prompt_file_is_re_read(self):
        # An operator editing a prompt should not have to reload the config for
        # it to take effect, so the cache is keyed by mtime rather than by load.
        name = self.write_prompt("prompts/system.md", "first")
        self.load({"global": {"instructions": {"system": {"file": name}}}})
        self.assertEqual(llm_config.get_instruction("system"), "first")

        path = self.base / name
        path.write_text("second", encoding="utf-8")
        os.utime(path, (0, 0))

        self.assertEqual(llm_config.get_instruction("system"), "second")

    def test_missing_prompt_file_falls_back(self):
        self.load({"global": {"instructions": {"system": {"file": "prompts/gone.md"}}}})

        with self.assertLogs(llm_config.logger, "ERROR"):
            self.assertEqual(llm_config.get_instruction("system", default="built-in"), "built-in")

    def test_default_file_is_read_when_the_config_names_none(self):
        self.write_prompt("prompts/schedule_agent.md", "Agent prompt.")
        self.load({"global": {}})

        self.assertEqual(
            llm_config.get_instruction("schedule_agent", default_file="prompts/schedule_agent.md"), "Agent prompt."
        )

    def test_a_named_instruction_wins_over_the_default_file(self):
        self.write_prompt("prompts/schedule_agent.md", "Shipped.")
        self.load({"global": {"instructions": {"schedule_agent": "Overridden."}}})

        self.assertEqual(
            llm_config.get_instruction("schedule_agent", default_file="prompts/schedule_agent.md"), "Overridden."
        )

    def test_missing_instruction_is_the_default(self):
        self.load({"global": {}})

        self.assertEqual(llm_config.get_instruction("system_addition"), "")
        self.assertEqual(llm_config.get_instruction("system", default="built-in"), "built-in")


class TestPriorities(LLMConfigTestCase):
    def test_priority_names_are_strings(self):
        self.load({"global": {"priorities": {"schedule_agent": ["best", 5]}}})

        self.assertEqual(llm_config.get_priority("schedule_agent"), ["best", "5"])

    def test_unknown_priority_is_empty(self):
        self.load({"global": {"priorities": {}}})

        self.assertEqual(llm_config.get_priority("response_gate"), [])


if __name__ == "__main__":
    unittest.main()
