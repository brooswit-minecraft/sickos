import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "apply-da-release-notes.py"
_spec = importlib.util.spec_from_file_location("apply_da_release_notes", MODULE_PATH)
apply_da_release_notes = importlib.util.module_from_spec(_spec)
sys.modules["apply_da_release_notes"] = apply_da_release_notes
_spec.loader.exec_module(apply_da_release_notes)

has_da_auto_bump_trailer = apply_da_release_notes.has_da_auto_bump_trailer
read_pack_version = apply_da_release_notes.read_pack_version
notes_file_path = apply_da_release_notes.notes_file_path
evaluate_gate = apply_da_release_notes.evaluate_gate
compose_release_body = apply_da_release_notes.compose_release_body
GateError = apply_da_release_notes.GateError


class TestHasDaAutoBumpTrailer(unittest.TestCase):
    def test_proper_trailer_is_detected(self):
        message = (
            "Bump Dynamic Atmosphere to 0.19.0-alpha.1\n"
            "\n"
            "Prepared by the auto-bump engine.\n"
            "\n"
            "DA-Auto-Bump: 0.19.0-alpha.1 abc123\n"
        )
        self.assertTrue(has_da_auto_bump_trailer(message))

    def test_mention_in_body_does_not_count(self):
        message = (
            "Bump Dynamic Atmosphere\n"
            "\n"
            "This closes the loop the DA-Auto-Bump trailer is supposed to open,\n"
            "but this is prose, not a trailer.\n"
        )
        self.assertFalse(has_da_auto_bump_trailer(message))

    def test_hand_written_trailer_with_different_value_shape_still_passes(self):
        message = (
            "Manual bump for the first real release\n"
            "\n"
            "Written by hand ahead of SICKOS-81.\n"
            "\n"
            "DA-Auto-Bump: whatever shape a human decides to write here\n"
        )
        self.assertTrue(has_da_auto_bump_trailer(message))

    def test_trailer_key_is_case_insensitive(self):
        message = "Subject\n\nBody.\n\nda-auto-bump: 1.2.3 xyz\n"
        self.assertTrue(has_da_auto_bump_trailer(message))

    def test_no_trailer_block_at_all(self):
        message = "Just a plain commit\n\nWith a body and nothing else.\n"
        self.assertFalse(has_da_auto_bump_trailer(message))

    def test_other_trailers_only(self):
        message = "Subject\n\nBody.\n\nSigned-off-by: someone <someone@example.com>\n"
        self.assertFalse(has_da_auto_bump_trailer(message))


