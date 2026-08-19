import json
import sys
import tempfile
import unittest
from pathlib import Path

# Setup path so we can import from src
# TODO: fix paths
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from core.personal_channels import ChannelSetupError, PersonalChannelRegistry  # noqa: E402


class TestPersonalChannelRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "nested" / "channels.json"
        self.registry = PersonalChannelRegistry(self.path, "report_channel_id", "management_channel_id")

    def test_missing_file_is_empty(self):
        self.assertEqual(self.registry.load(), {})
        self.assertEqual(self.registry.count(), 0)
        self.assertIsNone(self.registry.get(1))

    def test_unreadable_file_is_empty(self):
        # A corrupt registry must not take the whole cog down on startup.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{{{ not json", encoding="utf-8")

        self.assertEqual(self.registry.load(), {})

    def test_register_and_get(self):
        self.registry.register(42, 111, 5001, 5002)

        entry = self.registry.get(42)

        self.assertEqual(entry["report_channel_id"], 5001)
        self.assertEqual(entry["management_channel_id"], 5002)
        self.assertEqual(entry["guild_id"], 111)

    def test_register_creates_missing_directories(self):
        self.registry.register(42, 111, 5001, 5002)

        self.assertTrue(self.path.exists())

    def test_configurable_key_names(self):
        # The schedule file uses different names for the same two channels.
        registry = PersonalChannelRegistry(self.path, "channel_id", "tasks_channel_id")
        registry.register(42, 111, 5001, 5002)

        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["42"]["channel_id"], 5001)
        self.assertEqual(registry.display_channel_id(registry.get(42)), 5001)

    def test_extra_fields_survive_a_re_register(self):
        # The schedule keeps per-user solver settings in the same record.
        self.registry.register(42, 111, 5001, 5002, planning_days=30)
        self.registry.register(42, 111, 6001, 6002)

        entry = self.registry.get(42)

        self.assertEqual(entry["planning_days"], 30)
        self.assertEqual(entry["report_channel_id"], 6001)

    def test_save_leaves_no_temporary_file_behind(self):
        self.registry.register(42, 111, 5001, 5002)

        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])

    def test_remove(self):
        self.registry.register(42, 111, 5001, 5002)

        self.assertEqual(self.registry.remove(42)["report_channel_id"], 5001)
        self.assertIsNone(self.registry.get(42))
        self.assertIsNone(self.registry.remove(42))

    def test_count_in_guild(self):
        self.registry.register(1, 111, 1, 2)
        self.registry.register(2, 111, 3, 4)
        self.registry.register(3, 222, 5, 6)

        self.assertEqual(self.registry.count_in_guild(111), 2)
        self.assertEqual(self.registry.count_in_guild(222), 1)
        self.assertEqual(self.registry.count_in_guild(333), 0)

    def test_iter_display_channels_skips_entries_without_one(self):
        self.registry.register(1, 111, 5001, 5002)
        data = self.registry.load()
        data["2"] = {"guild_id": 111, "management_channel_id": 7002}
        self.registry.save(data)

        self.assertEqual(list(self.registry.iter_display_channels()), [(1, 5001)])

    def test_iter_management_channels_skips_entries_without_one(self):
        self.registry.register(1, 111, 5001, 5002)
        data = self.registry.load()
        data["2"] = {"guild_id": 111, "report_channel_id": 6001}
        self.registry.save(data)

        self.assertEqual(list(self.registry.iter_management_channels()), [(1, 5002)])

    def test_find_user_by_management_channel(self):
        self.registry.register(42, 111, 5001, 5002)

        self.assertEqual(self.registry.find_user_by_management_channel(5002), 42)
        self.assertIsNone(self.registry.find_user_by_management_channel(9999))


class TestChannelSetupError(unittest.TestCase):
    def test_renders_from_phrases(self):
        error = ChannelSetupError("limit_exceeded", "Limit is {max_channels}.", max_channels=5)

        self.assertEqual(error.text({"limit_exceeded": "Ліміт: {max_channels}."}), "Ліміт: 5.")

    def test_falls_back_when_the_phrase_is_missing(self):
        error = ChannelSetupError("limit_exceeded", "Limit is {max_channels}.", max_channels=5)

        self.assertEqual(error.text({}), "Limit is 5.")

    def test_a_broken_phrase_falls_back_instead_of_raising(self):
        # phrases.json is hand-edited; a typo there must not turn a friendly
        # error into a traceback.
        error = ChannelSetupError("limit_exceeded", "Limit is {max_channels}.", max_channels=5)

        self.assertEqual(error.text({"limit_exceeded": "Ліміт: {maks_channels}."}), "Limit is 5.")


if __name__ == "__main__":
    unittest.main()
