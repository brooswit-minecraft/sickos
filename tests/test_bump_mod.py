import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bump_mod.py"
SPEC = importlib.util.spec_from_file_location("bump_mod", MODULE_PATH)
bump_mod = importlib.util.module_from_spec(SPEC)
sys.modules["bump_mod"] = bump_mod
SPEC.loader.exec_module(bump_mod)

ELIGIBILITY_PATH = Path(__file__).parents[1] / "scripts" / "mod_eligibility.py"
ELIGIBILITY_SPEC = importlib.util.spec_from_file_location("mod_eligibility", ELIGIBILITY_PATH)
mod_eligibility = importlib.util.module_from_spec(ELIGIBILITY_SPEC)
sys.modules["mod_eligibility"] = mod_eligibility
ELIGIBILITY_SPEC.loader.exec_module(mod_eligibility)

COMPAT_PATH = Path(__file__).parents[1] / "scripts" / "modrinth_compat.py"
COMPAT_SPEC = importlib.util.spec_from_file_location("modrinth_compat", COMPAT_PATH)
modrinth_compat = importlib.util.module_from_spec(COMPAT_SPEC)
sys.modules["modrinth_compat"] = modrinth_compat
COMPAT_SPEC.loader.exec_module(modrinth_compat)

EligiblePin = mod_eligibility.EligiblePin
VersionCandidate = modrinth_compat.VersionCandidate

STUB_GATES = {"refresh": ["true"], "check": ["true"], "build": ["true"]}
FAILING_CHECK_GATES = {"refresh": ["true"], "check": ["false"], "build": ["true"]}

MOD_ID = "EXAMPLE1"
OTHER_MOD_ID = "OTHER1"
OLD_VERSION_ID = "OLDID1234"
OLD_HASH = "a" * 128


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(repo), check=check, capture_output=True, text=True)


