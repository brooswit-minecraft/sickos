import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from server_update_policy import should_deploy
from server_tuning_deploy_evidence import deployed


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

    def test_stranded_trailer_from_squash_composition_fails_closed(self):
        # MINECRAFT-258 incident, verbatim (see tests/test_release_decisions.py
        # for the full fixture/explanation): a squash merge split the branch
        # head's single terminal trailer paragraph into two, stranding
        # Release-Only above a fresh Co-authored-by-only final paragraph.
        # should_deploy's own "no trailer -> deploy" default then fired for
        # real, re-pointing and restarting a live hosted server that was
        # never authorized to restart. Must raise, not silently deploy.
        message = (
            "MINECRAFT-258: bump Dynamic Battle Axes to 0.2.1 (#109)\n"
            "\n"
            "Pin the arc-sweep-race fix (dynamic-battle-axes v0.2.1) instead of the\n"
            "0.2.0 jar, which still has the client/integrated-server thread race an\n"
            "independent post-merge review found. Patch bump per CONTRIBUTING.md\n"
            "(compatible mod bugfix).\n"
            "\n"
            "\n"
            "Release-Only: true\n"
            "\n"
            "Co-authored-by: brooswit <10604446+brooswit@users.noreply.github.com>\n"
            "Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>\n"
        )
        with self.assertRaises(ValueError):
            should_deploy(message)


def _jobs(*name_conclusion_pairs):
    return json.dumps({"jobs": [{"name": n, "conclusion": c} for n, c in name_conclusion_pairs]})


class ServerTuningDeployEvidenceTest(unittest.TestCase):
    """Eight real/synthetic job-list fixtures for server-tuning.yml's
    release-only-guard, captured from the provider during MINECRAFT-258
    review rather than invented, so a regression in the classifier (not
    just in the YAML wiring around it) is actually caught."""

    def test_release_only_skip_is_not_deployment(self):
        # sickos run 39049928024: deployment-policy decided deploy=false,
        # the reusable-workflow call itself never ran.
        self.assertFalse(deployed(_jobs(
            ("deployment-policy", "success"),
            ("server-update", "skipped"),
        )))

    def test_real_sftp_deploy_is_deployment(self):
        # sickos run 38766585539: a genuine successful chained deploy.
        self.assertTrue(deployed(_jobs(
            ("deployment-policy", "success"),
            ("server-update / deploy-method", "success"),
            ("server-update / Upload and restart server", "success"),
            ("server-update / Update server", "skipped"),
        )))

    def test_manual_dispatch_deploy_is_deployment(self):
        # sickos run 38505769236: same shape via workflow_dispatch.
        self.assertTrue(deployed(_jobs(
            ("deployment-policy", "success"),
            ("server-update / deploy-method", "success"),
            ("server-update / Upload and restart server", "success"),
            ("server-update / Update server", "skipped"),
        )))

    def test_failed_restart_is_not_deployment(self):
        # sickos run 38402755903.
        self.assertFalse(deployed(_jobs(
            ("server-update / deploy-method", "success"),
            ("server-update / Upload and restart server", "failure"),
            ("server-update / Update server", "skipped"),
        )))

    def test_cancelled_restart_is_not_deployment(self):
        # sickos run 38320053860.
        self.assertFalse(deployed(_jobs(
            ("server-update / deploy-method", "success"),
            ("server-update / Upload and restart server", "cancelled"),
            ("server-update / Update server", "skipped"),
        )))

    def test_no_server_update_jobs_is_not_deployment(self):
        # sickos runs 38442809887 / 38437170681: an unrelated job shape
        # (this file previously carried verify/diagnose/start/recover
        # jobs) must not be silently treated as a deploy.
        self.assertFalse(deployed(_jobs(("verify", "success"))))
        self.assertFalse(deployed(_jobs(("diagnose", "success"))))

    def test_deploy_method_success_alone_is_not_deployment(self):
        # The exact gap John Winstead's review named: deploy-method is
        # pure validation and never touches the server; a successful but
        # no-op "Update server" (Modrinth-API path not configured) must
        # not count either -- only the SFTP path's actual upload+restart
        # job is trusted as positive evidence.
        self.assertFalse(deployed(_jobs(
            ("deployment-policy", "success"),
            ("server-update / deploy-method", "success"),
            ("server-update / Update server", "success"),
            ("server-update / Upload and restart server", "skipped"),
        )))

    def test_unreadable_json_is_not_deployment(self):
        self.assertFalse(deployed(""))
        self.assertFalse(deployed("{not json"))
        self.assertFalse(deployed("{}"))
        self.assertFalse(deployed('{"jobs": "not-a-list"}'))


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

    def test_guard_reads_job_evidence_from_the_triggering_run_by_id(self):
        # Not a commit/trailer re-derivation: github.event.workflow_run.head_sha
        # is not guaranteed to be the commit that actually triggered the
        # chain (MINECRAFT-258 review, second pass). The guard must key off
        # the triggering run's own immutable id and its own job list, and
        # the actual classification must live in the tested script, not
        # be re-implemented inline in the YAML.
        guard_job = self.workflow.split("\n  release-only-guard:\n", 1)[1].split(
            "\n  sync:\n", 1
        )[0]
        self.assertIn("github.event.workflow_run.id", guard_job)
        self.assertIn("/jobs", guard_job)
        self.assertIn("server_tuning_deploy_evidence.py", guard_job)
        self.assertNotIn("head_sha", guard_job)
        self.assertNotIn("from server_update_policy import", guard_job)
        self.assertNotIn("import server_update_policy", guard_job)

    def test_permissions_include_actions_read(self):
        self.assertIn("actions: read", self.workflow.split("on:", 1)[1].split("jobs:", 1)[0])
