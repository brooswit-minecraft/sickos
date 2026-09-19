import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "da-auto-bump-dispatch.py"
_spec = importlib.util.spec_from_file_location("da_auto_bump_dispatch", MODULE_PATH)
dispatch = importlib.util.module_from_spec(_spec)
sys.modules["da_auto_bump_dispatch"] = dispatch
_spec.loader.exec_module(dispatch)

pause_gate = dispatch.pause_gate
select_credential = dispatch.select_credential
classify_engine_result = dispatch.classify_engine_result
render_job_summary = dispatch.render_job_summary
render_pushed_commit_line = dispatch.render_pushed_commit_line
render_paused_summary = dispatch.render_paused_summary
build_commit_message = dispatch.build_commit_message
build_listing_issue_body = dispatch.build_listing_issue_body
open_listing_issue = dispatch.open_listing_issue
has_da_auto_bump_trailer = dispatch._load_has_da_auto_bump_trailer()


def _base_summary(**overrides):
    summary = {
        "changed": False,
        "outcome": "already_pinned",
        "previous_pack_version": "0.22.1",
        "new_pack_version": None,
        "da_version": "0.19.1-alpha.1",
        "modrinth_version_id": "abc123",
        "sha512": "d" * 128,
        "category_received": "minor",
        "mapping_rule": None,
        "notes_path": None,
        "vouch": None,
        "listing_review_required": False,
        "listing_flagged_lines": [],
        "error": None,
    }
    summary.update(overrides)
    return summary


class TestPauseGate(unittest.TestCase):
    def test_unset_runs(self):
        self.assertFalse(pause_gate(None))

    def test_true_pauses(self):
        self.assertTrue(pause_gate("true"))

    def test_false_runs(self):
        self.assertFalse(pause_gate("false"))

    def test_uppercase_true_runs(self):
        # Exactly "true" pauses; "TRUE" is treated as any other value, per
        # the ticket's explicit test case.
        self.assertFalse(pause_gate("TRUE"))

    def test_empty_runs(self):
        self.assertFalse(pause_gate(""))


class TestSelectCredential(unittest.TestCase):
    def test_full_app_pair_selects_app(self):
        selection = select_credential(app_id="123", app_key_set=True, pat_set=True)
        self.assertEqual(selection.method, "app")
        self.assertEqual(selection.warnings, [])

    def test_no_credentials_selects_none(self):
        selection = select_credential(app_id="", app_key_set=False, pat_set=False)
        self.assertEqual(selection.method, "none")
        self.assertEqual(selection.warnings, [])

    def test_pat_only_selects_pat(self):
        selection = select_credential(app_id="", app_key_set=False, pat_set=True)
        self.assertEqual(selection.method, "pat")
        self.assertEqual(selection.warnings, [])

    def test_half_configured_app_id_only_warns_and_falls_back_to_pat(self):
        selection = select_credential(app_id="123", app_key_set=False, pat_set=True)
        self.assertEqual(selection.method, "pat")
        self.assertEqual(len(selection.warnings), 1)
        self.assertIn("SICKOS_DISPATCH_APP_PRIVATE_KEY", selection.warnings[0])

    def test_half_configured_app_key_only_warns_and_falls_back_to_pat(self):
        selection = select_credential(app_id="", app_key_set=True, pat_set=True)
        self.assertEqual(selection.method, "pat")
        self.assertEqual(len(selection.warnings), 1)
        self.assertIn("SICKOS_DISPATCH_APP_ID", selection.warnings[0])

    def test_half_configured_app_with_no_pat_selects_none_and_warns(self):
        selection = select_credential(app_id="123", app_key_set=False, pat_set=False)
        self.assertEqual(selection.method, "none")
        self.assertEqual(len(selection.warnings), 1)


class TestClassifyEngineResult(unittest.TestCase):
    def test_exit_0_already_pinned_is_no_op(self):
        self.assertEqual(classify_engine_result(0, "already_pinned"), "no_op")

    def test_exit_0_bumped_is_bump(self):
        self.assertEqual(classify_engine_result(0, "bumped"), "bump")

    def test_exit_0_unrecognised_outcome_fails_closed(self):
        self.assertEqual(classify_engine_result(0, "something_new"), "failure")

    def test_every_documented_nonzero_exit_code_is_failure(self):
        # docs/da-auto-bump.md's exit code table, as merged with SICKOS-77.
        for code in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11):
            with self.subTest(code=code):
                self.assertEqual(classify_engine_result(code, "some_outcome"), "failure")

    def test_unmapped_future_exit_code_fails_closed(self):
        # A code the merged doc has never documented must still fail
        # closed, never silently pass as success.
        self.assertEqual(classify_engine_result(12, None), "failure")

    def test_unhandled_exception_exit_code_1_is_failure(self):
        self.assertEqual(classify_engine_result(1, None), "failure")


