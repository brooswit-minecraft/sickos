import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bump-dynamic-atmosphere.py"
SPEC = importlib.util.spec_from_file_location("bump_dynamic_atmosphere", MODULE_PATH)
bump = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bump)


OLD_DA_HASH = "a" * 128
CONTROL_HASH = "c" * 128
OLD_VERSION_ID = "OLDID1234"
CONTROL_MOD_ID = "LNytGWDc"

STUB_GATES = {"refresh": ["true"], "check": ["true"], "build": ["true"]}
FAILING_CHECK_GATES = {"refresh": ["true"], "check": ["false"], "build": ["true"]}
FAILING_BUILD_GATES = {"refresh": ["true"], "check": ["true"], "build": ["false"]}
MISSING_BINARY_GATES = {
    "refresh": ["/no/such/binary-da-bump-test-missing"],
    "check": ["true"], "build": ["true"],
}

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(repo), check=check,
                           capture_output=True, text=True)


def build_fixture_repo():
    repo = Path(tempfile.mkdtemp(prefix="da-bump-fixture-"))
    (repo / "mods").mkdir()
    (repo / ".modrinth").mkdir()
    (repo / "docs").mkdir()

    (repo / "pack.toml").write_text(
        'name = "Sickos"\n'
        'author = "brooswit"\n'
        'version = "0.22.0"\n'
        'pack-format = "packwiz:1.1.0"\n'
        "\n"
        "[index]\n"
        'file = "index.toml"\n'
        'hash-format = "sha256"\n'
        'hash = "deadbeef"\n'
        "\n"
        "[versions]\n"
        'minecraft = "1.21.1"\n'
        'neoforge = "21.1.250"\n',
        encoding="utf-8",
    )
    (repo / "index.toml").write_text('hash-format = "sha256"\n', encoding="utf-8")

    (repo / "mods" / "dynamic-atmosphere.pw.toml").write_text(
        'name = "Dynamic Atmosphere"\n'
        'filename = "dynamicatmosphere-0.19.0-alpha.1.jar"\n'
        'side = "both"\n'
        "\n"
        "[download]\n"
        'url = "https://cdn.modrinth.com/data/PZV7RorC/versions/OLDID1234/dynamicatmosphere-0.19.0-alpha.1.jar"\n'
        'hash-format = "sha512"\n'
        f'hash = "{OLD_DA_HASH}"\n'
        "\n"
        "[update]\n"
        "[update.modrinth]\n"
        'mod-id = "PZV7RorC"\n'
        f'version = "{OLD_VERSION_ID}"\n',
        encoding="utf-8",
    )
    (repo / "mods" / "create.pw.toml").write_text(
        'name = "Create"\n'
        'filename = "create-6.0.4.jar"\n'
        'side = "both"\n'
        "\n"
        "[download]\n"
        'url = "https://cdn.modrinth.com/data/LNytGWDc/versions/xyz/create-6.0.4.jar"\n'
        'hash-format = "sha512"\n'
        f'hash = "{CONTROL_HASH}"\n'
        "\n"
        "[update]\n"
        "[update.modrinth]\n"
        f'mod-id = "{CONTROL_MOD_ID}"\n'
        'version = "xyz"\n',
        encoding="utf-8",
    )

    (repo / "README.md").write_text(
        "# Sickos\n"
        "\n"
        "Intro paragraph with no atmosphere keywords.\n"
        "\n"
        "## Dynamic Atmosphere\n"
        "\n"
        "Sickos 0.22.0 pins Dynamic Atmosphere 0.19.0-alpha.1 for client and server.\n"
        "Vapor and Smoke behave in a way described here.\n"
        "Ender Gas bursts randomly at night.\n"
        "\n"
        "## HarvestCraft\n"
        "\n"
        "Unrelated section that must never be flagged.\n",
        encoding="utf-8",
    )
    (repo / ".modrinth" / "description.md").write_text(
        "## Included mods\n"
        "\n"
        "- Dynamic Atmosphere (atmospheric cells with Vapor and Smoke)\n"
        "\n"
        "Some more prose about atmospheric behaviour here.\n"
        "\n"
        "## Install\n"
        "\n"
        "Unrelated install instructions.\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("/build/\n", encoding="utf-8")

    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def make_downloader(content):
    def downloader(url, dest_path):
        Path(dest_path).write_bytes(content)
    return downloader


# Honest Modrinth version metadata, keyed by version id, registered by make_payload()
# so every test's payload verifies unless a test deliberately overrides its entry.
MODRINTH_METADATA = {}


def modrinth_metadata_for(payload, *, project_id="PZV7RorC", version_id=None,
                           game_versions=("1.21.1",), loaders=("neoforge",)):
    # game_versions/loaders match the fixture pack's own target (see
    # build_fixture_repo below: minecraft = "1.21.1", neoforge = ...) --
    # MINECRAFT-362 review, seventeenth round added this check to
    # verify_against_modrinth_metadata/verify_version_file_metadata, so
    # every test relying on this default helper for an otherwise-honest
    # metadata response needs these fields present and matching, or the
    # new check fails them closed for a reason unrelated to what they
    # actually test.
    return {
        "id": version_id or payload["modrinth_version_id"],
        "project_id": project_id,
        "version_number": payload["version"],
        "game_versions": list(game_versions),
        "loaders": list(loaders),
        "files": [{
            "filename": payload["file_name"],
            "url": payload["download_url"],
            "hashes": {"sha1": payload["sha1"], "sha512": payload["sha512"]},
        }],
    }


def fake_modrinth_fetch(version_id):
    if version_id not in MODRINTH_METADATA:
        raise urllib.error.HTTPError("https://api.modrinth.com/v2/version/x", 404, "Not Found", {}, None)
    return MODRINTH_METADATA[version_id]


def make_payload(*, version="0.20.0-alpha.1", modrinth_version_id="NEWID5678",
                  content=b"new da jar contents", category="minor",
                  download_url=None, file_name=None, sha1=None, sha512=None):
    content_sha1 = hashlib.sha1(content).hexdigest() if sha1 is None else sha1
    content_sha512 = hashlib.sha512(content).hexdigest() if sha512 is None else sha512
    payload = {
        "version": version,
        "modrinth_version_id": modrinth_version_id,
        "sha1": content_sha1,
        "sha512": content_sha512,
        "download_url": download_url or f"https://cdn.modrinth.com/data/PZV7RorC/versions/{modrinth_version_id}/dynamicatmosphere-{version}.jar",
        "file_name": file_name or f"dynamicatmosphere-{version}.jar",
        "category": category,
        "github_release_url": f"https://github.com/brooswit-minecraft/dynamic-atmosphere/releases/tag/v{version}",
    }
    MODRINTH_METADATA[modrinth_version_id] = modrinth_metadata_for(payload)
    return payload


def stub_vouch(control_status, da_status):
    def fake_status(sha512):
        if sha512 == CONTROL_HASH:
            return control_status
        return da_status
    return mock.patch.object(bump, "_version_file_status", side_effect=fake_status)


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        MODRINTH_METADATA.clear()
        patcher = mock.patch.object(bump, "fetch_modrinth_version_metadata", side_effect=fake_modrinth_fetch)
        self.fetch_mock = patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = build_fixture_repo()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

    def porcelain(self):
        return _git(self.repo, "status", "--porcelain").stdout

    def run_engine(self, payload, content=b"new da jar contents", gates=None, **kwargs):
        downloader = kwargs.pop("downloader", None) or make_downloader(content)
        gate_commands = gates if gates is not None else STUB_GATES
        with stub_vouch(200, 404):
            return bump.run_engine(
                self.repo, payload, downloader=downloader, gate_commands=gate_commands, **kwargs
            )


class TestAlreadyPinned(EngineTestCase):
    def test_already_pinned_is_clean_noop(self):
        before = self.repo_tree_snapshot()
        payload = make_payload(
            version="0.19.0-alpha.1", modrinth_version_id=OLD_VERSION_ID,
            content=b"whatever, hash overridden below", sha1="0" * 40, sha512=OLD_DA_HASH,
        )
        summary, exit_code, message = self.run_engine(payload)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertFalse(summary["changed"])
        self.assertEqual(summary["outcome"], "already_pinned")
        self.assertIn("already pinned", message.lower())
        self.assertEqual(self.repo_tree_snapshot(), before)
        self.assertEqual(self.porcelain(), "")

    def repo_tree_snapshot(self):
        return {
            path: (self.repo / path).read_text(encoding="utf-8")
            for path in ("pack.toml", "README.md", "mods/dynamic-atmosphere.pw.toml")
        }


class TestHashAnomaly(EngineTestCase):
    def test_same_version_id_different_hash_is_anomaly(self):
        payload = make_payload(
            version="0.19.0-alpha.1", modrinth_version_id=OLD_VERSION_ID,
            content=b"different bytes entirely",
        )
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_ANOMALY)
        self.assertEqual(self.porcelain(), "")