class TestReadPackVersion(unittest.TestCase):
    def test_reads_version(self, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            (repo_root / "pack.toml").write_text(
                'name = "Sickos"\nversion = "0.23.0"\n', encoding="utf-8"
            )
            self.assertEqual(read_pack_version(repo_root), "0.23.0")

    def test_missing_version_raises(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            repo_root = Path(d)
            (repo_root / "pack.toml").write_text('name = "Sickos"\n', encoding="utf-8")
            with self.assertRaises(GateError):
                read_pack_version(repo_root)


class TestNotesFilePath(unittest.TestCase):
    def test_path_shape(self):
        repo_root = Path("/repo")
        self.assertEqual(
            notes_file_path(repo_root, "0.23.0"),
            Path("/repo/docs/releases/0.23.0.md"),
        )


class TestEvaluateGate(unittest.TestCase):
    def _repo_with(self, version="0.23.0", notes_exists=True):
        import tempfile

        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        repo_root = Path(d.name)
        (repo_root / "pack.toml").write_text(f'version = "{version}"\n', encoding="utf-8")
        releases_dir = repo_root / "docs" / "releases"
        releases_dir.mkdir(parents=True)
        if notes_exists:
            (releases_dir / f"{version}.md").write_text("# notes\n", encoding="utf-8")
        return repo_root

    def _trailer_message(self):
        return "Bump DA\n\nBody.\n\nDA-Auto-Bump: 0.19.0-alpha.1 abc123\n"

    def _no_trailer_message(self):
        return "Just a normal push\n"

    def test_passes_when_all_four_conditions_hold(self):
        repo_root = self._repo_with()
        result = evaluate_gate(
            conclusion="success",
            event="push",
            commit_message=self._trailer_message(),
            repo_root=repo_root,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.version, "0.23.0")

    def test_fails_on_conclusion_alone(self):
        repo_root = self._repo_with()
        result = evaluate_gate(
            conclusion="failure",
            event="push",
            commit_message=self._trailer_message(),
            repo_root=repo_root,
        )
        self.assertFalse(result.passed)
        self.assertIn("conclusion", result.reason)

    def test_fails_on_event_alone(self):
        repo_root = self._repo_with()
        result = evaluate_gate(
            conclusion="success",
            event="release",
            commit_message=self._trailer_message(),
            repo_root=repo_root,
        )
        self.assertFalse(result.passed)
        self.assertIn("push", result.reason)

    def test_fails_on_trailer_alone(self):
        repo_root = self._repo_with()
        result = evaluate_gate(
            conclusion="success",
            event="push",
            commit_message=self._no_trailer_message(),
            repo_root=repo_root,
        )
        self.assertFalse(result.passed)
        self.assertIn("trailer", result.reason)

    def test_fails_on_missing_notes_file_alone(self):
        repo_root = self._repo_with(notes_exists=False)
        result = evaluate_gate(
            conclusion="success",
            event="push",
            commit_message=self._trailer_message(),
            repo_root=repo_root,
        )
        self.assertFalse(result.passed)
        self.assertIn("notes file not found", result.reason)

    def test_hand_written_trailer_with_different_value_shape_still_passes_gate(self):
        repo_root = self._repo_with()
        message = "Manual bump\n\nBody.\n\nDA-Auto-Bump: whatever a human types\n"
        result = evaluate_gate(
            conclusion="success", event="push", commit_message=message, repo_root=repo_root
        )
        self.assertTrue(result.passed)


class TestComposeReleaseBody(unittest.TestCase):
    def test_fresh_body_appends_marker_and_generated_notes(self):
        notes = "## Migration\nDo the thing.\n"
        generated = "## What's Changed\n* stuff\n"
        composed = compose_release_body(generated, notes)
        self.assertTrue(composed.startswith("## Migration\nDo the thing."))
        self.assertIn(apply_da_release_notes.MARKER, composed)
        self.assertTrue(composed.rstrip("\n").endswith(generated.rstrip("\n")))
        # Marker must come after the notes and before the generated notes.
        self.assertLess(
            composed.index(apply_da_release_notes.MARKER),
            composed.index("What's Changed"),
        )

    def test_rerun_on_already_edited_body_is_byte_identical(self):
        notes = "## Migration\nDo the thing.\n"
        generated = "## What's Changed\n* stuff\n"
        first = compose_release_body(generated, notes)
        second = compose_release_body(first, notes)
        self.assertEqual(first, second)

    def test_empty_generated_notes_body(self):
        notes = "## Migration\nDo the thing.\n"
        first = compose_release_body("", notes)
        self.assertNotIn("\n\n\n", first)
        second = compose_release_body(first, notes)
        self.assertEqual(first, second)

    def test_null_body_treated_as_empty(self):
        notes = "## Migration\nDo the thing.\n"
        first = compose_release_body(None, notes)
        second = compose_release_body(first, notes)
        self.assertEqual(first, second)

    def test_generated_notes_never_duplicated_across_two_reruns(self):
        notes = "## Migration\nDo the thing.\n"
        generated = "## What's Changed\n* stuff\n"
        first = compose_release_body(generated, notes)
        second = compose_release_body(first, notes)
        third = compose_release_body(second, notes)
        self.assertEqual(first.count("What's Changed"), 1)
        self.assertEqual(second.count("What's Changed"), 1)
        self.assertEqual(third.count("What's Changed"), 1)


if __name__ == "__main__":
    unittest.main()