class TestRenderJobSummary(unittest.TestCase):
    def test_no_op_never_claims_hashes_were_verified(self):
        text = render_job_summary(_base_summary(), action="no_op")
        self.assertIn("not applicable", text)
        self.assertNotIn("verified (sha1", text)

    def test_bump_claims_verified(self):
        summary = _base_summary(
            changed=True, outcome="bumped", new_pack_version="0.23.0",
            mapping_rule="breaking_as_minor", notes_path="docs/releases/0.23.0.md",
            vouch={"control_status": 200, "da_status": 404, "result": "not_vouched", "error": None},
        )
        text = render_job_summary(summary, action="bump")
        self.assertIn("verified (sha1 and sha512", text)
        self.assertIn("docs/releases/0.23.0.md", text)
        self.assertIn("not_vouched", text)

    def test_integrity_failure_says_failed_not_verified(self):
        summary = _base_summary(outcome="integrity_failure", error="hash mismatch")
        text = render_job_summary(summary, action="failure")
        self.assertIn("failed (downloaded jar hash mismatch", text)
        self.assertIn("hash mismatch", text)

    def test_other_failure_does_not_claim_verified(self):
        summary = _base_summary(outcome="gate_failed", error="make check failed")
        text = render_job_summary(summary, action="failure")
        self.assertNotIn("verified (sha1", text)
        self.assertIn("not verified", text)

    def test_listing_review_required_lists_flagged_lines(self):
        summary = _base_summary(
            listing_review_required=True,
            listing_flagged_lines=[{"file": "README.md", "line": 20, "text": "Sickos loves Vapor."}],
        )
        text = render_job_summary(summary, action="bump")
        self.assertIn("README.md:20", text)

    def test_failure_action_includes_error_field(self):
        summary = _base_summary(outcome="dirty_tree", error="pack.toml is dirty")
        text = render_job_summary(summary, action="failure")
        self.assertIn("**Error:** pack.toml is dirty", text)


class TestRenderPushedCommitLine(unittest.TestCase):
    def test_no_op(self):
        line = render_pushed_commit_line(action="no_op", credential_method="none", pushed_sha=None, push_failed=False)
        self.assertIn("already pinned", line)

    def test_failure(self):
        line = render_pushed_commit_line(action="failure", credential_method="none", pushed_sha=None, push_failed=False)
        self.assertIn("run failed", line)

    def test_bump_no_credential(self):
        line = render_pushed_commit_line(action="bump", credential_method="none", pushed_sha=None, push_failed=False)
        self.assertIn("no push credential configured", line)

    def test_bump_push_rejected(self):
        line = render_pushed_commit_line(action="bump", credential_method="app", pushed_sha=None, push_failed=True)
        self.assertIn("rejected", line)
        self.assertIn("safe to re-run", line)

    def test_bump_pushed(self):
        line = render_pushed_commit_line(action="bump", credential_method="pat", pushed_sha="abc1234", push_failed=False)
        self.assertIn("abc1234", line)


class TestRenderPausedSummary(unittest.TestCase):
    def test_names_the_declined_version(self):
        text = render_paused_summary("0.20.0-alpha.1")
        self.assertIn("0.20.0-alpha.1", text)
        self.assertIn("NOT queued", text)

    def test_unknown_version_falls_back(self):
        text = render_paused_summary(None)
        self.assertIn("unknown", text)


class TestBuildCommitMessage(unittest.TestCase):
    def test_patch_uses_fix_prefix(self):
        message = build_commit_message(
            mapping_rule="patch", new_pack_version="0.22.2",
            da_version="0.19.2", modrinth_version_id="abc123",
        )
        self.assertTrue(message.startswith("fix: release Sickos 0.22.2"))

    def test_minor_uses_feat_prefix(self):
        message = build_commit_message(
            mapping_rule="minor", new_pack_version="0.23.0",
            da_version="0.20.0", modrinth_version_id="abc123",
        )
        self.assertTrue(message.startswith("feat: release Sickos 0.23.0"))

    def test_breaking_uses_feat_prefix(self):
        message = build_commit_message(
            mapping_rule="breaking_as_minor", new_pack_version="0.23.0",
            da_version="0.20.0-alpha.1", modrinth_version_id="abc123",
        )
        self.assertTrue(message.startswith("feat: release Sickos 0.23.0"))

    def test_trailer_is_recognised_by_the_real_gate_function(self):
        message = build_commit_message(
            mapping_rule="breaking_as_minor", new_pack_version="0.23.0",
            da_version="0.20.0-alpha.1", modrinth_version_id="abc123",
        )
        self.assertTrue(has_da_auto_bump_trailer(message))

    def test_trailer_is_alone_in_its_final_paragraph(self):
        message = build_commit_message(
            mapping_rule="minor", new_pack_version="0.23.0",
            da_version="0.20.0", modrinth_version_id="abc123",
        )
        # The body above the trailer is a multi-line paragraph plus a
        # bullet list -- exactly the shape SICKOS-79's review flagged as
        # unrecognised when a trailer isn't in its own final paragraph.
        final_paragraph = message.strip("\n").split("\n\n")[-1]
        self.assertEqual(final_paragraph, "DA-Auto-Bump: 0.20.0 abc123")

    def test_trailer_survives_multiline_body_and_bullet_list(self):
        message = build_commit_message(
            mapping_rule="breaking_as_minor", new_pack_version="1.0.0",
            da_version="1.0.0-beta.1", modrinth_version_id="xyz789",
        )
        # Sanity: the body really does contain a bullet list above the
        # trailer, and the gate still finds it.
        self.assertIn("- Dynamic Atmosphere version:", message)
        self.assertIn("- Mapping rule:", message)
        self.assertTrue(has_da_auto_bump_trailer(message))