class TestDowngradeRefused(EngineTestCase):
    def test_older_version_is_refused(self):
        payload = make_payload(version="0.18.0-alpha.1", modrinth_version_id="OLDER1")
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_DOWNGRADE_REFUSED)
        self.assertEqual(self.porcelain(), "")


class TestIntegrityFailures(EngineTestCase):
    def test_sha1_mismatch_fails_closed(self):
        content = b"real bytes"
        payload = make_payload(content=content)
        payload["sha1"] = "1" * 40
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, content=content)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(self.porcelain(), "")

    def test_sha512_mismatch_fails_closed(self):
        content = b"real bytes"
        payload = make_payload(content=content)
        payload["sha512"] = "2" * 128
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, content=content)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(self.porcelain(), "")


class TestGateFailures(EngineTestCase):
    def test_check_failure_restores_tree(self):
        payload = make_payload()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, gates=FAILING_CHECK_GATES)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_GATE_FAILURE)
        self.assertEqual(self.porcelain(), "")
        self.assertFalse((self.repo / "docs" / "releases").exists() and
                          any((self.repo / "docs" / "releases").iterdir()))

    def test_build_failure_restores_tree(self):
        payload = make_payload()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, gates=FAILING_BUILD_GATES)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_GATE_FAILURE)
        self.assertEqual(self.porcelain(), "")


