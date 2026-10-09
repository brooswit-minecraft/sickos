import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "release_decisions.py"
_spec = importlib.util.spec_from_file_location("release_decisions", MODULE_PATH)
release_decisions = importlib.util.module_from_spec(_spec)
sys.modules["release_decisions"] = release_decisions
_spec.loader.exec_module(release_decisions)

has_trailer = release_decisions.has_trailer
trailer_value = release_decisions.trailer_value
parse_hold = release_decisions.parse_hold
find_skipped_intermediate_versions = release_decisions.find_skipped_intermediate_versions
resolve_existing_release = release_decisions.resolve_existing_release
conflict_message = release_decisions.conflict_message
check_modrinth_config = release_decisions.check_modrinth_config
missing_config_message = release_decisions.missing_config_message
check_identity = release_decisions.check_identity
resolve_duplicate_publish = release_decisions.resolve_duplicate_publish
resolve_verify_publication = release_decisions.resolve_verify_publication
outcome_to_status = release_decisions.outcome_to_status
resolve_run_outcome = release_decisions.resolve_run_outcome

# docs/release-policy.md's durable-evidence baseline: sickos's own real,
# already-published v0.23.2 GitHub Release, verified live via
# `gh release view v0.23.2 --json targetCommitish,tagName`.
REAL_V0_23_2_TARGET_COMMITISH = "c8c15c445e920b1ad8c412b7f1b3bb88e0658b33"


class TestHoldTrailer(unittest.TestCase):
    def test_present_trailer_detected(self):
        message = "Batch three accepted merges\n\nReviewed together.\n\nRelease-Hold: true\n"
        result = parse_hold(message)
        self.assertTrue(result["held"])
        self.assertIsNone(result["reason"])

    def test_absent_trailer_not_held(self):
        message = "Add a new recipe\n\nOrdinary pack change.\n"
        result = parse_hold(message)
        self.assertFalse(result["held"])
        self.assertIsNone(result["reason"])

    def test_malformed_trailer_not_detected(self):
        # No blank line separating it from prose -- git's trailer parser
        # requires the trailer block to be its own final paragraph.
        message = "Add a mod\nRelease-Hold: true\n"
        self.assertFalse(has_trailer("Release-Hold", message))

    def test_mention_glued_to_prose_with_no_blank_line_not_detected(self):
        message = (
            "Fix a bug\n"
            "\n"
            "This change should set Release-Hold: true informally but it's just prose\n"
            "in the middle of a paragraph, not its own trailer block.\n"
        )
        self.assertFalse(has_trailer("Release-Hold", message))

    def test_reason_captured_verbatim(self):
        message = (
            "Batch several small merges\n"
            "\n"
            "Holding until the batch is complete.\n"
            "\n"
            "Release-Hold: true\n"
            "Release-Hold-Reason: waiting on two more PRs before cutting a release\n"
        )
        result = parse_hold(message)
        self.assertTrue(result["held"])
        self.assertEqual(result["reason"], "waiting on two more PRs before cutting a release")

    def test_trailer_key_is_case_insensitive(self):
        message = "Subject\n\nBody.\n\nrelease-hold: true\n"
        self.assertTrue(has_trailer("Release-Hold", message))

    def test_only_head_commit_message_is_ever_inspected(self):
        # parse_hold takes exactly one message string -- proving the
        # documented "Rebase and merge" limitation (a trailer on an earlier
        # commit of a multi-commit push is invisible) is a property of this
        # function's own signature, not just asserted in prose: there is no
        # way to hand it more than one commit's message.
        head_commit_message = "Second commit of a rebase-merged push\n\nNo trailer here.\n"
        self.assertFalse(parse_hold(head_commit_message)["held"])


class TestSkippedIntermediateVersions(unittest.TestCase):
    def test_all_versions_released_none_skipped(self):
        history = ["0.23.0", "0.23.1", "0.23.2"]
        released = {"0.23.0", "0.23.1", "0.23.2"}
        self.assertEqual(find_skipped_intermediate_versions(history, released), [])

    def test_middle_version_dropped_by_concurrency_is_detected(self):
        history = ["0.23.0", "0.23.1", "0.23.2"]
        released = {"0.23.0", "0.23.2"}
        self.assertEqual(find_skipped_intermediate_versions(history, released), ["0.23.1"])

    def test_duplicates_in_history_reported_once(self):
        history = ["0.23.0", "0.23.1", "0.23.1", "0.23.2"]
        released = {"0.23.0", "0.23.2"}
        self.assertEqual(find_skipped_intermediate_versions(history, released), ["0.23.1"])

    def test_order_follows_first_appearance(self):
        history = ["0.23.2", "0.23.1", "0.23.0"]
        released = {"0.23.2"}
        self.assertEqual(find_skipped_intermediate_versions(history, released), ["0.23.1", "0.23.0"])


