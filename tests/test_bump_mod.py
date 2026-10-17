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


def metadata_for(candidate, *, project_id=MOD_ID, version_type="release"):
    return {
        "id": candidate.version_id,
        "project_id": project_id,
        "version_number": candidate.version_number,
        "version_type": version_type,
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

    def run_engine(self, pin, candidate, *, content=b"new mod jar", category=None, gate_commands=None,
                   allow_breaking=False, migration_text=None, game_version=None, loader=None):
        return bump_mod.run_engine(
            self.repo, pin, candidate, category=category,
            downloader=make_downloader(content), run_command=subprocess.run,
            gate_commands=gate_commands or STUB_GATES, metadata_fetcher=self.fetch_mock,
            allow_breaking=allow_breaking, migration_text=migration_text,
            game_version=game_version, loader=loader,
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
    def test_matching_metadata_bumps_and_classifies_by_semver_delta(self):
        # 1.0.0 -> 1.1.0 is a minor-component change, so this is real
        # "minor" (not the old "minor_default_absent_category" sentinel)
        # even though no category/changelog was supplied -- the semver
        # delta is now the determining signal (MINECRAFT-362 review,
        # sixth round).
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
        self.assertEqual(summary["mapping_rule"], "minor")
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

    def test_patch_level_semver_delta_with_a_real_fix_only_changelog(self):
        # The exact shape of a real Modrinth changelog that never
        # matches derive_category_from_changelog's heading-only regex --
        # classified correctly anyway, from the version numbers alone.
        content = b"patch jar"
        candidate = make_candidate(content=content, version_number="1.4.3")
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.4.2"})
        pin = make_pin()

        summary, exit_code, _ = self.run_engine(pin, candidate, content=content)
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        self.assertEqual(summary["mapping_rule"], "patch")

    def test_major_semver_delta_alone_triggers_the_breaking_gate(self):
        # No category/changelog supplied at all -- the major-version
        # delta alone must still reach the allow_breaking fail-closed
        # gate; a breaking change announced only via version number,
        # not a changelog heading, must not slip through as "minor".
        content = b"breaking jar"
        candidate = make_candidate(content=content, version_number="2.0.0")
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.4.2"})
        pin = make_pin()

        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content)
        self.assertEqual(ctx.exception.outcome, "breaking_change_requires_human_review")

    def test_game_version_and_loader_threading_actually_reaches_the_category_gate(self):
        # MINECRAFT-362 review, eighth round: a prior round's fix
        # (semver_delta_category accepting game_version/loader) was
        # covered only by direct unit tests of that function -- nothing
        # asserted run_engine actually PASSES them through. This is the
        # real "YungsApi-1.21.1-NeoForge-5.1.9.jar" scheme: without both
        # game_version AND loader reaching semver_delta_category, this
        # major bump would classify as None and auto-publish as minor.
        content = b"yungs-style breaking jar"
        candidate = make_candidate(
            content=content, version_number="1.21.1-NeoForge-6.0.0",
            file_name="YungsApi-1.21.1-NeoForge-6.0.0.jar",
        )
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {
            "id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.21.1-NeoForge-5.1.9",
        })
        pin = make_pin()

        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content, game_version="1.21.1", loader="neoforge")
        self.assertEqual(ctx.exception.outcome, "breaking_change_requires_human_review")

    def test_changelog_category_cannot_downgrade_a_major_semver_delta(self):
        # category="patch" (e.g. misread from a changelog) must not
        # suppress the breaking classification a major version bump
        # itself declares -- escalation is one-directional.
        content = b"breaking jar undersold as a fix"
        candidate = make_candidate(content=content, version_number="3.0.0")
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "2.5.1"})
        pin = make_pin()

        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content, category="patch")
        self.assertEqual(ctx.exception.outcome, "breaking_change_requires_human_review")

    def test_changelog_category_can_escalate_a_minor_semver_delta(self):
        # 1.0.0 -> 1.1.0 is only a minor-component delta, but an explicit
        # category="breaking" (e.g. a real changelog heading) must still
        # escalate it to breaking.
        content = b"minor-looking but actually breaking"
        candidate = make_candidate(content=content, version_number="1.1.0")
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()

        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content, category="breaking")
        self.assertEqual(ctx.exception.outcome, "breaking_change_requires_human_review")

    def test_breaking_category_fails_closed_without_allow_breaking(self):
        # AC4 requires real migration/reset instructions on a breaking
        # release; this generic engine cannot generate them, so it must
        # refuse rather than auto-publish a placeholder in their place
        # (MINECRAFT-362 review).
        content = b"breaking jar"
        candidate = make_candidate(content=content, version_number="2.0.0")
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()
        before = self.porcelain()

        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content, category="breaking")
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_MIGRATION_MISSING)
        self.assertEqual(ctx.exception.outcome, "breaking_change_requires_human_review")
        self.assertEqual(self.porcelain(), before)

    def test_breaking_category_bumps_minor_and_flags_breaking_in_notes_with_allow_breaking(self):
        content = b"breaking jar"
        candidate = make_candidate(content=content, version_number="2.0.0", changelog="## Breaking\n- renamed a key")
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()

        summary, exit_code, _ = self.run_engine(
            pin, candidate, content=content, category="breaking", allow_breaking=True,
            migration_text="Back up your world before upgrading; delete config/example-mod.toml "
                            "to regenerate it with the new key.",
        )
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        self.assertEqual(summary["mapping_rule"], "breaking_as_minor")
        notes_text = (self.repo / summary["notes_path"]).read_text(encoding="utf-8")
        self.assertIn("BREAKING", notes_text)
        self.assertIn("Back up your world", notes_text)
        self.assertIn("renamed a key", notes_text)

    def test_allow_breaking_without_migration_text_is_refused(self):
        content = b"breaking jar"
        candidate = make_candidate(content=content, version_number="2.0.0")
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()
        before = self.porcelain()

        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content, category="breaking", allow_breaking=True)
        self.assertEqual(ctx.exception.outcome, "migration_text_required")
        self.assertEqual(self.porcelain(), before)

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

    def test_previous_pin_metadata_wrong_project_id_fails_closed(self):
        # MINECRAFT-362 review, seventh round: the PREVIOUS version's
        # metadata (fetched from pin.pinned_version_id for the
        # no-downgrade check) was never identity-checked, only the
        # candidate's was -- so a pin whose recorded version id resolves
        # to a different project's metadata would silently trust that
        # unrelated project's version_number for both the downgrade
        # check and the category determination.
        content = b"jar contents"
        candidate = make_candidate(content=content)
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": "SOME_OTHER_PROJECT", "version_number": "1.0.0"})
        pin = make_pin()
        before = self.porcelain()

        with self.assertRaises(bump_mod.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content)
        self.assertEqual(ctx.exception.exit_code, bump_mod.EXIT_INTEGRITY_FAILURE)
        self.assertIn("SOME_OTHER_PROJECT", ctx.exception.message)
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


