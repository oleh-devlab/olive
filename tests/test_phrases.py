import sys
import unittest
from pathlib import Path

# Setup path so we can import from src
# TODO: fix paths
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from core.utils import format_phrase  # noqa: E402


class TestPhrase(unittest.TestCase):
    default = "Limit is {max_channels}."

    def test_uses_the_guild_phrase(self):
        section = {"limit_exceeded": "Ліміт: {max_channels}."}

        self.assertEqual(format_phrase(section, "limit_exceeded", self.default, max_channels=5), "Ліміт: 5.")

    def test_falls_back_when_the_key_is_missing(self):
        self.assertEqual(format_phrase({}, "limit_exceeded", self.default, max_channels=5), "Limit is 5.")

    def test_unknown_placeholder_falls_back(self):
        # phrases.json is hand-edited: a typo there must not raise KeyError in
        # the middle of answering a command.
        section = {"limit_exceeded": "Ліміт: {maks_channels}."}

        self.assertEqual(format_phrase(section, "limit_exceeded", self.default, max_channels=5), "Limit is 5.")

    def test_stray_brace_falls_back(self):
        section = {"limit_exceeded": "Ліміт: {max_channels."}

        self.assertEqual(format_phrase(section, "limit_exceeded", self.default, max_channels=5), "Limit is 5.")

    def test_extra_kwargs_are_allowed(self):
        section = {"greeting": "Привіт!"}

        self.assertEqual(format_phrase(section, "greeting", "Hello!", name="unused"), "Привіт!")


if __name__ == "__main__":
    unittest.main()
