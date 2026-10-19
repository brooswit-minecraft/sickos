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

modrinth_compat = poll_mod_updates.modrinth_compat

STUB_GATES = {"refresh": ["true"], "check": ["true"], "build": ["true"], "dependency_closure": ["true"]}


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
        "version_type": entry.get("version_type", "release"),
        # Carried through from the list entry (MINECRAFT-362 review,
        # seventeenth round): run_engine's own metadata re-verification
        # now checks game_versions/loaders too, so the single-version
        # identity gate's response needs them present and matching,
        # same as the list response already has.
        "game_versions": entry.get("game_versions", []),
        "loaders": entry.get("loaders", []),
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
        # Normal rollout is the current contract (MINECRAFT-362 review,
        # comment 48456, item 5): no Release-Only trailer, so this
        # commit deploys/restarts the server the usual way once released.
        self.assertNotIn("Release-Only", log)

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

    def test_only_prerelease_entries_is_a_distinct_outcome_end_to_end(self):
        # MINECRAFT-362 review, tenth round, end-to-end through
        # poll_and_bump (not just main()'s classification in isolation).
        entry = version_entry("ALPHA1", "ALPHANEW", "1.1.0-beta.1", b"only beta", version_type="beta")
        self.modrinth.version_lists["ALPHA1"] = [entry]
        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "only_prerelease_available")

    def test_no_parseable_candidate_is_a_distinct_outcome_end_to_end(self):
        entry = version_entry("ALPHA1", "ALPHANEW", "not-semver-at-all", b"unparseable")
        self.modrinth.version_lists["ALPHA1"] = [entry]
        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "no_parseable_candidate")

    def test_downgrade_is_reported_not_crashed(self):
        self.modrinth.register_latest("ALPHA1", "ALPHANEW", "0.5.0", b"older build")
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")
        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "downgrade_refused")
        self.assertFalse(by_mod["ALPHA1"]["changed"])

    def test_category_derived_from_changelog_is_passed_to_the_engine(self):
        # A patch-level semver delta (1.0.0 -> 1.0.1) with an agreeing
        # changelog heading -- both signals say "patch", so that's what
        # reaches the engine. (A disagreeing changelog cannot downgrade
        # a more severe semver delta -- see test_poll_mod_updates'
        # sibling tests in tests/test_bump_mod.py for that escalation
        # behaviour; this test only checks the changelog hint reaches
        # run_engine at all.)
        self.modrinth.register_latest(
            "ALPHA1", "ALPHANEW", "1.0.1", b"alpha v2",
        )
        self.modrinth.version_lists["ALPHA1"][0]["changelog"] = "## Fixes\n- fixed a crash\n"
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")

        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "bumped")
        self.assertEqual(by_mod["ALPHA1"]["mapping_rule"], "patch")
        self.assertEqual(by_mod["ALPHA1"]["new_pack_version"], "0.22.1")

    def test_changelog_category_cannot_downgrade_the_semver_delta(self):
        # 1.0.0 -> 1.1.0 is a minor-component delta; a "## Fixes" heading
        # (patch) must not downgrade that -- the more severe of the two
        # wins.
        self.modrinth.register_latest("ALPHA1", "ALPHANEW", "1.1.0", b"alpha v2")
        self.modrinth.version_lists["ALPHA1"][0]["changelog"] = "## Fixes\n- fixed a crash\n"
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")

        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "bumped")
        self.assertEqual(by_mod["ALPHA1"]["mapping_rule"], "minor")
        self.assertEqual(by_mod["ALPHA1"]["new_pack_version"], "0.23.0")

    def test_real_bullet_list_changelog_still_classifies_from_semver_delta(self):
        # The exact shape of a real Modrinth changelog -- a bullet list,
        # not a heading -- which derive_category_from_changelog cannot
        # match (measured live: 0 of 15 real candidates matched). The
        # semver delta alone must still classify this correctly.
        self.modrinth.register_latest("ALPHA1", "ALPHANEW", "1.0.1", b"alpha v2")
        self.modrinth.version_lists["ALPHA1"][0]["changelog"] = "- Fixed memory leak in BlockEntityOnLoadCallback"
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")

        outcomes = self.run_poll()
        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "bumped")
        self.assertEqual(by_mod["ALPHA1"]["mapping_rule"], "patch")

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

        self.modrinth.register_latest("BETA1", "BETANEW", "1.1.0", b"beta v2")
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
        # MINECRAFT-362 review, ninth round: this assertion was computed
        # (before_head/after_head) but never actually checked -- restored.
        # A regression that refuses the bump outcome yet commits anyway
        # must fail this test.
        self.assertEqual(before_head, after_head)

    def test_game_version_and_loader_reach_the_category_gate_end_to_end(self):
        # MINECRAFT-362 review, eighth round: poll_and_bump must actually
        # PASS its own game_version/loader (read from this fixture's
        # pack.toml: "1.21.1"/"neoforge") through to run_engine's
        # category determination -- not just have semver_delta_category
        # support them in isolation. The YUNG's family's real scheme
        # ("<game_version>-<loader>-<mod_version>") only classifies
        # correctly with both.
        self.modrinth.register_latest(
            "ALPHA1", "ALPHANEW", "1.21.1-NeoForge-6.0.0", b"yungs-style breaking",
        )
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.21.1-NeoForge-5.1.9")

        before_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        outcomes = self.run_poll()
        after_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()

        by_mod = {o["mod_id"]: o for o in outcomes}
        self.assertEqual(by_mod["ALPHA1"]["outcome"], "breaking_change_requires_human_review")
        self.assertFalse(by_mod["ALPHA1"]["changed"])
        self.assertEqual(before_head, after_head)

    def test_untracked_pin_is_reported_as_an_outcome_not_discarded(self):
        # MINECRAFT-362 review, twelfth round, required item 2:
        # mod_eligibility.discover_eligible_pins' `untracked` list was
        # previously bound to `_untracked` and never read -- a pin with
        # no [update.modrinth] table produced NO outcome at all, the one
        # route that drops coverage without tripping any gap
        # classification. It must now appear in poll_and_bump's own
        # outcome list, unconditionally (not subject to the
        # one-bump-per-run stop), so main() can reconcile it like the
        # four gap classes above.
        (self.repo / "mods" / "no-modrinth-pin.pw.toml").write_text(
            'name = "No Modrinth Pin"\n'
            'filename = "nomodrinthpin-1.0.0.jar"\n'
            'side = "both"\n'
            "\n"
            "[download]\n"
            'url = "https://example.invalid/nomodrinthpin-1.0.0.jar"\n'
            'hash-format = "sha512"\n'
            f'hash = "{"d" * 128}"\n',
            encoding="utf-8",
        )
        outcomes = self.run_poll()
        untracked = [o for o in outcomes if o["outcome"] == "untracked"]
        self.assertEqual(len(untracked), 1)
        self.assertEqual(untracked[0]["pin_path"], "mods/no-modrinth-pin.pw.toml")
        self.assertIsNone(untracked[0]["mod_id"])
        # Still reported even on a run that also makes a real bump (the
        # one-bump-per-run stop only applies to the ELIGIBLE loop).
        self.modrinth.register_latest("ALPHA1", "ALPHANEW", "1.1.0", b"alpha v2")
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")
        outcomes = self.run_poll()
        untracked = [o for o in outcomes if o["outcome"] == "untracked"]
        bumped = [o for o in outcomes if o["outcome"] == "bumped"]
        self.assertEqual(len(untracked), 1)
        self.assertEqual(len(bumped), 1)

    def test_excluded_local_only_pin_is_its_own_outcome_not_a_coverage_regression(self):
        # MINECRAFT-362 review, thirteenth round, required item 1: a pin
        # named in mod_eligibility.EXCLUDED_MOD_NAMES (Herobrine API,
        # Baritone) lands in discover_eligible_pins' `untracked` list
        # alongside a genuinely-untracked pin, but for a completely
        # different, DELIBERATE reason -- this ticket itself requires
        # these never enter the public pack. Round 12 reconciled the
        # whole `untracked` list by pin_path, which meant adding exactly
        # the pin this ticket names as a requirement would fail every
        # scheduled run forever (no EXCLUDED_MOD_NAMES pin's path can be
        # on a static allowlist in advance). It must get its own outcome
        # kind, unconditionally expected, never reconciled by path.
        (self.repo / "mods" / "baritone.pw.toml").write_text(
            'name = "Baritone"\n'
            'filename = "baritone-1.0.0.jar"\n'
            'side = "client"\n'
            "\n"
            "[download]\n"
            'url = "https://example.invalid/baritone-1.0.0.jar"\n'
            'hash-format = "sha512"\n'
            f'hash = "{"e" * 128}"\n',
            encoding="utf-8",
        )
        outcomes = self.run_poll()
        excluded = [o for o in outcomes if o["outcome"] == "excluded_local_only"]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["pin_path"], "mods/baritone.pw.toml")
        self.assertEqual(excluded[0]["mod_name"], "Baritone")
        self.assertTrue(poll_mod_updates._is_expected_gap(excluded[0]))

    def test_excluded_pin_path_is_its_own_outcome_not_silently_dropped(self):
        # MINECRAFT-362 review, fifteenth round, smaller item: EXCLUDED_
        # PIN_PATHS is empty today, but its `continue` branch previously
        # dropped a matching pin with no outcome at all -- the latent
        # version of the exact silently-dropped-pin shape rounds twelve
        # and thirteen fixed for untracked/excluded_local_only pins. A
        # pin on this list must now get its own "excluded_pin_path"
        # outcome, unconditionally expected.
        with mock.patch.object(poll_mod_updates, "EXCLUDED_PIN_PATHS", frozenset({"mods/alpha-mod.pw.toml"})):
            outcomes = self.run_poll()
        excluded = [o for o in outcomes if o["outcome"] == "excluded_pin_path"]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["pin_path"], "mods/alpha-mod.pw.toml")
        self.assertEqual(excluded[0]["mod_id"], "ALPHA1")
        self.assertTrue(poll_mod_updates._is_expected_gap(excluded[0]))