def _find_packwiz():
    """Same resolution order as the Makefile's own `PACKWIZ ?=` line: a
    `packwiz` already on PATH, else this repo's own `./bin/packwiz` (what
    `make tools` installs). Returns None if neither exists, so the real
    dry-run test below can skip cleanly rather than fail in an
    environment that has not run `make tools`."""
    import shutil as _shutil
    on_path = _shutil.which("packwiz")
    if on_path:
        return on_path
    repo_bin = Path(__file__).parents[1] / "bin" / "packwiz"
    if repo_bin.exists():
        return str(repo_bin)
    return None


class TestRealPackwizDryRun(EngineTestCase):
    """MINECRAFT-362 review, fourteenth round: the one test in this file
    that calls run_engine with REAL default_gate_commands-shaped commands
    (actual packwiz refresh/export, actual `git diff --quiet` against the
    index) rather than STUB_GATES' `true`/`false`. 382 passing tests
    never once evaluated the real `make check` predicate, so a
    gate-ordering regression that made every real bump fail was
    invisible to the whole suite -- this is the dry-run coverage the
    review asked for to close that hole. Skips (not fails) where packwiz
    is not installed, so environments that have not run `make tools`
    still get the rest of this file's coverage."""

    PACKWIZ = _find_packwiz()

    def setUp(self):
        super().setUp()
        if self.PACKWIZ is None:
            self.skipTest("packwiz not installed; run `make tools` to enable this test")

    def _real_gate_commands(self):
        # "refresh" and "check" are the real packwiz binary plus the real
        # `make check` predicate -- exactly the two gates the fourteenth
        # round's ordering defect broke. "build" is deliberately left as
        # a no-op ("true"): `packwiz modrinth export` tries to fetch each
        # pin's own download URL (a real network call to cdn.modrinth.com
        # for whatever this fixture's fake pins point at), which this
        # dry-run test has no need to depend on -- the ordering defect
        # under test is entirely in refresh/check, same as DA's own gate
        # sequence treats build as independent of staging.
        return {
            "refresh": [self.PACKWIZ, "refresh"],
            "check": ["bash", "-c",
                      f'"{self.PACKWIZ}" refresh >/dev/null && '
                      'git diff --quiet -- pack.toml index.toml mods'],
            "build": ["true"],
        }

    def test_real_gates_produce_a_successful_bump(self):
        content = b"real packwiz dry-run jar"
        candidate = make_candidate(content=content)
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()
        summary, exit_code, _ = self.run_engine(
            pin, candidate, content=content, gate_commands=self._real_gate_commands(),
        )
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        self.assertEqual(summary["outcome"], "bumped")


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


