import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from server_update_policy import should_deploy


class ServerUpdatePolicyTest(unittest.TestCase):
    def test_default_preserves_automatic_deployment(self):
        self.assertTrue(should_deploy("Normal pack update\n"))

    def test_release_only_skips_hosting(self):
        self.assertFalse(should_deploy("Client release\n\nRelease-Only: true\n"))

    def test_false_preserves_deployment(self):
        self.assertTrue(should_deploy("Normal release\n\nRelease-Only: false\n"))

    def test_body_mention_is_not_a_trailer(self):
        self.assertTrue(should_deploy("Explain Release-Only: true in prose.\n\nMore explanation.\n"))

    def test_invalid_value_fails_closed(self):
        with self.assertRaises(ValueError):
            should_deploy("Release\n\nRelease-Only: maybe\n")

    def test_trailer_case_is_insensitive(self):
        self.assertFalse(should_deploy("Release\n\nrelease-only: TRUE\n"))