class TestListingIssue(unittest.TestCase):
    def test_body_lists_flagged_lines(self):
        summary = _base_summary(
            listing_review_required=True,
            listing_flagged_lines=[
                {"file": "README.md", "line": 20, "text": "Violence and Slime are unaffected."},
                {"file": ".modrinth/description.md", "line": 5, "text": "See Violence config."},
            ],
        )
        body = build_listing_issue_body(summary)
        self.assertIn("README.md:20", body)
        self.assertIn(".modrinth/description.md:5", body)
        self.assertIn("NOT auto-updated", body)

    def test_does_nothing_when_not_required(self):
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            raise AssertionError("gh should never be invoked when listing_review_required is false")

        open_listing_issue(
            summary=_base_summary(listing_review_required=False),
            new_pack_version="0.22.2", da_version="0.19.2",
            repo="brooswit-minecraft/sickos", run_command=fake_run,
        )
        self.assertEqual(calls, [])

    def test_failure_to_create_issue_does_not_raise(self):
        class FakeResult:
            returncode = 1
            stderr = "gh: some API error"

        def fake_run(*args, **kwargs):
            return FakeResult()

        # Must not raise.
        open_listing_issue(
            summary=_base_summary(listing_review_required=True, listing_flagged_lines=[]),
            new_pack_version="0.22.2", da_version="0.19.2",
            repo="brooswit-minecraft/sickos", run_command=fake_run,
        )

    def test_exception_creating_issue_does_not_raise(self):
        def fake_run(*args, **kwargs):
            raise RuntimeError("gh not installed")

        open_listing_issue(
            summary=_base_summary(listing_review_required=True, listing_flagged_lines=[]),
            new_pack_version="0.22.2", da_version="0.19.2",
            repo="brooswit-minecraft/sickos", run_command=fake_run,
        )

    def test_never_creates_labels(self):
        captured = {}

        class FakeResult:
            returncode = 0
            stderr = ""

        def fake_run(args, **kwargs):
            captured["args"] = args
            return FakeResult()

        open_listing_issue(
            summary=_base_summary(listing_review_required=True, listing_flagged_lines=[]),
            new_pack_version="0.22.2", da_version="0.19.2",
            repo="brooswit-minecraft/sickos", run_command=fake_run,
        )
        self.assertNotIn("--label", captured["args"])


class TestCLI(unittest.TestCase):
    def test_decide_writes_github_output_and_step_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            summary_path.write_text(json.dumps(_base_summary(
                changed=True, outcome="bumped", new_pack_version="0.23.0",
                mapping_rule="minor", notes_path="docs/releases/0.23.0.md",
            )), encoding="utf-8")
            output_path = tmp_path / "output.txt"
            step_summary_path = tmp_path / "summary.md"
            output_path.write_text("", encoding="utf-8")
            step_summary_path.write_text("", encoding="utf-8")

            exit_code = dispatch.main([
                "decide", "--exit-code", "0", "--summary-path", str(summary_path),
                "--github-output", str(output_path), "--github-step-summary", str(step_summary_path),
            ])
            self.assertEqual(exit_code, 0)
            output_text = output_path.read_text(encoding="utf-8")
            self.assertIn("action=bump", output_text)
            self.assertIn("new_pack_version=0.23.0", output_text)
            self.assertIn("## DA auto-bump: bumped", step_summary_path.read_text(encoding="utf-8"))

    def test_build_commit_message_cli_rejects_a_bad_trailer_shape(self):
        # Defence in depth: if build_commit_message ever regresses to
        # produce a trailer git's parser wouldn't recognise, the CLI must
        # fail loudly rather than hand the workflow a silently-broken
        # commit message.
        original = dispatch.build_commit_message
        try:
            dispatch.build_commit_message = lambda **kwargs: "subject\nDA-Auto-Bump: x y\n"
            exit_code = dispatch.main([
                "build-commit-message", "--mapping-rule", "patch",
                "--new-pack-version", "0.22.2", "--da-version", "0.19.2",
                "--modrinth-version-id", "abc123",
            ])
            self.assertEqual(exit_code, 1)
        finally:
            dispatch.build_commit_message = original


if __name__ == "__main__":
    unittest.main()