class TestResolveExistingRelease(unittest.TestCase):
    def test_no_release_exists(self):
        result = resolve_existing_release(None, "a" * 40)
        self.assertEqual(result["branch"], "no_release")

    def test_same_version_same_sha_is_replay(self):
        sha = "a" * 40
        result = resolve_existing_release(sha, sha)
        self.assertEqual(result["branch"], "replay")

    def test_same_version_different_sha_is_conflict(self):
        result = resolve_existing_release("a" * 40, "b" * 40)
        self.assertEqual(result["branch"], "conflict")
        self.assertEqual(result["existing_sha"], "a" * 40)
        self.assertEqual(result["this_sha"], "b" * 40)

    def test_conflict_message_names_both_shas(self):
        message = conflict_message("0.24.0", "a" * 40, "b" * 40)
        self.assertIn("a" * 40, message)
        self.assertIn("b" * 40, message)
        self.assertIn("0.24.0", message)

    def test_conflict_against_real_published_history_with_fabricated_different_sha(self):
        # docs/release-policy.md's own precedent for exercising this branch
        # safely: run the check function against a real, already-published
        # release with a fabricated *different* sha, never a real publish.
        result = resolve_existing_release(REAL_V0_23_2_TARGET_COMMITISH, "f" * 40)
        self.assertEqual(result["branch"], "conflict")


class TestModrinthConfig(unittest.TestCase):
    def test_both_set_is_configured(self):
        result = check_modrinth_config("token", "project")
        self.assertTrue(result["configured"])
        self.assertEqual(result["missing"], [])

    def test_token_missing(self):
        result = check_modrinth_config(None, "project")
        self.assertFalse(result["configured"])
        self.assertEqual(result["missing"], ["MODRINTH_TOKEN"])

    def test_project_id_missing(self):
        result = check_modrinth_config("token", "")
        self.assertFalse(result["configured"])
        self.assertEqual(result["missing"], ["MODRINTH_PROJECT_ID"])

    def test_both_missing(self):
        result = check_modrinth_config(None, None)
        self.assertFalse(result["configured"])
        self.assertEqual(result["missing"], ["MODRINTH_TOKEN", "MODRINTH_PROJECT_ID"])

    def test_message_names_exact_missing_vars_and_points_at_readme(self):
        message = missing_config_message(["MODRINTH_TOKEN"])
        self.assertIn("MODRINTH_TOKEN", message)
        self.assertNotIn("MODRINTH_PROJECT_ID", message)
        self.assertIn("README.md#secrets--variables", message)

    def test_message_names_both_when_both_missing(self):
        message = missing_config_message(["MODRINTH_TOKEN", "MODRINTH_PROJECT_ID"])
        self.assertIn("MODRINTH_TOKEN", message)
        self.assertIn("MODRINTH_PROJECT_ID", message)
        self.assertIn("README.md#secrets--variables", message)


class TestMissingConfigResolvesToFailedNeverSkipped(unittest.TestCase):
    """Both check-config's (first-attempt) and recover-publish's (replay)
    not-configured branches, per docs/release-policy.md Section 2.2/4."""

    def test_check_config_not_configured_is_failed_not_skipped(self):
        config = check_modrinth_config(None, None)
        self.assertFalse(config["configured"])
        outcome = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="no_release", conflict_detail=None,
            config_missing=config["missing"],
            release_job_result=None, recover_outcome=None, recover_detail=None,
            verify_outcome=None, verify_detail=None,
        )
        self.assertEqual(outcome["outcome"], "failed")
        self.assertEqual(outcome["state"], "failure")
        self.assertNotEqual(outcome["outcome"], "skipped")

    def test_recover_publish_not_configured_is_failed_not_skipped(self):
        config = check_modrinth_config("token", None)
        self.assertFalse(config["configured"])
        outcome = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="replay", conflict_detail=None,
            config_missing=config["missing"],
            release_job_result=None, recover_outcome=None, recover_detail=None,
            verify_outcome=None, verify_detail=None,
        )
        self.assertEqual(outcome["outcome"], "failed")
        self.assertEqual(outcome["state"], "failure")
        self.assertNotEqual(outcome["outcome"], "skipped")