class TestResolveCandidatePreservingChannel(unittest.TestCase):
    """MINECRAFT-421 review, point 1: `_resolve_candidate_preserving_channel`
    retries with include_prerelease=True only when the PIN's own
    currently-pinned version is itself non-release -- never from the
    candidate's shape alone. This pack's real Biomes O' Plenty/
    GlitchCore/IceAndFire Community Edition pins are the confirmed-live
    positive case; any pin pinned to a real release is the negative case
    (must behave exactly as before this round)."""

    def _pin(self, mod_id="EXAMPLE", pinned_version_id="OLDID"):
        return poll_mod_updates.mod_eligibility.EligiblePin(
            pin_path="mods/example.pw.toml", name="Example", side="both",
            mod_id=mod_id, pinned_version_id=pinned_version_id,
        )

    def test_release_pinned_channel_does_not_retry_negative(self):
        pin = self._pin()
        with mock.patch.object(
            poll_mod_updates.modrinth_compat, "resolve_latest_compatible_version",
            side_effect=modrinth_compat.OnlyPrereleaseAvailableError("only prerelease"),
        ) as resolve_mock, mock.patch.object(
            poll_mod_updates.bump_mod, "fetch_version_metadata",
            return_value={"id": "OLDID", "version_type": "release"},
        ):
            with self.assertRaises(modrinth_compat.OnlyPrereleaseAvailableError):
                poll_mod_updates._resolve_candidate_preserving_channel(
                    pin, game_version="1.21.1", loader="neoforge", fetcher=mock.Mock(), token=None,
                )
        # Only the one, default (include_prerelease=False) call -- no retry.
        self.assertEqual(resolve_mock.call_count, 1)
        self.assertNotIn("include_prerelease", resolve_mock.call_args.kwargs)

    def test_prerelease_pinned_channel_retries_and_resolves_positive(self):
        pin = self._pin()
        candidate = mock.Mock(name="candidate")
        calls = []

        def fake_resolve(mod_id, *, game_version, loader, fetcher, include_prerelease=False):
            calls.append(include_prerelease)
            if not include_prerelease:
                raise modrinth_compat.OnlyPrereleaseAvailableError("only prerelease")
            return candidate

        with mock.patch.object(
            poll_mod_updates.modrinth_compat, "resolve_latest_compatible_version", side_effect=fake_resolve,
        ), mock.patch.object(
            poll_mod_updates.bump_mod, "fetch_version_metadata",
            return_value={"id": "OLDID", "version_type": "beta"},
        ):
            result = poll_mod_updates._resolve_candidate_preserving_channel(
                pin, game_version="1.21.1", loader="neoforge", fetcher=mock.Mock(), token=None,
            )
        self.assertIs(result, candidate)
        self.assertEqual(calls, [False, True])

    def test_pinned_metadata_lookup_failure_fails_closed_to_original_error(self):
        pin = self._pin()
        with mock.patch.object(
            poll_mod_updates.modrinth_compat, "resolve_latest_compatible_version",
            side_effect=modrinth_compat.OnlyPrereleaseAvailableError("only prerelease"),
        ), mock.patch.object(
            poll_mod_updates.bump_mod, "fetch_version_metadata", side_effect=RuntimeError("network down"),
        ):
            with self.assertRaises(modrinth_compat.OnlyPrereleaseAvailableError):
                poll_mod_updates._resolve_candidate_preserving_channel(
                    pin, game_version="1.21.1", loader="neoforge", fetcher=mock.Mock(), token=None,
                )

    def test_other_errors_propagate_untouched(self):
        # NoParseableCandidateError/CompatResolutionError are not this
        # helper's concern at all -- only OnlyPrereleaseAvailableError is
        # ever retried.
        pin = self._pin()
        with mock.patch.object(
            poll_mod_updates.modrinth_compat, "resolve_latest_compatible_version",
            side_effect=modrinth_compat.NoParseableCandidateError("nope"),
        ):
            with self.assertRaises(modrinth_compat.NoParseableCandidateError):
                poll_mod_updates._resolve_candidate_preserving_channel(
                    pin, game_version="1.21.1", loader="neoforge", fetcher=mock.Mock(), token=None,
                )