class TestDirtyTreeGuard(EngineTestCase):
    def _dirty(self, relative_path, extra_text="\nlocal uncommitted note\n"):
        path = self.repo / relative_path
        path.write_text(path.read_text(encoding="utf-8") + extra_text, encoding="utf-8")

    def _assert_refused_without_touching_anything(self, relative_path):
        before_porcelain = self.porcelain()
        before_content = (self.repo / relative_path).read_text(encoding="utf-8")
        payload = make_payload()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_DIRTY_TREE)
        self.assertIn(relative_path, str(ctx.exception))
        self.assertEqual(self.porcelain(), before_porcelain)
        self.assertEqual((self.repo / relative_path).read_text(encoding="utf-8"), before_content)
        self.assertFalse((self.repo / "docs" / "releases").exists())

    def test_dirty_readme_refuses_before_touching_anything(self):
        self._dirty("README.md")
        self._assert_refused_without_touching_anything("README.md")

    def test_dirty_pack_toml_refuses_before_touching_anything(self):
        self._dirty("pack.toml", '\n# stray local edit\n')
        self._assert_refused_without_touching_anything("pack.toml")

    def test_dirty_index_toml_refuses_before_touching_anything(self):
        self._dirty("index.toml", "\n# stray local edit\n")
        self._assert_refused_without_touching_anything("index.toml")

    def test_dirty_da_pin_refuses_before_touching_anything(self):
        self._dirty("mods/dynamic-atmosphere.pw.toml", "\n# stray local edit\n")
        self._assert_refused_without_touching_anything("mods/dynamic-atmosphere.pw.toml")

    def test_unrelated_dirty_file_does_not_trip_the_guard(self):
        # A pre-existing uncommitted change OUTSIDE the four restore-target
        # paths must not refuse the run -- the guard is scoped to exactly
        # the paths restore_repo() can touch.
        (self.repo / "CONTRIBUTING.md").write_text("unrelated\n", encoding="utf-8")
        payload = make_payload(category="patch")
        summary, exit_code, _ = self.run_engine(payload)
        self.assertEqual(exit_code, bump.EXIT_OK)

    def test_git_status_failure_refuses_without_touching_anything(self):
        # A non-zero exit from `git status` itself (not a repo, a
        # safe.directory "dubious ownership" refusal, a corrupt index, ...)
        # must not be silently read as "clean" just because stdout is empty.
        def failing_status(args, **kwargs):
            return subprocess.CompletedProcess(
                args, returncode=128, stdout="",
                stderr="fatal: detected dubious ownership in repository\n",
            )

        before_porcelain = self.porcelain()
        payload = make_payload()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, run_command=failing_status)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_CONFIG_ERROR)
        self.assertIn("git status", str(ctx.exception))
        self.assertIn("128", str(ctx.exception))
        self.assertIn("dubious ownership", str(ctx.exception))
        self.assertEqual(self.porcelain(), before_porcelain)
        self.assertFalse((self.repo / "docs" / "releases").exists())


class TestCategoryMapping(EngineTestCase):
    def test_patch_bumps_patch(self):
        # 0.19.0-alpha.1 -> 0.19.1-alpha.1 is itself a patch-level delta,
        # agreeing with the payload's own "patch" category (not merely
        # not contradicting a higher one -- see
        # test_payload_category_cannot_downgrade_the_semver_delta for
        # that case).
        payload = make_payload(version="0.19.1-alpha.1", category="patch")
        summary, exit_code, _ = self.run_engine(payload)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["new_pack_version"], "0.22.1")
        self.assertEqual(summary["mapping_rule"], "patch")
        self.assertFalse(summary["listing_review_required"])
        self.assertEqual(summary["listing_flagged_lines"], [])

    def test_minor_bumps_minor_resets_patch(self):
        payload = make_payload(category="minor")
        summary, exit_code, _ = self.run_engine(payload)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["new_pack_version"], "0.23.0")
        self.assertEqual(summary["mapping_rule"], "minor")
        self.assertTrue(summary["listing_review_required"])
        self.assertTrue(any(line["file"] == "README.md" for line in summary["listing_flagged_lines"]))
        self.assertTrue(any(line["file"] == ".modrinth/description.md" for line in summary["listing_flagged_lines"]))

    def test_breaking_bumps_minor_with_migration(self):
        release_body = (
            "# 0.20.0-alpha.1\n\n## Migration\n\nBack up your world. Run `/da migrate` once.\n\n"
            "## Changes\n\n- stuff\n\n# 0.19.0-alpha.1\n\n## Migration\n\nOLD, must not be picked up.\n"
        )
        body_file = self.repo.parent / "release-body.md"
        body_file.write_text(release_body, encoding="utf-8")
        self.addCleanup(body_file.unlink, missing_ok=True)

        payload = make_payload(version="0.20.0-alpha.1", category="breaking")
        summary, exit_code, _ = self.run_engine(payload, release_body_override=str(body_file))
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["new_pack_version"], "0.23.0")
        self.assertEqual(summary["mapping_rule"], "breaking_as_minor")
        self.assertTrue(summary["listing_review_required"])

        notes = (self.repo / summary["notes_path"]).read_text(encoding="utf-8")
        self.assertIn("BREAKING", notes)
        self.assertIn("Back up your world. Run `/da migrate` once.", notes)
        self.assertNotIn("OLD, must not be picked up.", notes)

    def test_atmo7_breaking_migration_notes_preserve_required_reset_instructions(self):
        release_body = (
            "# 0.20.0-alpha.1\n\n## Migration\n\n"
            "Saved dust, ender, exhaust, void, and slime gas is discarded on load.\n"
            "The violence config resets to defaults under voidGas.\n"
            "Client and server must use the same Dynamic Atmosphere version.\n\n"
            "## Changes\n\n- breaking config migration\n"
        )
        body_file = self.repo.parent / "release-body-atmo7.md"
        body_file.write_text(release_body, encoding="utf-8")
        self.addCleanup(body_file.unlink, missing_ok=True)

        payload = make_payload(version="0.20.0-alpha.1", category="breaking")
        summary, exit_code, _ = self.run_engine(payload, release_body_override=str(body_file))
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["new_pack_version"], "0.23.0")

        notes = (self.repo / summary["notes_path"]).read_text(encoding="utf-8")
        self.assertIn("Saved dust, ender, exhaust, void, and slime gas is discarded on load.", notes)
        self.assertIn("The violence config resets to defaults under voidGas.", notes)
        self.assertIn("Client and server must use the same Dynamic Atmosphere version.", notes)

    def test_breaking_without_migration_section_fails_closed(self):
        release_body = "# 0.20.0-alpha.1\n\n## Changes\n\n- stuff\n"
        body_file = self.repo.parent / "release-body-nomig.md"
        body_file.write_text(release_body, encoding="utf-8")
        self.addCleanup(body_file.unlink, missing_ok=True)

        payload = make_payload(version="0.20.0-alpha.1", category="breaking")
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, release_body_override=str(body_file))
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_MIGRATION_MISSING)
        self.assertEqual(self.porcelain(), "")

    def test_absent_category_defaults_to_minor_and_says_so(self):
        # Same core (0, 19, 0) as the pin, only the prerelease identifier
        # advances -- semver_delta_category correctly returns None for
        # this (no component delta to classify), so this exercises the
        # genuine "truly undetermined" sentinel path, not a case where
        # the version delta itself would have said "minor" anyway
        # (MINECRAFT-362 review, eighth round).
        payload = make_payload(version="0.19.0-alpha.2", category=None)
        summary, exit_code, _ = self.run_engine(payload)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["new_pack_version"], "0.23.0")
        self.assertEqual(summary["mapping_rule"], "minor_default_absent_category")
        notes = (self.repo / summary["notes_path"]).read_text(encoding="utf-8")
        self.assertIn("did not include a category", notes)

    def test_unrecognised_category_defaults_to_minor_and_says_so(self):
        payload = make_payload(version="0.19.0-alpha.2", category="wat")
        summary, exit_code, _ = self.run_engine(payload)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["new_pack_version"], "0.23.0")
        self.assertEqual(summary["mapping_rule"], "minor_default_unrecognised_category")
        notes = (self.repo / summary["notes_path"]).read_text(encoding="utf-8")
        self.assertIn("was not one of", notes)

    def test_payload_category_cannot_downgrade_the_semver_delta(self):
        # 0.19.0-alpha.1 -> 0.20.0-alpha.1 is a minor-component delta;
        # a payload category of "patch" must not downgrade that --
        # the more severe of the two wins (MINECRAFT-362 review, eighth
        # round: the DA dispatch path had no semver-delta derivation at
        # all before this, so AC4's breaking gate could never fire here
        # even when a major DA release's payload category was absent or
        # wrong).
        payload = make_payload(version="0.20.0-alpha.1", category="patch")
        summary, exit_code, _ = self.run_engine(payload)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["mapping_rule"], "minor")

    def test_major_semver_delta_alone_triggers_the_migration_gate(self):
        # No category in the payload at all -- the major-version delta
        # alone must still reach the "## Migration" required check.
        release_body = "# 1.0.0\n\n## Changes\n\n- stuff\n"
        body_file = self.repo.parent / "release-body-nomig-major.md"
        body_file.write_text(release_body, encoding="utf-8")
        self.addCleanup(body_file.unlink, missing_ok=True)

        payload = make_payload(version="1.0.0", category=None)
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, release_body_override=str(body_file))
        self.assertEqual(ctx.exception.outcome, "migration_missing")


