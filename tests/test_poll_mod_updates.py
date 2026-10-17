import hashlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "poll-mod-updates.py"
SPEC = importlib.util.spec_from_file_location("poll_mod_updates", MODULE_PATH)
poll_mod_updates = importlib.util.module_from_spec(SPEC)
sys.modules["poll_mod_updates"] = poll_mod_updates
SPEC.loader.exec_module(poll_mod_updates)

STUB_GATES = {"refresh": ["true"], "check": ["true"], "build": ["true"]}


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(repo), check=check, capture_output=True, text=True)


def _pin_toml(name, filename, mod_id, version_id, hash_value):
    return (
        f'name = "{name}"\n'
        f'filename = "{filename}"\n'
        'side = "both"\n'
        "\n"
        "[download]\n"
        f'url = "https://cdn.modrinth.com/data/{mod_id}/versions/{version_id}/{filename}"\n'
        'hash-format = "sha512"\n'
        f'hash = "{hash_value}"\n'
        "\n"
        "[update]\n"
        "[update.modrinth]\n"
        f'mod-id = "{mod_id}"\n'
        f'version = "{version_id}"\n'
    )


def build_fixture_repo():
    repo = Path(tempfile.mkdtemp(prefix="poll-mod-fixture-"))
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

    (repo / "mods" / "dynamic-atmosphere.pw.toml").write_text(
        _pin_toml("Dynamic Atmosphere", "dynamicatmosphere-0.19.0.jar", "PZV7RorC", "DAOLD", "a" * 128),
        encoding="utf-8",
    )
    (repo / "mods" / "alpha-mod.pw.toml").write_text(
        _pin_toml("Alpha Mod", "alphamod-1.0.0.jar", "ALPHA1", "ALPHAOLD", "b" * 128),
        encoding="utf-8",
    )
    (repo / "mods" / "beta-mod.pw.toml").write_text(
        _pin_toml("Beta Mod", "betamod-1.0.0.jar", "BETA1", "BETAOLD", "c" * 128),
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("/build/\n", encoding="utf-8")

    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def version_entry(mod_id, version_id, version_number, content, *, game_versions=("1.21.1",), loaders=("neoforge",),
                   version_type="release", changelog=""):
    filename = f"{mod_id.lower()}-{version_number}.jar"
    return {
        "id": version_id, "version_number": version_number, "version_type": version_type,
        "changelog": changelog,
        "game_versions": list(game_versions), "loaders": list(loaders),
        "dependencies": [],
        "files": [{
            "primary": True, "filename": filename,
            "hashes": {"sha1": hashlib.sha1(content).hexdigest(), "sha512": hashlib.sha512(content).hexdigest()},
            "url": f"https://cdn.modrinth.com/data/{mod_id}/versions/{version_id}/{filename}",
        }],
    }


def version_metadata_from_entry(entry, project_id):
    primary = entry["files"][0]
    return {
        "id": entry["id"], "project_id": project_id, "version_number": entry["version_number"],
        "files": [primary],
    }


class FakeModrinth:
    """Routes both the version-list (compat resolution) and single-version
    (identity gate) Modrinth calls a real engine run needs, from one
    hand-built table, so tests never touch the network."""

    def __init__(self):
        self.version_lists = {}  # mod_id -> [entry, ...]
        self.single_versions = {}  # version_id -> metadata dict
        self.content_by_url = {}  # download_url -> real bytes, for the stub downloader

    def register_latest(self, mod_id, version_id, version_number, content, **kwargs):
        entry = version_entry(mod_id, version_id, version_number, content, **kwargs)
        self.version_lists.setdefault(mod_id, []).append(entry)
        self.single_versions[version_id] = version_metadata_from_entry(entry, mod_id)
        self.content_by_url[entry["files"][0]["url"]] = content

    def downloader(self, url, dest_path):
        Path(dest_path).write_bytes(self.content_by_url.get(url, b""))

    def register_old_pin_metadata(self, version_id, mod_id, version_number):
        self.single_versions[version_id] = {"id": version_id, "project_id": mod_id, "version_number": version_number}

    def list_fetcher(self, url):
        for mod_id in self.version_lists:
            if f"/project/{mod_id}/version" in url:
                return self.version_lists[mod_id]
        return []

    def single_fetcher(self, version_id):
        import urllib.error
        if version_id not in self.single_versions:
            raise urllib.error.HTTPError(f"https://api.modrinth.com/v2/version/{version_id}", 404, "Not Found", {}, None)
        return self.single_versions[version_id]


class TestPollAndBump(unittest.TestCase):
    def setUp(self):
        self.repo = build_fixture_repo()
        self.modrinth = FakeModrinth()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def run_poll(self):
        # bump_mod.run_engine's metadata_fetcher must hit single_fetcher,
        # not the list fetcher poll_and_bump passes to modrinth_compat --
        # patch run_engine so both wirings are exercised exactly as the
        # real poll/engine split uses them (stub gates/downloader too, so
        # no real `make`/network call happens in a unit test).
        original_run_engine = poll_mod_updates.bump_mod.run_engine

        def patched_run_engine(repo_root, pin, candidate, **kwargs):
            kwargs["metadata_fetcher"] = self.modrinth.single_fetcher
            kwargs["gate_commands"] = STUB_GATES
            kwargs["downloader"] = self.modrinth.downloader
            return original_run_engine(repo_root, pin, candidate, **kwargs)

        with mock.patch.object(poll_mod_updates.bump_mod, "run_engine", side_effect=patched_run_engine):
            return poll_mod_updates.poll_and_bump(self.repo, fetcher=self.modrinth.list_fetcher)

    def test_includes_dynamic_atmosphere_pin_too(self):
        # No excluded pins: this poll is DA's recovery backstop if a
        # queued dispatch gets replaced by a cron run sharing the same
        # concurrency group (see this module's docstring). With nothing
        # newer registered for DA, it is simply a clean no-op like any
        # other pin -- not silently skipped.
        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertIn("PZV7RorC", by_mod)
        self.assertEqual(by_mod["PZV7RorC"]["outcome"], "no_candidate")

    def test_dynamic_atmosphere_pin_is_bumped_like_any_other(self):
        self.modrinth.register_latest("PZV7RorC", "DANEW", "0.20.0", b"da v0.20.0")
        self.modrinth.register_old_pin_metadata("DAOLD", "PZV7RorC", "0.19.0")
        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["PZV7RorC"]["outcome"], "bumped")

    def test_no_candidate_when_nothing_newer(self):
        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "no_candidate")
        self.assertEqual(by_mod["BETA1"]["outcome"], "no_candidate")
        self.assertFalse(by_mod["ALPHA1"]["changed"])

    def test_single_mod_bump_commits(self):
        content = b"alpha jar v2"
        self.modrinth.register_latest("ALPHA1", "ALPHANEW", "1.1.0", content)
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")

        before_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        outcomes = self.run_poll()
        after_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()

        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "bumped")
        self.assertTrue(by_mod["ALPHA1"]["changed"])
        self.assertNotEqual(before_head, after_head)
        self.assertEqual(by_mod["ALPHA1"]["commit_sha"], after_head)

        pack_text = (self.repo / "pack.toml").read_text(encoding="utf-8")
        self.assertIn('version = "0.23.0"', pack_text)

        log = _git(self.repo, "log", "-1", "--format=%B").stdout
        self.assertIn("Mod-Auto-Bump: ALPHA1 ALPHANEW", log)
        self.assertIn("Release-Only: true", log)

    def test_two_mods_pending_only_the_first_bumps_this_run(self):
        # At most one bump commit per run (MINECRAFT-362 review, second
        # round): a multi-bump run would race release.yml's own
        # concurrency group and drop every release but the last. Alpha
        # sorts first alphabetically, so it bumps and the run stops;
        # Beta is left for the next scheduled run, not reported as any
        # kind of failure -- it simply has no entry in this run's summary.
        self.modrinth.register_latest("ALPHA1", "ALPHANEW", "1.1.0", b"alpha v2")
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")
        self.modrinth.register_latest("BETA1", "BETANEW", "2.0.0", b"beta v2")
        self.modrinth.register_old_pin_metadata("BETAOLD", "BETA1", "1.0.0")

        before_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        outcomes = self.run_poll()
        after_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()

        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "bumped")
        self.assertNotIn("BETA1", by_mod)
        self.assertEqual(by_mod["ALPHA1"]["new_pack_version"], "0.23.0")

        # Exactly one commit was made this run.
        log_shas = _git(self.repo, "log", "--format=%H", f"{before_head}..{after_head}").stdout.split()
        self.assertEqual(len(log_shas), 1)
        self.assertEqual(log_shas[0], after_head)

    def test_incompatible_version_is_skipped_as_no_candidate(self):
        self.modrinth.register_latest(
            "ALPHA1", "ALPHANEW", "1.1.0", b"incompatible build",
            game_versions=("1.20.1",),
        )
        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "no_candidate")

    def test_downgrade_is_reported_not_crashed(self):
        self.modrinth.register_latest("ALPHA1", "ALPHANEW", "0.5.0", b"older build")
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")
        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "downgrade_refused")
        self.assertFalse(by_mod["ALPHA1"]["changed"])

    def test_category_derived_from_changelog_is_passed_to_the_engine(self):
        self.modrinth.register_latest(
            "ALPHA1", "ALPHANEW", "1.1.0", b"alpha v2",
        )
        self.modrinth.version_lists["ALPHA1"][0]["changelog"] = "## Fixes\n- fixed a crash\n"
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")

        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "bumped")
        self.assertEqual(by_mod["ALPHA1"]["mapping_rule"], "patch")
        self.assertEqual(by_mod["ALPHA1"]["new_pack_version"], "0.22.1")

    def test_one_mod_failing_does_not_block_the_others(self):
        # Alpha's candidate forges hashes that don't match its own
        # metadata -> integrity failure. Beta still gets considered and
        # bumped in the same run.
        content = b"alpha v2"
        entry = version_entry("ALPHA1", "ALPHANEW", "1.1.0", content)
        self.modrinth.version_lists["ALPHA1"] = [entry]
        corrupted_metadata = version_metadata_from_entry(entry, "ALPHA1")
        corrupted_metadata["files"][0] = dict(corrupted_metadata["files"][0])
        corrupted_metadata["files"][0]["hashes"] = {"sha1": "0" * 40, "sha512": "0" * 128}
        self.modrinth.single_versions["ALPHANEW"] = corrupted_metadata

        self.modrinth.register_latest("BETA1", "BETANEW", "2.0.0", b"beta v2")
        self.modrinth.register_old_pin_metadata("BETAOLD", "BETA1", "1.0.0")

        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "integrity_failure")
        self.assertEqual(by_mod["BETA1"]["outcome"], "bumped")

    def test_breaking_change_is_not_auto_published(self):
        # bump_mod.run_engine's allow_breaking gate (default False) fires
        # for a poll-derived "breaking" category -- this script never
        # passes allow_breaking=True, so a breaking candidate is reported,
        # not silently published with a placeholder migration note.
        entry = version_entry(
            "ALPHA1", "ALPHANEW", "2.0.0", b"breaking alpha",
            changelog="## Breaking\n- renamed a config key",
        )
        self.modrinth.version_lists["ALPHA1"] = [entry]
        self.modrinth.single_versions["ALPHANEW"] = version_metadata_from_entry(entry, "ALPHA1")
        self.modrinth.content_by_url[entry["files"][0]["url"]] = b"breaking alpha"
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")

        before_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        outcomes = self.run_poll()
        after_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()

        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "breaking_change_requires_human_review")
        self.assertFalse(by_mod["ALPHA1"]["changed"])
        self.assertEqual(before_head, after_head)