class TestGateOrderingAgainstTheRealCheckPredicate(EngineTestCase):
    """MINECRAFT-362 review, fourteenth round: STUB_GATES above uses
    `true` for every gate, which passes regardless of staging order --
    382 passing tests and 6 green CI checks never once evaluated
    `make check`'s actual predicate, so a gate-ordering regression that
    made every real bump fail was invisible to the whole suite. This
    class reproduces that predicate directly with plain git (no
    packwiz/make needed, so it is as cheap and CI-portable as STUB_GATES
    itself): `make check`'s rule is literally `git diff --quiet --
    pack.toml index.toml mods` run against the INDEX, not HEAD, so it
    only passes if this run's own writes to those paths are already
    staged -- exactly the ordering run_engine must get right."""

    REALISTIC_CHECK_GATES = {
        "refresh": ["true"],
        "check": ["git", "diff", "--quiet", "--", "pack.toml", "index.toml", "mods"],
        "build": ["true"],
    }

    def test_check_gate_passes_because_staging_happens_before_it_runs(self):
        content = b"realistic gate jar"
        candidate = make_candidate(content=content)
        self.register(candidate.version_id, metadata_for(candidate))
        self.register(OLD_VERSION_ID, {"id": OLD_VERSION_ID, "project_id": MOD_ID, "version_number": "1.0.0"})
        pin = make_pin()
        summary, exit_code, _ = self.run_engine(
            pin, candidate, content=content, gate_commands=self.REALISTIC_CHECK_GATES,
        )
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        self.assertEqual(summary["outcome"], "bumped")


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


class TestFenced(unittest.TestCase):
    def test_embedded_triple_backtick_is_defanged(self):
        rendered = bump_mod._fenced("before\n```\nafter")
        # The fence markers around the WHOLE block are real; only the
        # one embedded inside the content must be defanged so it cannot
        # prematurely close that outer fence.
        self.assertTrue(rendered.startswith("```\n"))
        self.assertTrue(rendered.endswith("\n```"))
        inner = rendered[4:-4]
        self.assertNotIn("```", inner)
        self.assertIn("before", inner)
        self.assertIn("after", inner)

    def test_long_text_is_truncated(self):
        rendered = bump_mod._fenced("x" * (bump_mod.MAX_CHANGELOG_CHARS + 500))
        self.assertIn("... (truncated)", rendered)
        self.assertLess(len(rendered), bump_mod.MAX_CHANGELOG_CHARS + 100)