class TestNotesFile(EngineTestCase):
    def test_notes_committed_staged_and_path_in_summary(self):
        payload = make_payload(version="0.19.1-alpha.1", category="patch")
        summary, exit_code, _ = self.run_engine(payload)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["notes_path"], "docs/releases/0.22.1.md")
        self.assertTrue((self.repo / summary["notes_path"]).exists())
        staged = _git(self.repo, "diff", "--cached", "--name-only").stdout.splitlines()
        self.assertIn(summary["notes_path"], staged)

    def test_preexisting_notes_file_fails_closed(self):
        releases_dir = self.repo / "docs" / "releases"
        releases_dir.mkdir(parents=True, exist_ok=True)
        existing = releases_dir / "0.22.1.md"
        existing.write_text("already published\n", encoding="utf-8")

        payload = make_payload(version="0.19.1-alpha.1", category="patch")
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_NOTES_EXISTS)
        self.assertEqual(existing.read_text(encoding="utf-8"), "already published\n")


class TestListingReviewRequired(EngineTestCase):
    def test_false_on_patch(self):
        summary, exit_code, _ = self.run_engine(make_payload(version="0.19.1-alpha.1", category="patch"))
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertFalse(summary["listing_review_required"])

    def test_true_on_minor_with_not_auto_updated_statement(self):
        summary, exit_code, _ = self.run_engine(make_payload(category="minor"))
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertTrue(summary["listing_review_required"])
        notes = (self.repo / summary["notes_path"]).read_text(encoding="utf-8")
        self.assertIn("NOT auto-updated", notes)
        self.assertIn("Vapor and Smoke behave", notes)