class TestMainExitCode(unittest.TestCase):
    """Exercises main()'s pass/fail classification directly (item 2,
    MINECRAFT-362 review second round) without touching Modrinth or git --
    poll_and_bump itself is mocked, since what's under test here is purely
    "does this set of outcomes make the job red or green"."""

    def _run_main_with(self, outcomes):
        with mock.patch.object(poll_mod_updates, "poll_and_bump", return_value=outcomes):
            with mock.patch.dict("os.environ", {}, clear=False):
                import io
                import contextlib
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    exit_code = poll_mod_updates.main(["--repo-root", "."])
        return exit_code, buf.getvalue()

    def test_expected_gaps_alone_do_not_fail_the_run(self):
        # "A" uses a mod_id ON the known-anonymous-404 allowlist -- a
        # resolution_failed for a mod_id NOT on it is a different test
        # below, since that must fail, not warn.
        known_404_mod_id = next(iter(poll_mod_updates.KNOWN_ANONYMOUS_404_MOD_IDS))
        outcomes = [
            {"mod_id": known_404_mod_id, "mod_name": "A", "pin_path": "mods/a.pw.toml", "outcome": "resolution_failed", "error": "404"},
            {"mod_id": "B", "mod_name": "B", "pin_path": "mods/b.pw.toml", "outcome": "unsupported_version_scheme", "error": "x"},
            {"mod_id": "C", "mod_name": "C", "pin_path": "mods/c.pw.toml", "outcome": "breaking_change_requires_human_review", "error": "y"},
            {"mod_id": "D", "mod_name": "D", "pin_path": "mods/d.pw.toml", "outcome": "no_candidate", "error": None},
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 0)
        self.assertIn("::warning::A (mods/a.pw.toml)", output)
        self.assertIn("::warning::B (mods/b.pw.toml)", output)
        self.assertIn("::warning::C (mods/c.pw.toml)", output)

    def test_a_real_unexpected_failure_still_fails_the_run(self):
        known_404_mod_id = next(iter(poll_mod_updates.KNOWN_ANONYMOUS_404_MOD_IDS))
        outcomes = [
            {"mod_id": known_404_mod_id, "mod_name": "A", "pin_path": "mods/a.pw.toml", "outcome": "resolution_failed", "error": "404"},
            {"mod_id": "E", "mod_name": "E", "pin_path": "mods/e.pw.toml", "outcome": "integrity_failure", "error": "bad hash"},
            {"mod_id": "D", "mod_name": "D", "pin_path": "mods/d.pw.toml", "outcome": "no_candidate", "error": None},
        ]
        exit_code, _ = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 1)

    def test_resolution_failed_for_an_unlisted_mod_id_fails_the_run(self):
        # The core of this review round's fix: "resolution_failed" is NOT
        # a blanket expected gap. A project that is not on the
        # individually-confirmed allowlist (e.g. a Modrinth 5xx, a DNS
        # failure, a revoked token, or a newly-deleted/privated project)
        # must fail the run, mixed in with otherwise-normal outcomes so
        # it can't hide behind the aggregate guard either.
        outcomes = [
            {"mod_id": "SOME_OTHER_PROJECT", "mod_name": "F", "pin_path": "mods/f.pw.toml", "outcome": "resolution_failed", "error": "HTTP 503"},
            {"mod_id": "D", "mod_name": "D", "pin_path": "mods/d.pw.toml", "outcome": "no_candidate", "error": None},
        ]
        exit_code, _ = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 1)

    def test_every_pin_failing_to_resolve_fails_regardless_of_allowlist(self):
        # Aggregate guard: even if every single resolution_failed outcome
        # IS on the allowlist, a run where NOTHING else succeeded (no
        # no_candidate/already_pinned/bumped at all) signals a systemic
        # problem (outage, response-shape change), not the documented
        # per-pin baseline.
        outcomes = [
            {"mod_id": mod_id, "mod_name": mod_id, "pin_path": f"mods/{mod_id}.pw.toml",
             "outcome": "resolution_failed", "error": "HTTP 503"}
            for mod_id in poll_mod_updates.KNOWN_ANONYMOUS_404_MOD_IDS
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 1)
        self.assertIn("::error::every pin examined this run failed to resolve at all", output)


if __name__ == "__main__":
    unittest.main()