GITHUB_PIN_SLUG = "brooswit-minecraft/dynamic-despawn"
GITHUB_PIN_REGEX = r"^.+\.jar$"


class TestPollAndBumpGithubPins(unittest.TestCase):
    """MINECRAFT-362 review, comment 48415: poll_and_bump must also
    resolve packwiz-native [update.github] pins, sharing ONE
    one-bump-per-run counter with the Modrinth loop above -- a Modrinth
    bump this run means the GitHub loop does no work at all, not just
    that it stops early."""

    def setUp(self):
        self.repo = build_fixture_repo()
        self.modrinth = FakeModrinth()
        pin_text = (
            'name = "Dynamic Despawn"\n'
            'filename = "dynamicdespawn-0.3.3.jar"\n'
            'side = "both"\n'
            "\n"
            "[download]\n"
            f'url = "https://github.com/{GITHUB_PIN_SLUG}/releases/download/v0.3.3/dynamicdespawn-0.3.3.jar"\n'
            'hash-format = "sha256"\n'
            f'hash = "{"a" * 64}"\n'
            "\n"
            "[update]\n"
            "[update.github]\n"
            f'slug = "{GITHUB_PIN_SLUG}"\n'
            'tag = "v0.3.3"\n'
            f'branch = "{"a" * 40}"\n'
            + r'regex = "^.+\\.jar$"' + "\n"
        )
        (self.repo / "mods" / "dynamic-despawn.pw.toml").write_text(pin_text, encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "add github-tracked pin")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def run_poll(self, *, release=None, previous_release=None):
        release = release or self._release(tag="v0.3.3", digest_hex="a" * 64)
        previous_release = previous_release or self._release(tag="v0.3.3", digest_hex="a" * 64)
        original_run_engine = poll_mod_updates.bump_mod.run_engine

        def patched_run_engine(repo_root, pin, candidate, **kwargs):
            kwargs["metadata_fetcher"] = self.modrinth.single_fetcher
            kwargs["gate_commands"] = STUB_GATES
            kwargs["downloader"] = self.modrinth.downloader
            return original_run_engine(repo_root, pin, candidate, **kwargs)

        original_github_run_engine = poll_mod_updates.bump_mod_github.run_engine

        def patched_github_run_engine(repo_root, pin, candidate, **kwargs):
            kwargs["gate_commands"] = STUB_GATES
            return original_github_run_engine(repo_root, pin, candidate, **kwargs)

        with mock.patch.object(
            poll_mod_updates.bump_mod, "run_engine", side_effect=patched_run_engine,
        ), mock.patch.object(
            poll_mod_updates.bump_mod_github, "run_engine", side_effect=patched_github_run_engine,
        ), mock.patch.object(
            poll_mod_updates.github_release_identity, "fetch_releases_json", return_value=[release],
        ), mock.patch.object(
            poll_mod_updates.github_release_identity, "fetch_release_by_tag_json",
            return_value=previous_release,
        ), mock.patch.object(
            # The pinned tag itself must resolve to the SAME source already
            # recorded on disk ("a" * 40, see setUp's pin fixture) or
            # _verify_identity_matches_pin's source check fails every one
            # of these tests before reaching whatever they're actually
            # exercising (MINECRAFT-362 review, comment 48492) -- only a
            # genuinely NEW tag resolves to the "b" * 40 placeholder.
            poll_mod_updates.github_release_identity, "resolve_tag_source_sha",
            side_effect=lambda slug, tag: ("a" * 40) if tag == "v0.3.3" else ("b" * 40),
        ), mock.patch.object(
            poll_mod_updates.github_release_identity, "download_and_verify_sha256",
            return_value=b"new despawn jar",
        ):
            return poll_mod_updates.poll_and_bump(self.repo, fetcher=self.modrinth.list_fetcher)

    def _release(self, *, tag="v0.3.4", content=b"new despawn jar", digest_hex=None, prerelease=False):
        digest_hex = digest_hex or hashlib.sha256(content).hexdigest()
        filename = f"dynamicdespawn-{tag.lstrip('v')}.jar"
        return {
            "tag_name": tag,
            "assets": [{
                "name": filename,
                "digest": "sha256:" + digest_hex,
                "browser_download_url": f"https://github.com/{GITHUB_PIN_SLUG}/releases/download/{tag}/{filename}",
                "size": len(content),
            }],
            "created_at": "2026-02-01T00:00:00Z", "draft": False, "prerelease": prerelease,
            "target_commitish": "b" * 40, "body": "",
        }

    def test_github_pin_bumps_when_nothing_newer_on_modrinth_side(self):
        outcomes = self.run_poll(release=self._release())
        by_path = {o["pin_path"]: o for o in outcomes}
        self.assertEqual(by_path["mods/dynamic-despawn.pw.toml"]["outcome"], "bumped")
        pin_text = (self.repo / "mods" / "dynamic-despawn.pw.toml").read_text(encoding="utf-8")
        self.assertIn('tag = "v0.3.4"', pin_text)

    def test_no_candidate_when_already_at_latest(self):
        outcomes = self.run_poll()  # default release is already v0.3.3, the pinned tag
        by_path = {o["pin_path"]: o for o in outcomes}
        self.assertEqual(by_path["mods/dynamic-despawn.pw.toml"]["outcome"], "already_pinned")

    def test_a_modrinth_bump_this_run_means_the_github_loop_does_no_work_at_all(self):
        self.modrinth.register_latest("ALPHA1", "ALPHANEW", "1.1.0", b"alpha v2")
        self.modrinth.register_old_pin_metadata("ALPHAOLD", "ALPHA1", "1.0.0")
        # The ALPHA1 Modrinth pin sorts before dynamic-despawn.pw.toml
        # alphabetically, so it bumps first and the shared `bumped`
        # counter must skip the GitHub loop entirely -- it never even
        # calls out to discover a candidate for dynamic-despawn.
        outcomes = self.run_poll(release=self._release())
        by_path = {o["pin_path"]: o for o in outcomes}
        self.assertEqual(by_path["mods/alpha-mod.pw.toml"]["outcome"], "bumped")
        self.assertNotIn("mods/dynamic-despawn.pw.toml", by_path)

    def test_accepted_prerelease_channel_is_preserved(self):
        """MINECRAFT-362 review, comment 48456, item 4: a pin whose
        CURRENTLY-pinned release is itself a prerelease (this pack's
        real dynamic-atmosphere/dynamic-population pins) must still
        discover a newer prerelease -- include_prerelease is derived
        from the currently-pinned release's own flag, not a global
        default."""
        previous = self._release(tag="v0.3.3", digest_hex="a" * 64, prerelease=True)
        newer_prerelease = self._release(tag="v0.4.0-alpha.1", prerelease=True)
        outcomes = self.run_poll(release=newer_prerelease, previous_release=previous)
        by_path = {o["pin_path"]: o for o in outcomes}
        self.assertEqual(by_path["mods/dynamic-despawn.pw.toml"]["outcome"], "bumped")


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

    # A real, healthy run always has at least one already_pinned or
    # bumped pin (MINECRAFT-362 review, eleventh round's aggregate
    # guard) -- every outcomes list below that is meant to pass includes
    # one of these so it reflects that invariant, not just the specific
    # gap being tested.
    _HEALTHY_PIN = {"mod_id": "HEALTHY1", "mod_name": "Healthy Mod", "pin_path": "mods/healthy.pw.toml",
                    "outcome": "already_pinned", "error": None}

    def test_expected_gaps_alone_do_not_fail_the_run(self):
        # "B"/"C" use real mod_ids from their matching KNOWN_*_PIN_IDS
        # dicts (or, for "C", an unlistable-by-design outcome), since
        # _is_expected_gap checks membership by mod_id, not outcome
        # string alone. KNOWN_ANONYMOUS_404_MOD_IDS is empty (sixteenth
        # review round: MODRINTH_TOKEN is actually configured, so
        # "resolution_failed" is no longer presumed expected for any
        # mod_id -- see test_resolution_failed_is_never_an_expected_gap_now).
        known_non_semver_mod_id = next(iter(poll_mod_updates.KNOWN_NON_SEMVER_PIN_IDS))
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": known_non_semver_mod_id, "mod_name": "B", "pin_path": "mods/b.pw.toml", "outcome": "unsupported_version_scheme", "error": "x"},
            {"mod_id": "C", "mod_name": "C", "pin_path": "mods/c.pw.toml", "outcome": "breaking_change_requires_human_review", "error": "y"},
            {"mod_id": "D", "mod_name": "D", "pin_path": "mods/d.pw.toml", "outcome": "no_candidate", "error": None},
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 0)
        self.assertIn("::warning::B (mods/b.pw.toml)", output)
        self.assertIn("::warning::C (mods/c.pw.toml)", output)

    def test_resolution_failed_is_never_an_expected_gap_now(self):
        # MINECRAFT-362 review, sixteenth round, required item: a prior
        # round's KNOWN_ANONYMOUS_404_MOD_IDS allowlist rested on the
        # false premise that MODRINTH_TOKEN was not configured.
        # MODRINTH_TOKEN IS configured and wired into this workflow, so
        # this module's fetcher is never actually anonymous in a real
        # run -- the allowlist was emptied rather than kept on a false
        # justification. A resolution_failed for ANY mod_id (including
        # the 4 previously-allowlisted ones) must now fail the run.
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": "PZV7RorC", "mod_name": "Dynamic Atmosphere", "pin_path": "mods/dynamic-atmosphere.pw.toml",
             "outcome": "resolution_failed", "error": "404"},
        ]
        exit_code, _ = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 1)

    def test_downgrade_refused_and_dependency_conflict_are_expected_gaps(self):
        # MINECRAFT-362 review, sixth round: both recur identically every
        # run for a given pin until something upstream changes (a newer
        # real release, or the pack's own current pins), the same
        # repeatable-until-external-change shape, so neither must be
        # red-by-construction. `dependency_conflict` briefly had its own
        # per-mod-id allowlist (nineteenth round, after a confirmed false
        # positive); reverted in the twenty-first round once the root
        # cause was fixed at its source (modrinth_compat.
        # find_dependency_conflicts no longer compares a `required`
        # dependency at all) -- it can now only mean the one remaining,
        # genuinely unpredictable-in-advance case (`incompatible`-and-
        # pinned), so any mod_id is "expected" again, same as
        # downgrade_refused.
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": "F", "mod_name": "F", "pin_path": "mods/f.pw.toml", "outcome": "downgrade_refused", "error": "x"},
            {"mod_id": "G", "mod_name": "G", "pin_path": "mods/g.pw.toml", "outcome": "dependency_conflict", "error": "y"},
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 0)
        self.assertIn("::warning::F (mods/f.pw.toml)", output)
        self.assertIn("::warning::G (mods/g.pw.toml)", output)

    def test_dependency_closure_gate_failed_is_an_expected_gap_but_generic_gate_failed_is_not(self):
        # MINECRAFT-362 review, twenty-second round, smaller item:
        # run_gate now gives the dependency_closure gate its own outcome
        # string so it can join dependency_conflict/downgrade_refused's
        # unconditionally-expected bucket, while refresh/check/build
        # failing (generic "gate_failed") stays red-by-construction on
        # purpose -- that trio failing means the build pipeline itself
        # is broken, which must fail a poll run loudly.
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": "H", "mod_name": "H", "pin_path": "mods/h.pw.toml",
             "outcome": "dependency_closure_gate_failed", "error": "refused"},
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 0)
        self.assertIn("::warning::H (mods/h.pw.toml)", output)

        outcomes_with_real_failure = [
            self._HEALTHY_PIN,
            {"mod_id": "I", "mod_name": "I", "pin_path": "mods/i.pw.toml",
             "outcome": "gate_failed", "error": "check gate failed"},
        ]
        exit_code, _ = self._run_main_with(outcomes_with_real_failure)
        self.assertEqual(exit_code, 1)

    def test_runtime_incompatible_is_an_expected_gap(self):
        # MINECRAFT-362 review, twenty-second round: check_runtime_
        # compatibility_or_die's own outcome -- a real candidate jar
        # requiring a newer platform (minecraft/neoforge/etc.) than this
        # pack runs -- is exactly as unpredictable per-candidate as
        # dependency_conflict, so it must not hard-fail a poll run.
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": "J", "mod_name": "J", "pin_path": "mods/j.pw.toml",
             "outcome": "runtime_incompatible", "error": "neoforge too old"},
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 0)
        self.assertIn("::warning::J (mods/j.pw.toml)", output)

    def test_no_parseable_candidate_and_only_prerelease_are_expected_gaps(self):
        # MINECRAFT-362 review, tenth round: a prior round's own fix
        # (modrinth_compat raising a distinct exception instead of
        # silently returning None) was wired to the wrong outcome class
        # here -- it landed as "resolution_failed", which only the 4
        # allowlisted mod_ids escape, so these pins hard-failed the run
        # every single time. Both are deterministic per-pin facts, like
        # downgrade_refused/dependency_conflict above -- but (eleventh
        # round) checked against their OWN static list by mod_id, so use
        # real mod_ids from each dict here.
        no_parseable_mod_id = next(iter(poll_mod_updates.KNOWN_NO_PARSEABLE_CANDIDATE_PIN_IDS))
        only_prerelease_mod_id = next(iter(poll_mod_updates.KNOWN_ONLY_PRERELEASE_PIN_IDS))
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": no_parseable_mod_id, "mod_name": "H", "pin_path": "mods/h.pw.toml", "outcome": "no_parseable_candidate", "error": "x"},
            {"mod_id": only_prerelease_mod_id, "mod_name": "I", "pin_path": "mods/i.pw.toml", "outcome": "only_prerelease_available", "error": "y"},
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 0)
        self.assertIn("::warning::H (mods/h.pw.toml)", output)
        self.assertIn("::warning::I (mods/i.pw.toml)", output)

    def test_unlisted_pin_in_a_gap_class_is_a_coverage_regression(self):
        # MINECRAFT-362 review, eleventh round, item 2: a pin hitting
        # unsupported_version_scheme/no_parseable_candidate/only_
        # prerelease_available WITHOUT being on the matching static list
        # is something this poll has never seen before for that pin --
        # must fail, not blend into the documented baseline.
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": "NOT_LISTED", "mod_name": "J", "pin_path": "mods/j.pw.toml",
             "outcome": "unsupported_version_scheme", "error": "x"},
        ]
        exit_code, _ = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 1)

    def test_a_real_unexpected_failure_still_fails_the_run(self):
        outcomes = [
            self._HEALTHY_PIN,
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
        # Aggregate guard: even if every single examined outcome IS on
        # its matching allowlist, a run where NOTHING else succeeded (no
        # already_pinned/bumped at all) signals a systemic problem
        # (outage, response-shape change), not the documented per-pin
        # baseline. Uses KNOWN_NON_SEMVER_PIN_IDS rather than
        # resolution_failed/KNOWN_ANONYMOUS_404_MOD_IDS -- the latter is
        # now empty (sixteenth review round: MODRINTH_TOKEN is actually
        # configured, so resolution_failed is never an expected gap by
        # itself any more), but the aggregate-guard property under test
        # here is independent of which list is used.
        outcomes = [
            {"mod_id": mod_id, "mod_name": mod_id, "pin_path": f"mods/{mod_id}.pw.toml",
             "outcome": "unsupported_version_scheme", "error": "not semver"}
            for mod_id in poll_mod_updates.KNOWN_NON_SEMVER_PIN_IDS
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 1)
        self.assertIn("::error::no pin in this run reached already_pinned or bumped", output)

    def test_every_pin_only_prerelease_available_fails_the_aggregate_guard(self):
        # MINECRAFT-362 review, eleventh round: a prior round's narrower
        # guard (only triggered by "every pin resolution_failed") missed
        # this shape entirely -- confirmed live, injecting a fetcher
        # where every entry's version_type reads something other than
        # "release" exits 0 under the old guard.
        mod_ids = poll_mod_updates.KNOWN_ONLY_PRERELEASE_PIN_IDS
        outcomes = [
            {"mod_id": mod_id, "mod_name": mod_id, "pin_path": f"mods/{mod_id}.pw.toml",
             "outcome": "only_prerelease_available", "error": "x"}
            for mod_id in mod_ids
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 1)
        self.assertIn("::error::no pin in this run reached already_pinned or bumped", output)

    def test_every_pin_no_candidate_fails_the_aggregate_guard(self):
        # The sharpest shape: no_candidate prints no warning at all (it
        # is the ordinary "nothing newer" case), so a systemic break
        # that makes every pin look like a clean no_candidate (a missing
        # `files` array, a renamed game_versions field) must still be
        # caught -- no_candidate deliberately does not count as
        # "healthy" for this guard, for exactly that reason.
        outcomes = [
            {"mod_id": f"MOD{i}", "mod_name": f"MOD{i}", "pin_path": f"mods/mod{i}.pw.toml",
             "outcome": "no_candidate", "error": None}
            for i in range(5)
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 1)
        self.assertIn("::error::no pin in this run reached already_pinned or bumped", output)

    def test_a_healthy_mix_including_no_candidate_does_not_fail_the_guard(self):
        # The guard must not fire on an ordinary healthy run just
        # because some pins are no_candidate -- only when NOTHING in the
        # run reached already_pinned/bumped.
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": "MOD1", "mod_name": "MOD1", "pin_path": "mods/mod1.pw.toml", "outcome": "no_candidate", "error": None},
        ]
        exit_code, _ = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 0)

    def test_zero_outcomes_fails_the_run(self):
        # MINECRAFT-362 review, twelfth round, required item 1: a prior
        # version of the aggregate guard read `if outcomes and not
        # any(...)`, so an EMPTY outcomes list (mods/ missing or renamed,
        # or discover_eligible_pins returning nothing for every pin)
        # short-circuited the guard entirely and exited 0 with no
        # ::error:: at all. Examining nothing is never a healthy,
        # nothing-to-do run.
        exit_code, output = self._run_main_with([])
        self.assertEqual(exit_code, 1)
        self.assertIn("::error::zero pins were examined this run", output)

    def test_known_untracked_pin_is_an_expected_gap(self):
        # A pin on KNOWN_UNTRACKED_PIN_PATHS (no [update.modrinth] table,
        # a documented, standing gap) must warn, not fail.
        # KNOWN_UNTRACKED_PIN_PATHS is empty on main today (MINECRAFT-362
        # review, twenty-third round: its last 4 real entries all gained
        # a native [update.github] table) -- patch in a throwaway one so
        # this test still exercises the reconciliation mechanism itself,
        # independent of whether anything real currently sits on the list.
        known_untracked_path = "mods/some-future-untracked-pin.pw.toml"
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": None, "mod_name": "Untracked", "pin_path": known_untracked_path,
             "outcome": "untracked", "error": "no [update.modrinth] table; not Modrinth-tracked"},
        ]
        with mock.patch.object(
            poll_mod_updates, "KNOWN_UNTRACKED_PIN_PATHS", {known_untracked_path: "Some Future Untracked Pin"},
        ):
            exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 0)
        self.assertIn(f"::warning::Untracked ({known_untracked_path})", output)

    def test_excluded_local_only_pin_warns_and_keeps_the_run_green(self):
        # MINECRAFT-362 review, thirteenth round, required item 1: an
        # EXCLUDED_MOD_NAMES pin (Herobrine API, Baritone) must warn, not
        # fail -- the whole point of giving it its own outcome kind
        # rather than reusing "untracked" is that its pin_path can NEVER
        # be on a static allowlist in advance (a human names the file),
        # so a path-keyed guard would otherwise fail every run forever
        # the moment one of these pins exists, defeating the exclusion
        # mechanism this ticket itself requires.
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": None, "mod_name": "Baritone", "pin_path": "mods/baritone.pw.toml",
             "outcome": "excluded_local_only", "error": "excluded local-only mod"},
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 0)
        self.assertIn("::warning::Baritone (mods/baritone.pw.toml)", output)

    def test_excluded_pin_path_warns_and_keeps_the_run_green(self):
        # MINECRAFT-362 review, fifteenth round, smaller item: same
        # reasoning as excluded_local_only above, for EXCLUDED_PIN_PATHS.
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": "ALPHA1", "mod_name": "Alpha Mod", "pin_path": "mods/alpha-mod.pw.toml",
             "outcome": "excluded_pin_path", "error": "pin_path explicitly excluded via EXCLUDED_PIN_PATHS"},
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 0)
        self.assertIn("::warning::Alpha Mod (mods/alpha-mod.pw.toml) deliberately excluded from this poll", output)

    def test_unlisted_untracked_pin_is_a_coverage_regression(self):
        # MINECRAFT-362 review, twelfth round, required item 2: a pin
        # that newly loses its [update.modrinth] table (a packwiz format
        # change, or a human editing error) is NOT on
        # KNOWN_UNTRACKED_PIN_PATHS and must fail the run -- this is
        # exactly the "a mod dropping out of enrollment silently" shape
        # item 1's zero-outcomes case is the extreme version of.
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": None, "mod_name": "Newly Untracked", "pin_path": "mods/newly-untracked.pw.toml",
             "outcome": "untracked", "error": "no [update.modrinth] table; not Modrinth-tracked"},
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 1)
        self.assertIn("::error::Newly Untracked (mods/newly-untracked.pw.toml) failed this run", output)

    def test_failed_outcome_names_the_specific_pin(self):
        # MINECRAFT-362 review, twelfth round, smaller item: the
        # per-outcome ::warning:: loop only ever fires for EXPECTED gaps,
        # so a run with one known gap and one genuine, unlisted
        # regression previously annotated only the known gap and said
        # nothing about the pin that actually turned the run red.
        known_only_prerelease_mod_id = next(iter(poll_mod_updates.KNOWN_ONLY_PRERELEASE_PIN_IDS))
        outcomes = [
            self._HEALTHY_PIN,
            {"mod_id": known_only_prerelease_mod_id, "mod_name": "Known Gap", "pin_path": "mods/known.pw.toml",
             "outcome": "only_prerelease_available", "error": "no release build"},
            {"mod_id": "REGRESSED", "mod_name": "Create Aeronautics", "pin_path": "mods/create-aeronautics.pw.toml",
             "outcome": "unsupported_version_scheme", "error": "not semver"},
        ]
        exit_code, output = self._run_main_with(outcomes)
        self.assertEqual(exit_code, 1)
        self.assertIn(
            "::error::Create Aeronautics (mods/create-aeronautics.pw.toml) failed this run: "
            "outcome='unsupported_version_scheme'",
            output,
        )


class TestCommitBump(unittest.TestCase):
    """MINECRAFT-362 review, fifth round: for symmetry with
    scripts/bump_mod.py's cli_commit_bump, commit_bump also commits an
    explicit pathspec rather than a bare `git commit` -- this run's own
    fresh CI checkout has an empty index so there is nothing to
    accidentally sweep in today, but a second implementation that
    forgets this if it is ever copied elsewhere would reintroduce the
    CLI's exact bug."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="commit-bump-"))
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
        _git(self.repo, "add", "-A")

        poll_mod_updates.commit_bump(self.repo, "fix: bump\n\nRelease-Only: true\n", ("pack.toml",))

        committed = _git(self.repo, "diff", "--name-only", "HEAD~1", "HEAD").stdout.split()
        self.assertEqual(committed, ["pack.toml"])
        still_staged = _git(self.repo, "diff", "--cached", "--name-only").stdout.split()
        self.assertEqual(still_staged, ["other.txt"])


if __name__ == "__main__":
    unittest.main()