class TestVouchCheck(EngineTestCase):
    def test_control_200_da_404_is_not_vouched(self):
        content = b"x"
        with stub_vouch(200, 404):
            summary, exit_code, _ = bump.run_engine(
                self.repo, make_payload(category="patch", content=content),
                downloader=make_downloader(content), gate_commands=STUB_GATES,
            )
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["vouch"]["result"], "not_vouched")

    def test_both_404_is_inconclusive_control_failed(self):
        with stub_vouch(404, 404):
            content = b"y"
            summary, exit_code, _ = bump.run_engine(
                self.repo, make_payload(category="patch", content=content),
                downloader=make_downloader(content), gate_commands=STUB_GATES,
            )
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["vouch"]["result"], "inconclusive_control_failed")

    def test_control_429_is_inconclusive_not_404(self):
        with stub_vouch(429, 404):
            content = b"z"
            summary, exit_code, _ = bump.run_engine(
                self.repo, make_payload(category="patch", content=content),
                downloader=make_downloader(content), gate_commands=STUB_GATES,
            )
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["vouch"]["result"], "inconclusive")
        self.assertEqual(summary["vouch"]["control_status"], 429)

    def test_vouch_never_changes_exit_code(self):
        for control, da in ((200, 404), (404, 404), (200, 200), (429, 404)):
            with self.subTest(control=control, da=da):
                repo = build_fixture_repo()
                self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
                content = f"content-{control}-{da}".encode()
                with stub_vouch(control, da):
                    _, exit_code, _ = bump.run_engine(
                        repo, make_payload(category="patch", content=content),
                        downloader=make_downloader(content), gate_commands=STUB_GATES,
                    )
                self.assertEqual(exit_code, bump.EXIT_OK)

    def test_status_function_raising_is_inconclusive_not_propagated(self):
        def raising(sha512):
            raise OSError("simulated RemoteDisconnected")

        content = b"status-raises"
        with mock.patch.object(bump, "_version_file_status", side_effect=raising):
            summary, exit_code, _ = bump.run_engine(
                self.repo, make_payload(category="patch", content=content),
                downloader=make_downloader(content), gate_commands=STUB_GATES,
            )
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertTrue(summary["changed"])
        self.assertEqual(summary["vouch"]["result"], "inconclusive")
        self.assertIsNotNone(summary["vouch"]["error"])

    def test_missing_control_mod_file_is_inconclusive_not_propagated(self):
        # Commit the removal so it's part of HEAD: otherwise
        # assert_only_da_pin_changed_under_mods (an unrelated AC3 check)
        # would trip on the now-missing control file being a *diff* under
        # mods/, rather than exercising the vouch check's own catch-all.
        (self.repo / "mods" / "create.pw.toml").unlink()
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "remove control mod for this test")
        content = b"no-control-file"
        summary, exit_code, _ = bump.run_engine(
            self.repo, make_payload(category="patch", content=content),
            downloader=make_downloader(content), gate_commands=STUB_GATES,
        )
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["vouch"]["result"], "inconclusive")
        self.assertIsNotNone(summary["vouch"]["error"])

    def test_vouch_error_reaches_notes(self):
        def raising(sha512):
            raise OSError("simulated failure")

        content = b"error-in-notes"
        with mock.patch.object(bump, "_version_file_status", side_effect=raising):
            summary, exit_code, _ = bump.run_engine(
                self.repo, make_payload(category="patch", content=content),
                downloader=make_downloader(content), gate_commands=STUB_GATES,
            )
        self.assertEqual(exit_code, bump.EXIT_OK)
        notes = (self.repo / summary["notes_path"]).read_text(encoding="utf-8")
        self.assertIn("inconclusive", notes)
        self.assertIn("Error:", notes)


class TestInvalidPayload(EngineTestCase):
    def assert_rejected(self, payload):
        before_porcelain = self.porcelain()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INVALID_PAYLOAD)
        self.assertEqual(self.porcelain(), before_porcelain)

    def test_bad_sha1_length(self):
        payload = make_payload()
        payload["sha1"] = "abc123"
        self.assert_rejected(payload)

    def test_bad_sha512_length(self):
        payload = make_payload()
        payload["sha512"] = "abc123"
        self.assert_rejected(payload)

    def test_path_in_file_name(self):
        payload = make_payload()
        payload["file_name"] = "../evil.jar"
        self.assert_rejected(payload)

    def test_absolute_path_in_file_name(self):
        payload = make_payload()
        payload["file_name"] = "/etc/passwd.jar"
        self.assert_rejected(payload)

    def test_non_cdn_download_url(self):
        payload = make_payload()
        payload["download_url"] = "https://evil.example.com/dynamicatmosphere.jar"
        self.assert_rejected(payload)

    def test_non_https_download_url(self):
        payload = make_payload()
        payload["download_url"] = "http://cdn.modrinth.com/data/x/y.jar"
        self.assert_rejected(payload)

    def test_non_alphanumeric_modrinth_id(self):
        payload = make_payload()
        payload["modrinth_version_id"] = "abc def"
        self.assert_rejected(payload)

    def test_github_release_url_wrong_owner_rejected(self):
        payload = make_payload()
        payload["github_release_url"] = "https://github.com/someone-else/dynamic-atmosphere/releases/tag/v1.0.0"
        self.assert_rejected(payload)

    def test_github_release_url_wrong_repo_rejected(self):
        payload = make_payload()
        payload["github_release_url"] = "https://github.com/brooswit-minecraft/some-other-repo/releases/tag/v1.0.0"
        self.assert_rejected(payload)

    def test_version_with_quote_rejected(self):
        payload = make_payload()
        payload["version"] = '0.1.0"; evil = "x'
        self.assert_rejected(payload)

    def test_missing_field_rejected(self):
        payload = make_payload()
        del payload["sha1"]
        self.assert_rejected(payload)

    def test_non_object_payload_rejected(self):
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(["not", "an", "object"])
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INVALID_PAYLOAD)