class TestCliCommitBump(unittest.TestCase):
    """MINECRAFT-362 review, fifth round: cli_commit_bump previously ran
    a bare `git commit` with no pathspec, so anything else a human had
    already staged in their working checkout would be swept into this
    release commit too. Real git repo, no mocking -- what's under test
    is git's own pathspec'd-commit behaviour, not this module's logic."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="cli-commit-bump-"))
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        (self.repo / "pack.toml").write_text("version = \"0.22.0\"\n", encoding="utf-8")
        (self.repo / "other.txt").write_text("pre-existing\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "fixture")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_unrelated_staged_file_is_not_swept_into_the_commit(self):
        (self.repo / "pack.toml").write_text("version = \"0.23.0\"\n", encoding="utf-8")
        (self.repo / "other.txt").write_text("an unrelated in-progress edit\n", encoding="utf-8")
        _git(self.repo, "add", "-A")  # operator's own working checkout already has both staged

        bump_mod.cli_commit_bump(self.repo, "fix: bump\n\nRelease-Only: true\n", ("pack.toml",))

        committed = _git(self.repo, "diff", "--name-only", "HEAD~1", "HEAD").stdout.split()
        self.assertEqual(committed, ["pack.toml"])
        still_staged = _git(self.repo, "diff", "--cached", "--name-only").stdout.split()
        self.assertEqual(still_staged, ["other.txt"])


def cli_version_metadata(version_id, *, game_versions=("1.21.1",), loaders=("neoforge",),
                          content=b"cli jar contents"):
    return {
        "id": version_id, "project_id": MOD_ID, "version_number": "2.0.0",
        "version_type": "release", "changelog": "",
        "game_versions": list(game_versions), "loaders": list(loaders),
        "dependencies": [],
        "files": [{
            "primary": True, "filename": "examplemod-2.0.0.jar",
            "hashes": {"sha1": hashlib.sha1(content).hexdigest(), "sha512": hashlib.sha512(content).hexdigest()},
            "url": f"https://cdn.modrinth.com/data/{MOD_ID}/versions/{version_id}/examplemod-2.0.0.jar",
        }],
    }


class TestCliMain(unittest.TestCase):
    """Exercises bump_mod.py's CLI wiring itself (MINECRAFT-362 review,
    third and fourth rounds: a human must have a real runnable command
    for the allow_breaking path, and that command must build its pin and
    candidate from the pin on disk plus real Modrinth metadata, not from
    hand-transcribed flags). run_engine is mocked throughout for the
    argument-wiring tests -- the engine logic itself is already covered
    above; what's under test here is CLI-level argument validation,
    pin/candidate construction, and the commit step it now performs."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="bump-mod-cli-"))
        self.on_disk_pin = EligiblePin(
            pin_path="mods/example-mod.pw.toml", name="Example Mod", side="client",
            mod_id=MOD_ID, pinned_version_id=OLD_VERSION_ID,
        )
        self._discover_patch = mock.patch.object(
            bump_mod.mod_eligibility, "discover_eligible_pins", return_value=([self.on_disk_pin], []),
        )
        self._targets_patch = mock.patch.object(
            bump_mod.mod_eligibility, "read_pack_targets", return_value=("1.21.1", "neoforge"),
        )
        self._discover_patch.start()
        self._targets_patch.start()
        self.addCleanup(self._discover_patch.stop)
        self.addCleanup(self._targets_patch.stop)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _base_args(self, extra=()):
        return [
            "--repo-root", str(self.tmpdir),
            "--pin-path", "mods/example-mod.pw.toml", "--mod-id", MOD_ID, "--version-id", "NEWID",
            *extra,
        ]

    def test_breaking_category_without_allow_breaking_is_refused_before_touching_the_engine(self):
        with mock.patch.object(bump_mod, "run_engine") as mock_run_engine:
            exit_code = bump_mod.cli_main(self._base_args(["--category", "breaking"]))
        self.assertEqual(exit_code, 2)
        mock_run_engine.assert_not_called()

    def test_allow_breaking_without_migration_text_file_is_refused(self):
        with mock.patch.object(bump_mod, "run_engine") as mock_run_engine:
            exit_code = bump_mod.cli_main(
                self._base_args(["--category", "breaking", "--allow-breaking"])
            )
        self.assertEqual(exit_code, 2)
        mock_run_engine.assert_not_called()

    def test_side_defaults_from_the_pin_on_disk(self):
        fake_summary = {"changed": False, "outcome": "no_candidate"}
        with mock.patch.object(bump_mod, "fetch_version_metadata", return_value=cli_version_metadata("NEWID")):
            with mock.patch.object(bump_mod, "run_engine", return_value=(fake_summary, bump_mod.EXIT_OK, "ok")) as mock_run_engine:
                exit_code = bump_mod.cli_main(self._base_args())
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        pin_arg = mock_run_engine.call_args[0][1]
        self.assertEqual(pin_arg.side, "client")  # from self.on_disk_pin, NOT the old "both" default

    def test_explicit_side_disagreeing_with_disk_is_refused(self):
        with mock.patch.object(bump_mod, "fetch_version_metadata", return_value=cli_version_metadata("NEWID")):
            with mock.patch.object(bump_mod, "run_engine") as mock_run_engine:
                with self.assertRaises(SystemExit):
                    bump_mod.cli_main(self._base_args(["--side", "server"]))
        mock_run_engine.assert_not_called()

    def test_mod_id_defaults_from_the_pin_on_disk(self):
        # MINECRAFT-362 review, fifth round: --mod-id was the one
        # pin-identity field left unguarded, defaulting to whatever the
        # operator typed with no cross-check against the pin on disk --
        # the exact field run_engine's own identity gate trusts as
        # ground truth.
        fake_summary = {"changed": False, "outcome": "no_candidate"}
        args = [
            "--repo-root", str(self.tmpdir),
            "--pin-path", "mods/example-mod.pw.toml", "--version-id", "NEWID",
        ]
        with mock.patch.object(bump_mod, "fetch_version_metadata", return_value=cli_version_metadata("NEWID")):
            with mock.patch.object(bump_mod, "run_engine", return_value=(fake_summary, bump_mod.EXIT_OK, "ok")) as mock_run_engine:
                exit_code = bump_mod.cli_main(args)
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        pin_arg = mock_run_engine.call_args[0][1]
        self.assertEqual(pin_arg.mod_id, MOD_ID)  # from self.on_disk_pin

    def test_explicit_mod_id_disagreeing_with_disk_is_refused(self):
        with mock.patch.object(bump_mod, "fetch_version_metadata", return_value=cli_version_metadata("NEWID")):
            with mock.patch.object(bump_mod, "run_engine") as mock_run_engine:
                with self.assertRaises(SystemExit):
                    bump_mod.cli_main(self._base_args(["--mod-id", "uCdwusMi"]))
        mock_run_engine.assert_not_called()

    def test_incompatible_candidate_is_refused_before_touching_the_engine(self):
        # Candidate declares fabric/1.20.1, not this pack's neoforge/1.21.1
        # -- the compatibility check resolve_latest_compatible_version
        # would otherwise have done is re-done explicitly here, since the
        # CLI is given a version id directly and never calls that resolver.
        with mock.patch.object(
            bump_mod, "fetch_version_metadata",
            return_value=cli_version_metadata("NEWID", game_versions=("1.20.1",), loaders=("fabric",)),
        ):
            with mock.patch.object(bump_mod, "run_engine") as mock_run_engine:
                with self.assertRaises(SystemExit):
                    bump_mod.cli_main(self._base_args())
        mock_run_engine.assert_not_called()

    def test_candidate_carries_real_compatibility_and_dependency_data(self):
        metadata = cli_version_metadata("NEWID")
        metadata["dependencies"] = [{"project_id": "SOME_OTHER", "dependency_type": "incompatible"}]
        fake_summary = {"changed": False, "outcome": "no_candidate"}
        with mock.patch.object(bump_mod, "fetch_version_metadata", return_value=metadata):
            with mock.patch.object(bump_mod, "run_engine", return_value=(fake_summary, bump_mod.EXIT_OK, "ok")) as mock_run_engine:
                bump_mod.cli_main(self._base_args())
        candidate_arg = mock_run_engine.call_args[0][2]
        self.assertEqual(candidate_arg.game_versions, ("1.21.1",))
        self.assertEqual(candidate_arg.loaders, ("neoforge",))
        self.assertEqual(len(candidate_arg.dependencies), 1)

    def test_reads_migration_text_and_changelog_files_and_commits_with_release_only_trailer(self):
        migration_path = self.tmpdir / "migration.md"
        migration_path.write_text("Back up your world first.", encoding="utf-8")
        changelog_path = self.tmpdir / "changelog.md"
        changelog_path.write_text("## Breaking\n- renamed a key", encoding="utf-8")

        fake_summary = {
            "changed": True, "outcome": "bumped", "notes_path": "docs/releases/0.23.0.md",
            "mapping_rule": "breaking_as_minor", "new_pack_version": "0.23.0",
            "candidate_version_id": "NEWID", "candidate_version_number": "2.0.0",
        }
        with mock.patch.object(bump_mod, "fetch_version_metadata", return_value=cli_version_metadata("NEWID")):
            with mock.patch.object(bump_mod, "run_engine", return_value=(fake_summary, bump_mod.EXIT_OK, "ok")) as mock_run_engine:
                with mock.patch.object(bump_mod, "cli_commit_bump", return_value="deadbeef" * 5) as mock_commit:
                    exit_code = bump_mod.cli_main(self._base_args([
                        "--category", "breaking", "--allow-breaking",
                        "--migration-text-file", str(migration_path),
                        "--changelog-file", str(changelog_path),
                    ]))
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        mock_run_engine.assert_called_once()
        _, kwargs = mock_run_engine.call_args
        self.assertEqual(kwargs["allow_breaking"], True)
        self.assertEqual(kwargs["migration_text"], "Back up your world first.")
        self.assertEqual(kwargs["category"], "breaking")
        candidate_arg = mock_run_engine.call_args[0][2]
        self.assertEqual(candidate_arg.changelog, "## Breaking\n- renamed a key")

        mock_commit.assert_called_once()
        committed_message = mock_commit.call_args[0][1]
        self.assertIn("Release-Only: true", committed_message)
        self.assertIn("Mod-Auto-Bump:", committed_message)
        self.assertFalse(bump_mod.server_update_policy.should_deploy(committed_message))

    def test_no_commit_guidance_names_literal_paths_not_an_unsafe_substitution(self):
        # MINECRAFT-362 review, seventh round: a prior round's --no-commit
        # guidance told the operator to pass $(git diff --cached
        # --name-only) as the pathspec, which expands to EVERY staged
        # path -- including anything else they already had staged --
        # reintroducing the exact whole-index-sweep bug the explicit
        # pathspec was supposed to fix. The printed remedy must name the
        # four intended paths literally instead.
        fake_summary = {
            "changed": True, "outcome": "bumped", "notes_path": "docs/releases/0.23.0.md",
            "mapping_rule": "minor", "new_pack_version": "0.23.0",
            "candidate_version_id": "NEWID", "candidate_version_number": "1.1.0",
        }
        with mock.patch.object(bump_mod, "fetch_version_metadata", return_value=cli_version_metadata("NEWID")):
            with mock.patch.object(bump_mod, "run_engine", return_value=(fake_summary, bump_mod.EXIT_OK, "ok")):
                with mock.patch.object(bump_mod, "cli_commit_bump") as mock_commit:
                    with mock.patch("sys.stdout") as mock_stdout:
                        exit_code = bump_mod.cli_main(self._base_args(["--no-commit"]))
        self.assertEqual(exit_code, bump_mod.EXIT_OK)
        mock_commit.assert_not_called()
        printed = "".join(call.args[0] for call in mock_stdout.write.call_args_list if call.args)
        # The actual commit command recommended must use the four literal
        # paths, not the unsafe substitution (which may still be
        # mentioned elsewhere as a "don't do this" warning).
        self.assertIn(
            "git commit -F <message-file> -- pack.toml index.toml "
            "mods/example-mod.pw.toml docs/releases/0.23.0.md",
            printed,
        )
        self.assertNotIn("-- $(git diff --cached --name-only)`", printed)

    def test_migration_text_without_breaking_category_warns_not_silently_discards(self):
        migration_path = self.tmpdir / "migration.md"
        migration_path.write_text("Some text.", encoding="utf-8")
        fake_summary = {"changed": False, "outcome": "no_candidate"}
        with mock.patch.object(bump_mod, "fetch_version_metadata", return_value=cli_version_metadata("NEWID")):
            with mock.patch.object(bump_mod, "run_engine", return_value=(fake_summary, bump_mod.EXIT_OK, "ok")):
                with mock.patch("sys.stderr") as mock_stderr:
                    bump_mod.cli_main(self._base_args(["--migration-text-file", str(migration_path)]))
        warned = "".join(call.args[0] for call in mock_stderr.write.call_args_list if call.args)
        self.assertIn("will not appear", warned)

    def test_anonymous_404_names_modrinth_token_not_a_bare_traceback(self):
        error = urllib.error.HTTPError("https://api.modrinth.com/v2/version/x", 404, "Not Found", {}, None)
        with mock.patch.object(bump_mod, "fetch_version_metadata", side_effect=error):
            with self.assertRaises(SystemExit) as ctx:
                bump_mod.cli_main(self._base_args())
        self.assertIn("MODRINTH_TOKEN", str(ctx.exception))

    def test_transport_failure_is_a_clean_exit_not_a_bare_traceback(self):
        error = urllib.error.URLError("timed out")
        with mock.patch.object(bump_mod, "fetch_version_metadata", side_effect=error):
            with self.assertRaises(SystemExit) as ctx:
                bump_mod.cli_main(self._base_args())
        self.assertIn("timed out", str(ctx.exception))

    def test_engine_error_is_reported_with_its_own_exit_code(self):
        with mock.patch.object(bump_mod, "fetch_version_metadata", return_value=cli_version_metadata("NEWID")):
            with mock.patch.object(
                bump_mod, "run_engine",
                side_effect=bump_mod.EngineError(bump_mod.EXIT_DOWNGRADE_REFUSED, "downgrade_refused", "nope"),
            ):
                exit_code = bump_mod.cli_main(self._base_args())
        self.assertEqual(exit_code, bump_mod.EXIT_DOWNGRADE_REFUSED)


if __name__ == "__main__":
    unittest.main()