def build_fixture_repo():
    repo = Path(tempfile.mkdtemp(prefix="bump-mod-fixture-"))
    (repo / "mods").mkdir()
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

    (repo / "mods" / "example-mod.pw.toml").write_text(
        'name = "Example Mod"\n'
        'filename = "examplemod-1.0.0.jar"\n'
        'side = "both"\n'
        "\n"
        "[download]\n"
        f'url = "https://cdn.modrinth.com/data/{MOD_ID}/versions/{OLD_VERSION_ID}/examplemod-1.0.0.jar"\n'
        'hash-format = "sha512"\n'
        f'hash = "{OLD_HASH}"\n'
        "\n"
        "[update]\n"
        "[update.modrinth]\n"
        f'mod-id = "{MOD_ID}"\n'
        f'version = "{OLD_VERSION_ID}"\n',
        encoding="utf-8",
    )
    (repo / "mods" / "other-mod.pw.toml").write_text(
        'name = "Other Mod"\n'
        'filename = "othermod-1.0.0.jar"\n'
        'side = "both"\n'
        "\n"
        "[download]\n"
        f'url = "https://cdn.modrinth.com/data/{OTHER_MOD_ID}/versions/OTHEROLD/othermod-1.0.0.jar"\n'
        'hash-format = "sha512"\n'
        f'hash = "{"d" * 128}"\n'
        "\n"
        "[update]\n"
        "[update.modrinth]\n"
        f'mod-id = "{OTHER_MOD_ID}"\n'
        'version = "OTHEROLD"\n',
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


def make_pin(**overrides):
    defaults = dict(
        pin_path="mods/example-mod.pw.toml", name="Example Mod", side="both",
        mod_id=MOD_ID, pinned_version_id=OLD_VERSION_ID,
    )
    defaults.update(overrides)
    return EligiblePin(**defaults)


def make_candidate(*, version_id="NEWID5678", version_number="1.1.0", content=b"new mod jar", **overrides):
    defaults = dict(
        version_id=version_id,
        version_number=version_number,
        file_name=f"examplemod-{version_number}.jar",
        sha1=hashlib.sha1(content).hexdigest(),
        sha512=hashlib.sha512(content).hexdigest(),
        download_url=f"https://cdn.modrinth.com/data/{MOD_ID}/versions/{version_id}/examplemod-{version_number}.jar",
    )
    defaults.update(overrides)
    return VersionCandidate(**defaults)


def metadata_for(candidate, *, project_id=MOD_ID):
    return {
        "id": candidate.version_id,
        "project_id": project_id,
        "version_number": candidate.version_number,
        "files": [{
            "filename": candidate.file_name,
            "url": candidate.download_url,
            "hashes": {"sha1": candidate.sha1, "sha512": candidate.sha512},
        }],
    }


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = build_fixture_repo()
        self.fetch_registry = {}
        self.fetch_mock = mock.Mock(side_effect=self._fetch)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def _fetch(self, version_id):
        if version_id not in self.fetch_registry:
            raise urllib.error.HTTPError("https://api.modrinth.com/v2/version/x", 404, "Not Found", {}, None)
        return self.fetch_registry[version_id]

    def register(self, version_id, metadata):
        self.fetch_registry[version_id] = metadata

    def porcelain(self):
        return _git(self.repo, "status", "--porcelain").stdout

    def run_engine(self, pin, candidate, *, content=b"new mod jar", category=None, gate_commands=None):
        return bump_mod.run_engine(
            self.repo, pin, candidate, category=category,
            downloader=make_downloader(content), run_command=subprocess.run,
            gate_commands=gate_commands or STUB_GATES, metadata_fetcher=self.fetch_mock,
        )


class TestAlreadyPinnedAndNoCandidate(EngineTestCase):
    def test_already_pinned_is_a_clean_noop(self):
        pin = make_pin()
        candidate = make_candidate(version_id=OLD_VERSION_ID, version_number="1.0.0")
        before = self.porcelain()
        summary, exit_code, _ = self.run_engine(pin, candidate)
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        self.assertFalse(summary["changed"])
        self.assertEqual(summary["outcome"], "already_pinned")
        self.assertEqual(self.porcelain(), before)
        self.fetch_mock.assert_not_called()

    def test_no_candidate_is_a_clean_noop(self):
        pin = make_pin()
        before = self.porcelain()
        summary, exit_code, _ = self.run_engine(pin, None)
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        self.assertEqual(summary["outcome"], "no_candidate")
        self.assertEqual(self.porcelain(), before)


class TestSuccessfulBump(EngineTestCase):
    def test_matching_metadata_bumps_and_bumps_pack_minor_by_default(self):
        content = b"new mod jar"
        candidate = make_candidate(content=content)
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()

        summary, exit_code, _ = self.run_engine(pin, candidate, content=content)
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        self.assertTrue(summary["changed"])
        self.assertEqual(summary["outcome"], "bumped")
        self.assertEqual(summary["new_pack_version"], "0.23.0")
        self.assertEqual(summary["mapping_rule"], "minor_default_absent_category")
        self.assertTrue(summary["listing_review_required"])

        pin_text = (self.repo / "mods" / "example-mod.pw.toml").read_text(encoding="utf-8")
        self.assertIn(candidate.version_id, pin_text)
        self.assertIn(hashlib.sha512(content).hexdigest(), pin_text)

        pack_text = (self.repo / "pack.toml").read_text(encoding="utf-8")
        self.assertIn('version = "0.23.0"', pack_text)

        notes_path = self.repo / summary["notes_path"]
        self.assertTrue(notes_path.exists())
        self.assertIn("Example Mod", notes_path.read_text(encoding="utf-8"))

        staged = _git(self.repo, "diff", "--cached", "--name-only").stdout.splitlines()
        self.assertIn("mods/example-mod.pw.toml", staged)
        self.assertIn("pack.toml", staged)

    def test_breaking_category_bumps_minor_and_flags_breaking_in_notes(self):
        content = b"breaking jar"
        candidate = make_candidate(content=content, version_number="2.0.0")
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()

        summary, exit_code, _ = self.run_engine(pin, candidate, content=content, category="breaking")
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        self.assertEqual(summary["mapping_rule"], "breaking_as_minor")
        notes_path = self.repo / summary["notes_path"]
        self.assertIn("BREAKING", notes_path.read_text(encoding="utf-8"))

    def test_unparsable_previous_metadata_fails_closed_not_open(self):
        # The previous-version lookup fails (404) -- e.g. the currently
        # pinned version was unpublished on Modrinth. The engine must fail
        # closed, not silently skip the no-downgrade check and bump
        # anyway: a skipped check here would let a stale/attacker-chosen
        # "highest compatible" candidate downgrade the pin undetected.
        content = b"jar contents"
        candidate = make_candidate(content=content)
        self.register(candidate.version_id, metadata_for(candidate))
        # Deliberately do NOT register OLD_VERSION_ID -> fetch raises 404.
        pin = make_pin()
        before = self.porcelain()
        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content)
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(self.porcelain(), before)