class TestOnlyDaPinChangesUnderMods(EngineTestCase):
    def test_only_da_pin_file_changes(self):
        payload = make_payload(category="patch")
        summary, exit_code, _ = self.run_engine(payload)
        self.assertEqual(exit_code, bump.EXIT_OK)
        changed = _git(self.repo, "diff", "--cached", "--name-only").stdout.splitlines()
        mods_changed = [p for p in changed if p.startswith("mods/")]
        self.assertEqual(mods_changed, ["mods/dynamic-atmosphere.pw.toml"])
        create_pin_after = (self.repo / "mods" / "create.pw.toml").read_text(encoding="utf-8")
        create_pin_before = (
            'name = "Create"\n'
            'filename = "create-6.0.4.jar"\n'
            'side = "both"\n'
            "\n"
            "[download]\n"
            'url = "https://cdn.modrinth.com/data/LNytGWDc/versions/xyz/create-6.0.4.jar"\n'
            'hash-format = "sha512"\n'
            f'hash = "{CONTROL_HASH}"\n'
            "\n"
            "[update]\n"
            "[update.modrinth]\n"
            f'mod-id = "{CONTROL_MOD_ID}"\n'
            'version = "xyz"\n'
        )
        self.assertEqual(create_pin_after, create_pin_before)


class TestUnexpectedExceptionFailsClosed(EngineTestCase):
    def test_missing_gate_binary_restores_tree_and_reraises(self):
        payload = make_payload()
        with self.assertRaises(FileNotFoundError):
            self.run_engine(payload, gates=MISSING_BINARY_GATES)
        self.assertEqual(self.porcelain(), "")
        self.assertFalse((self.repo / "docs" / "releases").exists() and
                          any((self.repo / "docs" / "releases").iterdir()))


class TestSemverCompare(unittest.TestCase):
    def test_prerelease_older_than_release(self):
        self.assertEqual(bump.compare_semver("0.19.0-alpha.1", "0.19.0"), -1)

    def test_numeric_prerelease_identifiers_compare_numerically(self):
        self.assertEqual(bump.compare_semver("0.19.0-alpha.2", "0.19.0-alpha.10"), -1)

    def test_equal_versions(self):
        self.assertEqual(bump.compare_semver("0.19.0-alpha.1", "0.19.0-alpha.1"), 0)

    def test_core_version_dominates(self):
        self.assertEqual(bump.compare_semver("0.20.0-alpha.1", "0.19.0"), 1)


class TestMigrationExtraction(unittest.TestCase):
    def test_verbatim_extraction_from_cumulative_body(self):
        body = (
            "# 0.20.0\n\n## Migration\n\nLine one.\nLine two, verbatim.\n\n## Changes\n\n- x\n"
            "\n# 0.19.0\n\n## Migration\n\nold, must be ignored\n"
        )
        self.assertEqual(bump.extract_migration_section(body), "Line one.\nLine two, verbatim.")

    def test_missing_returns_none(self):
        body = "# 0.20.0\n\n## Changes\n\n- x\n"
        self.assertIsNone(bump.extract_migration_section(body))

    def test_hash_comment_inside_fenced_block_is_not_a_heading(self):
        body = (
            "# 0.20.0\n\n## Migration\n\nRun this:\n\n```bash\n"
            "# step 1: back up\ncp -r world world.bak\n"
            "# step 2: delete\nrm -rf config/da\n```\n\nThen restart.\n"
        )
        extracted = bump.extract_migration_section(body)
        self.assertIn("# step 1: back up", extracted)
        self.assertIn("# step 2: delete", extracted)
        self.assertIn("Then restart.", extracted)
        self.assertTrue(extracted.rstrip("\n").endswith("Then restart."))

    def test_h2_line_inside_fenced_block_is_not_a_heading(self):
        body = (
            "# 0.20.0\n\n## Migration\n\nEdit your config:\n\n~~~toml\n"
            "## this looks like a heading but is inside a fence\n"
            "value = 1\n~~~\n\nDone.\n\n## Changes\n\n- x\n"
        )
        extracted = bump.extract_migration_section(body)
        self.assertIn("## this looks like a heading but is inside a fence", extracted)
        self.assertIn("Done.", extracted)
        self.assertNotIn("- x", extracted)

    def test_crlf_body_extracts_same_as_lf_normalized(self):
        lf_body = (
            "category: breaking\n\n# 0.20.0-alpha.1\n\n## Migration\n\nBack up.\n"
        )
        crlf_body = lf_body.replace("\n", "\r\n")
        self.assertEqual(
            bump.extract_migration_section(crlf_body),
            bump.extract_migration_section(lf_body),
        )
        self.assertEqual(bump.extract_migration_section(crlf_body), "Back up.")

    def test_own_section_boundary_ignores_hash_inside_fence(self):
        body = (
            "# 0.20.0\n\n```\n# not the next release\n```\n\nSome text.\n\n"
            "# 0.19.0\n\n## Migration\n\nold, must be ignored\n"
        )
        own_section = bump.extract_own_release_section(body)
        self.assertIn("Some text.", own_section)
        self.assertNotIn("old, must be ignored", own_section)


