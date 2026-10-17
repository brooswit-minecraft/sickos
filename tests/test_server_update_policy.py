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


class ServerTuningGateContractTest(unittest.TestCase):
    """Static contract on server-tuning.yml's live-SFTP gate: a guard-job
    failure, an infra blip, or an operator cancel must fail CLOSED (no
    live write), not open. Reading the YAML text is what actually catches
    a `!= 'false'` / `always()` regression -- no live workflow_run event
    or Actions-API mock required (MINECRAFT-258 review)."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/server-tuning.yml"
        ).read_text()
        cls.sync_job = cls.workflow.split("\n  sync:\n", 1)[1]

    def test_sync_gate_requires_deploy_true_not_merely_not_false(self):
        self.assertIn("needs.release-only-guard.outputs.deploy == 'true'", self.sync_job)
        self.assertNotIn("outputs.deploy != 'false'", self.sync_job)

    def test_sync_gate_does_not_use_always(self):
        # always() keeps the live-write job eligible even after a
        # cancelled run; !cancelled() preserves the push/workflow_dispatch
        # short-circuit while still failing closed on a cancel.
        gate = self.sync_job.split("if: >-", 1)[1].split("runs-on:", 1)[0]
        self.assertIn("!cancelled()", gate)
        self.assertNotIn("always()", gate)

    def test_guard_reads_release_message_from_a_separate_checkout(self):
        guard_job = self.workflow.split("\n  release-only-guard:\n", 1)[1].split(
            "\n  sync:\n", 1
        )[0]
        self.assertIn("path: release-source", guard_job)
        self.assertIn("git -C release-source show", guard_job)