class TestIdentityMatchMismatch(unittest.TestCase):
    def _version_json(self, **overrides):
        base = {
            "version_number": "0.24.0",
            "files": [{"filename": "sickos-0.24.0.mrpack", "primary": True}],
            "changelog": "Source-Revision: " + "a" * 40 + "\n",
        }
        base.update(overrides)
        return base

    def test_exact_match_no_source_revision_check(self):
        result = check_identity(
            self._version_json(),
            expected_version_number="0.24.0",
            expected_sha="a" * 40,
            pack_name="sickos",
            source_revision_available=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["source_revision_status"], "not_available")

    def test_version_number_mismatch(self):
        result = check_identity(
            self._version_json(version_number="0.24.1"),
            expected_version_number="0.24.0",
            expected_sha="a" * 40,
            pack_name="sickos",
            source_revision_available=False,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("version_number" in m for m in result["mismatches"]))

    def test_filename_mismatch(self):
        result = check_identity(
            self._version_json(files=[{"filename": "sickos-0.23.9.mrpack", "primary": True}]),
            expected_version_number="0.24.0",
            expected_sha="a" * 40,
            pack_name="sickos",
            source_revision_available=False,
        )
        self.assertFalse(result["ok"])

    def test_multiple_matching_files_is_a_mismatch(self):
        result = check_identity(
            self._version_json(files=[
                {"filename": "sickos-0.24.0.mrpack", "primary": True},
                {"filename": "sickos-0.24.0.mrpack", "primary": False},
            ]),
            expected_version_number="0.24.0",
            expected_sha="a" * 40,
            pack_name="sickos",
            source_revision_available=False,
        )
        self.assertFalse(result["ok"])

    def test_source_revision_match_when_available(self):
        result = check_identity(
            self._version_json(),
            expected_version_number="0.24.0",
            expected_sha="a" * 40,
            pack_name="sickos",
            source_revision_available=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["source_revision_status"], "match")

    def test_source_revision_mismatch_when_available(self):
        result = check_identity(
            self._version_json(),
            expected_version_number="0.24.0",
            expected_sha="b" * 40,
            pack_name="sickos",
            source_revision_available=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["source_revision_status"], "mismatch")

    def test_source_revision_absent_when_available_is_mismatch_not_skip(self):
        result = check_identity(
            self._version_json(changelog="no trailer here"),
            expected_version_number="0.24.0",
            expected_sha="a" * 40,
            pack_name="sickos",
            source_revision_available=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["source_revision_status"], "mismatch")


class TestDuplicatePublishFallback(unittest.TestCase):
    def test_identity_matches_resolves_published(self):
        version_json = {
            "version_number": "0.24.0",
            "files": [{"filename": "sickos-0.24.0.mrpack", "primary": True}],
            "changelog": "",
        }
        result = resolve_duplicate_publish(
            version_json, expected_version_number="0.24.0", expected_sha="a" * 40, pack_name="sickos",
        )
        self.assertEqual(result["outcome"], "published")

    def test_identity_mismatch_resolves_failed(self):
        version_json = {
            "version_number": "0.24.0",
            "files": [{"filename": "sickos-0.23.9.mrpack", "primary": True}],
            "changelog": "",
        }
        result = resolve_duplicate_publish(
            version_json, expected_version_number="0.24.0", expected_sha="a" * 40, pack_name="sickos",
        )
        self.assertEqual(result["outcome"], "failed")


class TestVerifyPublication(unittest.TestCase):
    def _version_json(self):
        return {
            "version_number": "0.24.0",
            "files": [{"filename": "sickos-0.24.0.mrpack", "primary": True}],
            "changelog": "",
        }

    def test_success_and_match_is_published(self):
        result = resolve_verify_publication(
            rinth_exit_code=0, version_json=self._version_json(),
            expected_version_number="0.24.0", expected_sha="a" * 40, pack_name="sickos",
        )
        self.assertEqual(result["outcome"], "published")
        self.assertIn("MINECRAFT-44 pending", result["detail"])

    def test_wait_timeout_is_failed(self):
        result = resolve_verify_publication(
            rinth_exit_code=release_decisions.RINTH_EXIT_WAIT_TIMEOUT, version_json=None,
            expected_version_number="0.24.0", expected_sha="a" * 40, pack_name="sickos",
        )
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("timed out", result["detail"])

    def test_unreadable_project_is_failed(self):
        result = resolve_verify_publication(
            rinth_exit_code=release_decisions.RINTH_EXIT_NOT_FOUND, version_json=None,
            expected_version_number="0.24.0", expected_sha="a" * 40, pack_name="sickos",
        )
        self.assertEqual(result["outcome"], "failed")

    def test_success_but_mismatch_is_failed_never_silent_pass(self):
        version_json = self._version_json()
        version_json["version_number"] = "0.23.9"
        result = resolve_verify_publication(
            rinth_exit_code=0, version_json=version_json,
            expected_version_number="0.24.0", expected_sha="a" * 40, pack_name="sickos",
        )
        self.assertEqual(result["outcome"], "failed")


