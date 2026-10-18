import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from tuning_push_sync_policy import should_sync_immediately


class TuningPushSyncPolicyTest(unittest.TestCase):
    def test_tuning_only_commit_syncs_immediately(self):
        self.assertTrue(should_sync_immediately(
            ["server-config.json", "scripts/sync-server-config.py"]))

    def test_commit_also_touching_mods_defers(self):
        self.assertFalse(should_sync_immediately(
            ["server-config.json", "mods/dynamic-atmosphere.pw.toml", "index.toml"]))

    def test_commit_touching_pack_toml_defers(self):
        self.assertFalse(should_sync_immediately(["server-config.json", "pack.toml"]))

    def test_commit_touching_unrelated_path_still_syncs_immediately(self):
        self.assertTrue(should_sync_immediately(
            ["server-config.json", "README.md", "docs/release-policy.md"]))

    def test_empty_changed_paths_syncs_immediately(self):
        self.assertTrue(should_sync_immediately([]))


if __name__ == "__main__":
    unittest.main()