class TestDowngradeAndAnomaly(EngineTestCase):
    def test_downgrade_refused_fails_closed(self):
        content = b"older jar"
        candidate = make_candidate(content=content, version_number="0.5.0")
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()
        before = self.porcelain()
        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content)
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_DOWNGRADE_REFUSED)
        self.assertEqual(self.porcelain(), before)

    def test_same_version_number_refused_as_downgrade(self):
        content = b"same version jar"
        candidate = make_candidate(content=content, version_number="1.0.0")
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()
        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content)
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_DOWNGRADE_REFUSED)


class TestMetadataGate(EngineTestCase):
    def test_wrong_project_id_fails_closed(self):
        content = b"wrong project jar"
        candidate = make_candidate(content=content)
        self.register(candidate.version_id, metadata_for(candidate, project_id="SOMETHING_ELSE"))
        pin = make_pin()
        before = self.porcelain()
        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content)
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_INTEGRITY_FAILURE)
        self.assertIn("project_id", ctx.exception.message)
        self.assertEqual(self.porcelain(), before)

    def test_forged_bytes_fail_closed(self):
        candidate = make_candidate(content=b"honest bytes")
        self.register(candidate.version_id, metadata_for(candidate))
        pin = make_pin()
        before = self.porcelain()
        with self.assertRaises(bump_mod.EngineError) as ctx:
            # downloader actually serves different bytes than the metadata's hashes
            self.run_engine(pin, candidate, content=b"forged bytes")
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(self.porcelain(), before)

    def test_404_lookup_fails_closed(self):
        candidate = make_candidate(content=b"x")
        # Deliberately do not register the candidate's version id.
        pin = make_pin()
        before = self.porcelain()
        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=b"x")
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_INTEGRITY_FAILURE)
        self.assertIn("HTTP 404", ctx.exception.message)
        self.assertEqual(self.porcelain(), before)


class TestGateFailureRestoresRepo(EngineTestCase):
    def test_failing_check_gate_restores_repo(self):
        content = b"gate failure jar"
        candidate = make_candidate(content=content)
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()
        before = self.porcelain()
        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content, gate_commands=FAILING_CHECK_GATES)
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_GATE_FAILURE)
        self.assertEqual(self.porcelain(), before)
        self.assertFalse((self.repo / "docs" / "releases").exists() and
                          any((self.repo / "docs" / "releases").iterdir()))


class TestDirtyTreeRefusal(EngineTestCase):
    def test_dirty_pin_file_refuses_to_start(self):
        (self.repo / "mods" / "example-mod.pw.toml").write_text("dirty\n", encoding="utf-8")
        candidate = make_candidate(content=b"x")
        pin = make_pin()
        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=b"x")
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_DIRTY_TREE)


class TestDependencyConflict(EngineTestCase):
    def test_incompatible_with_a_currently_pinned_mod_fails_closed(self):
        content = b"conflicting jar"
        candidate = make_candidate(
            content=content,
            dependencies=({"project_id": OTHER_MOD_ID, "dependency_type": "incompatible"},),
        )
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()

        before = self.porcelain()
        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content)
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_CONFIG_ERROR)
        self.assertEqual(ctx.exception.outcome, "dependency_conflict")
        self.assertIn(OTHER_MOD_ID, ctx.exception.message)
        self.assertEqual(self.porcelain(), before)

    def test_incompatible_with_a_mod_not_pinned_still_bumps(self):
        content = b"harmless jar"
        candidate = make_candidate(
            content=content,
            dependencies=({"project_id": "SOME_UNRELATED_MOD", "dependency_type": "incompatible"},),
        )
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()

        summary, exit_code, _ = self.run_engine(pin, candidate, content=content)
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        self.assertTrue(summary["changed"])

    def test_required_dependency_not_pinned_does_not_block_the_bump(self):
        content = b"needs something jar"
        candidate = make_candidate(
            content=content,
            dependencies=({"project_id": "SOME_UNRELATED_MOD", "dependency_type": "required"},),
        )
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()

        summary, exit_code, _ = self.run_engine(pin, candidate, content=content)
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        self.assertTrue(summary["changed"])


if __name__ == "__main__":
    unittest.main()