class TestOutcomeToStatus(unittest.TestCase):
    def test_held_is_error_never_failure_or_success(self):
        result = outcome_to_status("held", "HELD")
        self.assertEqual(result["state"], "error")

    def test_skipped_is_error(self):
        result = outcome_to_status("skipped", "SKIPPED")
        self.assertEqual(result["state"], "error")

    def test_publishing_is_pending(self):
        result = outcome_to_status("publishing", "PUBLISHING")
        self.assertEqual(result["state"], "pending")

    def test_published_is_the_only_success(self):
        result = outcome_to_status("published", "PUBLISHED")
        self.assertEqual(result["state"], "success")

    def test_failed_is_failure(self):
        result = outcome_to_status("failed", "FAILED")
        self.assertEqual(result["state"], "failure")

    def test_unknown_outcome_raises(self):
        with self.assertRaises(ValueError):
            outcome_to_status("not-a-real-outcome", "x")


class TestResolveRunOutcome(unittest.TestCase):
    def test_held_short_circuits_everything_else(self):
        result = resolve_run_outcome(
            held=True, held_reason="batching merges",
            existing_branch="no_release", conflict_detail=None,
            config_missing=None, release_job_result="success",
            recover_outcome=None, recover_detail=None,
            verify_outcome="published", verify_detail="irrelevant",
        )
        self.assertEqual(result["outcome"], "held")
        self.assertEqual(result["state"], "error")
        self.assertIn("batching merges", result["description"])

    def test_conflict_is_failed(self):
        result = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="conflict", conflict_detail="sha mismatch",
            config_missing=None, release_job_result=None,
            recover_outcome=None, recover_detail=None,
            verify_outcome=None, verify_detail=None,
        )
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["state"], "failure")

    def test_first_attempt_published(self):
        result = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="no_release", conflict_detail=None,
            config_missing=[], release_job_result="success",
            recover_outcome=None, recover_detail=None,
            verify_outcome="published", verify_detail="v0.24.0 == abcdef12",
        )
        self.assertEqual(result["outcome"], "published")
        self.assertEqual(result["state"], "success")

    def test_first_attempt_build_failure(self):
        result = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="no_release", conflict_detail=None,
            config_missing=[], release_job_result="failure",
            recover_outcome=None, recover_detail=None,
            verify_outcome=None, verify_detail=None,
        )
        self.assertEqual(result["outcome"], "failed")

    def test_first_attempt_verify_publication_mismatch(self):
        result = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="no_release", conflict_detail=None,
            config_missing=[], release_job_result="success",
            recover_outcome=None, recover_detail=None,
            verify_outcome="failed", verify_detail="filename mismatch",
        )
        self.assertEqual(result["outcome"], "failed")

    def test_replay_direct_rinth_publish_success_then_verified(self):
        result = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="replay", conflict_detail=None,
            config_missing=[], release_job_result=None,
            recover_outcome="publish_succeeded", recover_detail=None,
            verify_outcome="published", verify_detail="v0.24.0 == abcdef12",
        )
        self.assertEqual(result["outcome"], "published")

    def test_replay_duplicate_fallback_match_is_published_without_verify(self):
        result = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="replay", conflict_detail=None,
            config_missing=[], release_job_result=None,
            recover_outcome="published", recover_detail="benign replay",
            verify_outcome=None, verify_detail=None,
        )
        self.assertEqual(result["outcome"], "published")

    def test_replay_duplicate_fallback_mismatch_is_failed(self):
        result = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="replay", conflict_detail=None,
            config_missing=[], release_job_result=None,
            recover_outcome="failed", recover_detail="publish-identity conflict",
            verify_outcome=None, verify_detail=None,
        )
        self.assertEqual(result["outcome"], "failed")

    def test_missing_config_never_skipped_for_sickos(self):
        result = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="no_release", conflict_detail=None,
            config_missing=["MODRINTH_TOKEN", "MODRINTH_PROJECT_ID"],
            release_job_result=None, recover_outcome=None, recover_detail=None,
            verify_outcome=None, verify_detail=None,
        )
        self.assertEqual(result["outcome"], "failed")
        self.assertNotEqual(result["outcome"], "skipped")

    def test_unrecognized_shape_fails_closed(self):
        result = resolve_run_outcome(
            held=False, held_reason=None,
            existing_branch="something-unexpected", conflict_detail=None,
            config_missing=[], release_job_result=None,
            recover_outcome=None, recover_detail=None,
            verify_outcome=None, verify_detail=None,
        )
        self.assertEqual(result["outcome"], "failed")


if __name__ == "__main__":
    unittest.main()