class TestCliMain(EngineTestCase):
    def test_main_writes_summary_out_and_returns_exit_code(self):
        content = b"cli test content"
        payload = make_payload(version="0.19.1-alpha.1", category="patch", content=content)
        payload_path = self.repo.parent / "payload.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        self.addCleanup(payload_path.unlink, missing_ok=True)
        summary_out = self.repo.parent / "summary.json"
        self.addCleanup(summary_out.unlink, missing_ok=True)

        with mock.patch.object(bump, "download_and_verify", return_value=None), \
             mock.patch.object(bump, "default_gate_commands", return_value=STUB_GATES), \
             stub_vouch(200, 404):
            exit_code = bump.main([
                "--payload", str(payload_path),
                "--repo-root", str(self.repo),
                "--summary-out", str(summary_out),
            ])
        self.assertEqual(exit_code, bump.EXIT_OK)
        written = json.loads(summary_out.read_text(encoding="utf-8"))
        self.assertTrue(written["changed"])
        self.assertEqual(written["new_pack_version"], "0.22.1")

    def test_main_reports_unexpected_error_with_nonzero_exit(self):
        content = b"cli unexpected"
        payload = make_payload(version="0.19.1-alpha.1", category="patch", content=content)
        payload_path = self.repo.parent / "payload-unexpected.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        self.addCleanup(payload_path.unlink, missing_ok=True)
        summary_out = self.repo.parent / "summary-unexpected.json"
        self.addCleanup(summary_out.unlink, missing_ok=True)

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        with mock.patch.object(bump, "download_and_verify", side_effect=boom), \
             mock.patch.object(bump, "default_gate_commands", return_value=STUB_GATES), \
             stub_vouch(200, 404):
            exit_code = bump.main([
                "--payload", str(payload_path),
                "--repo-root", str(self.repo),
                "--summary-out", str(summary_out),
            ])
        self.assertEqual(exit_code, bump.EXIT_UNEXPECTED_ERROR)
        written = json.loads(summary_out.read_text(encoding="utf-8"))
        self.assertFalse(written["changed"])
        self.assertEqual(written["outcome"], "unexpected_error")
        self.assertIn("boom", written["error"])


class TestSharedContractFixture(unittest.TestCase):
    FIXTURE_PATH = FIXTURES_DIR / "dynamic-atmosphere-released.example.json"

    def load_fixture(self):
        return json.loads(self.FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_has_exactly_the_eight_agreed_keys(self):
        payload = self.load_fixture()
        self.assertEqual(set(payload.keys()), set(bump.REQUIRED_PAYLOAD_FIELDS))

    def test_fixture_validates_as_a_payload(self):
        payload = self.load_fixture()
        validated = bump.validate_payload(payload)
        self.assertEqual(validated["version"], payload["version"])
        self.assertEqual(validated["modrinth_version_id"], payload["modrinth_version_id"])
        self.assertEqual(validated["download_url"], payload["download_url"])


if __name__ == "__main__":
    unittest.main()


class TestModrinthMetadataGate(EngineTestCase):
    """AC3: the payload is verified against Modrinth's own version-file metadata, fail closed."""

    CONTENT = b"metadata gate jar"

    def attempt(self, payload, expect_code=None):
        before = self.porcelain()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, content=self.CONTENT)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(self.porcelain(), before)
        self.assertFalse((self.repo / "docs" / "releases").exists())
        return ctx.exception.message

    def test_matching_metadata_bumps(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        summary, exit_code, _ = self.run_engine(payload, content=self.CONTENT)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertTrue(summary["changed"])
        self.fetch_mock.assert_called_once_with(payload["modrinth_version_id"])

    def test_payload_sha512_differs_from_modrinth_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["files"][0]["hashes"]["sha512"] = "e" * 128
        self.assertIn("sha512", self.attempt(payload))

    def test_payload_sha1_differs_from_modrinth_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["files"][0]["hashes"]["sha1"] = "e" * 40
        self.assertIn("sha1", self.attempt(payload))

    def test_payload_and_download_agree_but_modrinth_differs_fails_closed(self):
        # A forged payload whose hashes match the (attacker-served) bytes it points at.
        payload = make_payload(category="patch", content=b"forged bytes")
        MODRINTH_METADATA[payload["modrinth_version_id"]] = modrinth_metadata_for(
            make_payload(category="patch", content=b"the real released jar",
                         modrinth_version_id="OTHERID99")
            | {"modrinth_version_id": payload["modrinth_version_id"]}
        )
        self.attempt_forged(payload)

    def attempt_forged(self, payload):
        before = self.porcelain()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, content=b"forged bytes")
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(self.porcelain(), before)

    def test_download_url_differs_from_modrinth_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["files"][0]["url"] = "https://evil.example/x.jar"
        self.assertIn("url", self.attempt(payload))

    def test_wrong_project_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["project_id"] = "SOMEOTHER"
        self.assertIn("project_id", self.attempt(payload))

    def test_forged_older_bytes_labelled_as_newer_version_fails_closed(self):
        # Valid, genuinely published OLDER release (hashes, url, file all real) presented
        # under a NEWER payload version string: version_number must equal payload.version.
        payload = make_payload(version="0.21.0-alpha.1", category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["version_number"] = "0.20.0-alpha.1"
        message = self.attempt(payload)
        self.assertIn("version_number", message)
        self.assertIn("0.20.0-alpha.1", message)

    def test_metadata_without_version_number_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        del MODRINTH_METADATA[payload["modrinth_version_id"]]["version_number"]
        self.assertIn("version_number", self.attempt(payload))

    def test_metadata_version_id_mismatch_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["id"] = "DIFFERENT"
        self.assertIn("does not match", self.attempt(payload))

    def test_no_file_with_payload_name_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["files"][0]["filename"] = "other.jar"
        self.assertIn("exactly one file", self.attempt(payload))

    def test_lookup_404_private_project_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        del MODRINTH_METADATA[payload["modrinth_version_id"]]
        self.assertIn("HTTP 404", self.attempt(payload))

    # MINECRAFT-362 review, seventeenth round: this dispatch path trusts
    # an external webhook's payload directly and, unlike the generalized
    # poll path, had NO (game_version, loader) compatibility check at
    # all before a write -- a dispatch for a build declaring the wrong
    # Minecraft version or loader would still pin and publish
    # successfully. These three tests prove the fix: wrong loader, wrong
    # game version, and metadata missing the fields entirely all fail
    # closed, same as every other metadata mismatch above.

    def test_wrong_loader_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["loaders"] = ["fabric"]
        message = self.attempt(payload)
        self.assertIn("loaders", message)
        self.assertIn("neoforge", message)

    def test_wrong_game_version_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["game_versions"] = ["1.20.1"]
        message = self.attempt(payload)
        self.assertIn("game_versions", message)
        self.assertIn("1.21.1", message)

    def test_metadata_missing_compatibility_fields_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        del MODRINTH_METADATA[payload["modrinth_version_id"]]["game_versions"]
        del MODRINTH_METADATA[payload["modrinth_version_id"]]["loaders"]
        message = self.attempt(payload)
        self.assertIn("game_versions", message)

    def test_lookup_transport_error_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        self.fetch_mock.side_effect = OSError("network down")
        self.assertIn("lookup failed", self.attempt(payload))

    def test_already_pinned_noop_makes_no_metadata_lookup(self):
        payload = make_payload(
            version="0.19.0-alpha.1", modrinth_version_id=OLD_VERSION_ID,
            content=b"x", sha1="0" * 40, sha512=OLD_DA_HASH,
        )
        summary, exit_code, _ = self.run_engine(payload)
        self.assertEqual(summary["outcome"], "already_pinned")
        self.fetch_mock.assert_not_called()


class TestDependencyConflict(EngineTestCase):
    """MINECRAFT-362 review, eighteenth round: this dispatch path never
    called modrinth_compat.find_dependency_conflicts at all, unlike the
    generalized poll path -- a DA candidate declaring an existing pinned
    project incompatible, or requiring an unverifiable project, would
    still pin and publish successfully (confirmed live by the reviewer
    with otherwise-valid identity/hashes/compatibility metadata).
    `create.pw.toml` (CONTROL_MOD_ID) is the one other mod this fixture
    repo already has pinned."""

    CONTENT = b"dependency conflict jar"

    def test_incompatible_with_a_currently_pinned_mod_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["dependencies"] = [
            {"project_id": CONTROL_MOD_ID, "dependency_type": "incompatible"},
        ]
        before = self.porcelain()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, content=self.CONTENT)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_CONFIG_ERROR)
        self.assertEqual(ctx.exception.outcome, "dependency_conflict")
        self.assertIn(CONTROL_MOD_ID, ctx.exception.message)
        self.assertEqual(self.porcelain(), before)

    def test_required_dependency_not_pinned_at_all_fails_closed(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["dependencies"] = [
            {"project_id": "SOME_UNRELATED_MOD", "dependency_type": "required"},
        ]
        before = self.porcelain()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(payload, content=self.CONTENT)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_CONFIG_ERROR)
        self.assertEqual(ctx.exception.outcome, "dependency_conflict")
        self.assertIn("SOME_UNRELATED_MOD", ctx.exception.message)
        self.assertEqual(self.porcelain(), before)

    def test_no_dependencies_field_at_all_still_bumps(self):
        # The honest-metadata default (modrinth_metadata_for above) has
        # no "dependencies" key at all -- must not crash or refuse.
        payload = make_payload(category="patch", content=self.CONTENT)
        summary, exit_code, _ = self.run_engine(payload, content=self.CONTENT)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertTrue(summary["changed"])

    def test_required_dependency_matching_the_pin_still_bumps(self):
        payload = make_payload(category="patch", content=self.CONTENT)
        MODRINTH_METADATA[payload["modrinth_version_id"]]["dependencies"] = [
            {"project_id": CONTROL_MOD_ID, "dependency_type": "required"},
        ]
        summary, exit_code, _ = self.run_engine(payload, content=self.CONTENT)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertTrue(summary["changed"])


class TestMetadataFetcherAuth(unittest.TestCase):
    def fetch(self, version_id, token):
        captured = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{}"

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            return Resp()

        with mock.patch.object(bump.urllib.request, "urlopen", fake_urlopen), \
                mock.patch.object(bump, "_MODRINTH_TOKEN", token):
            bump.fetch_modrinth_version_metadata(version_id)
        return captured

    def test_token_sent_only_when_set_and_version_id_is_quoted(self):
        captured = self.fetch("a/b", None)
        self.assertIsNone(captured["auth"])
        self.assertTrue(captured["url"].endswith("/v2/version/a%2Fb"))
        self.assertEqual(self.fetch("abc", "tok")["auth"], "tok")

    def test_main_removes_token_from_environment_before_gates_run(self):
        seen = {}

        def fake_run_engine(repo_root, payload_raw, **kwargs):
            seen["env_token"] = bump.os.environ.get("MODRINTH_TOKEN")
            seen["module_token"] = bump._MODRINTH_TOKEN
            return {"changed": False}, bump.EXIT_OK, "ok"

        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp) / "p.json"
            payload_path.write_text("{}", encoding="utf-8")
            with mock.patch.dict(bump.os.environ, {"MODRINTH_TOKEN": "secret-tok"}), \
                    mock.patch.object(bump, "run_engine", fake_run_engine), \
                    mock.patch.object(bump, "_MODRINTH_TOKEN", None):
                self.assertEqual(bump.main(["--payload", str(payload_path), "--repo-root", tmp]), bump.EXIT_OK)
                self.assertNotIn("MODRINTH_TOKEN", bump.os.environ)
        self.assertIsNone(seen["env_token"])
        self.assertEqual(seen["module_token"], "secret-tok")
