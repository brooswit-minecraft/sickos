import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "mod_eligibility.py"
SPEC = importlib.util.spec_from_file_location("mod_eligibility", MODULE_PATH)
mod_eligibility = importlib.util.module_from_spec(SPEC)
sys.modules["mod_eligibility"] = mod_eligibility  # dataclass() needs this registered first
SPEC.loader.exec_module(mod_eligibility)

discover_eligible_pins = mod_eligibility.discover_eligible_pins
discover_github_pins = mod_eligibility.discover_github_pins


MODRINTH_PIN = """\
name = "Dynamic Atmosphere"
filename = "dynamicatmosphere-0.20.3.jar"
side = "both"

[download]
url = "https://cdn.modrinth.com/data/PZV7RorC/versions/PgZ2uZsh/dynamicatmosphere-0.20.3.jar"
hash-format = "sha512"
hash = "{}"

[update]
[update.modrinth]
mod-id = "PZV7RorC"
version = "PgZ2uZsh"
""".format("a" * 128)

CLIENT_SIDE_PIN = """\
name = "Default Options"
filename = "defaultoptions-21.1.8.jar"
side = "client"

[download]
url = "https://cdn.modrinth.com/data/WEg59z5b/versions/1zt17WsC/defaultoptions-21.1.8.jar"
hash-format = "sha512"
hash = "{}"

[update]
[update.modrinth]
mod-id = "WEg59z5b"
version = "1zt17WsC"
""".format("b" * 128)

GITHUB_RELEASE_PIN = """\
name = "Dynamic Despawn"
filename = "dynamicdespawn-0.3.3.jar"
side = "both"

[download]
url = "https://github.com/brooswit-minecraft/dynamic-despawn/releases/download/v0.3.3/dynamicdespawn-0.3.3.jar"
hash-format = "sha512"
hash = "{}"
""".format("c" * 128)

GITHUB_TRACKED_PIN = r"""
name = "Dynamic Despawn"
filename = "dynamicdespawn-0.3.3.jar"
side = "both"

[download]
url = "https://github.com/brooswit-minecraft/dynamic-despawn/releases/download/v0.3.3/dynamicdespawn-0.3.3.jar"
hash-format = "sha256"
hash = "{}"

[update]
[update.github]
slug = "brooswit-minecraft/dynamic-despawn"
tag = "v0.3.3"
branch = "35c4e76ec10ac3558f3a5aba327505c59529f404"
regex = "^.+(?<!-api|-dev|-dev-preshadow|-sources)\\.jar$"
""".format("e" * 64)

EXCLUDED_PIN = """\
name = "Baritone"
filename = "baritone-1.0.0.jar"
side = "client"

[download]
url = "https://cdn.modrinth.com/data/baritone-fake/versions/v1/baritone-1.0.0.jar"
hash-format = "sha512"
hash = "{}"

[update]
[update.modrinth]
mod-id = "baritone-fake"
version = "v1"
""".format("d" * 128)


class TestDiscoverEligiblePins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.mods_dir = self.repo_root / "mods"
        self.mods_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, filename, content):
        (self.mods_dir / filename).write_text(content, encoding="utf-8")

    def test_modrinth_pin_is_eligible(self):
        self.write("dynamic-atmosphere.pw.toml", MODRINTH_PIN)
        eligible, untracked = discover_eligible_pins(self.repo_root)
        self.assertEqual(len(eligible), 1)
        self.assertEqual(untracked, [])
        pin = eligible[0]
        self.assertEqual(pin.mod_id, "PZV7RorC")
        self.assertEqual(pin.pinned_version_id, "PgZ2uZsh")
        self.assertEqual(pin.side, "both")
        self.assertEqual(pin.pin_path, "mods/dynamic-atmosphere.pw.toml")

    def test_preserves_non_both_side(self):
        self.write("default-options.pw.toml", CLIENT_SIDE_PIN)
        eligible, _ = discover_eligible_pins(self.repo_root)
        self.assertEqual(eligible[0].side, "client")

    def test_github_release_only_pin_is_untracked_not_dropped(self):
        # No [update.github] table at all here (GITHUB_RELEASE_PIN) --
        # this is the "neither mechanism knows about it" gap, distinct
        # from the GITHUB_TRACKED_PIN cases below (MINECRAFT-362 review,
        # twenty-second round: these four mods have since gained
        # [update.github] tables -- see test_github_tracked_pin_is_
        # neither_eligible_nor_untracked -- but this pin SHAPE, with no
        # update table at all, must still be reported as a gap if it
        # ever recurs for a fifth mod).
        self.write("dynamic-despawn.pw.toml", GITHUB_RELEASE_PIN)
        eligible, untracked = discover_eligible_pins(self.repo_root)
        self.assertEqual(eligible, [])
        self.assertEqual(len(untracked), 1)
        self.assertIn("untracked", untracked[0].reason)

    def test_github_tracked_pin_is_neither_eligible_nor_untracked(self):
        # MINECRAFT-362 review, twenty-second round: an [update.github]
        # pin is tracked (by discover_github_pins, not this function),
        # so discover_eligible_pins must not report it as a gap any more.
        self.write("dynamic-despawn.pw.toml", GITHUB_TRACKED_PIN)
        eligible, untracked = discover_eligible_pins(self.repo_root)
        self.assertEqual(eligible, [])
        self.assertEqual(untracked, [])

    def test_discover_github_pins(self):
        self.write("dynamic-despawn.pw.toml", GITHUB_TRACKED_PIN)
        github_pins = discover_github_pins(self.repo_root)
        self.assertEqual(len(github_pins), 1)
        pin = github_pins[0]
        self.assertEqual(pin.name, "Dynamic Despawn")
        self.assertEqual(pin.slug, "brooswit-minecraft/dynamic-despawn")
        self.assertEqual(pin.pinned_tag, "v0.3.3")
        self.assertEqual(pin.pinned_source, "35c4e76ec10ac3558f3a5aba327505c59529f404")
        self.assertIn(r"\.jar$", pin.regex)
        self.assertEqual(pin.side, "both")
        self.assertEqual(pin.pin_path, "mods/dynamic-despawn.pw.toml")

    def test_discover_github_pins_ignores_modrinth_and_untracked_pins(self):
        self.write("dynamic-atmosphere.pw.toml", MODRINTH_PIN)
        self.write("dynamic-despawn.pw.toml", GITHUB_RELEASE_PIN)
        self.assertEqual(discover_github_pins(self.repo_root), [])

    def test_discover_github_pins_respects_excluded_mod_names(self):
        excluded_github_pin = GITHUB_TRACKED_PIN.replace("Dynamic Despawn", "Baritone")
        self.write("baritone.pw.toml", excluded_github_pin)
        self.assertEqual(discover_github_pins(self.repo_root), [])

    def test_excluded_local_only_name_is_never_eligible_even_if_pinned(self):
        self.write("baritone.pw.toml", EXCLUDED_PIN)
        eligible, untracked = discover_eligible_pins(self.repo_root)
        self.assertEqual(eligible, [])
        self.assertEqual(len(untracked), 1)
        self.assertIn("excluded", untracked[0].reason)

    def test_mixed_set(self):
        self.write("dynamic-atmosphere.pw.toml", MODRINTH_PIN)
        self.write("default-options.pw.toml", CLIENT_SIDE_PIN)
        self.write("magicavoxel-loader.pw.toml", GITHUB_RELEASE_PIN.replace("Dynamic Despawn", "MagicaVoxel Loader"))
        self.write("dynamic-despawn.pw.toml", GITHUB_TRACKED_PIN)
        self.write("baritone.pw.toml", EXCLUDED_PIN)
        eligible, untracked = discover_eligible_pins(self.repo_root)
        self.assertEqual({p.name for p in eligible}, {"Dynamic Atmosphere", "Default Options"})
        self.assertEqual({p.name for p in untracked}, {"MagicaVoxel Loader", "Baritone"})
        self.assertEqual({p.name for p in discover_github_pins(self.repo_root)}, {"Dynamic Despawn"})

    def test_empty_mods_dir(self):
        eligible, untracked = discover_eligible_pins(self.repo_root)
        self.assertEqual(eligible, [])
        self.assertEqual(untracked, [])


class TestReadPackTargets(unittest.TestCase):
    """Moved here from scripts/poll-mod-updates.py (MINECRAFT-362 review,
    fourth round) so scripts/bump_mod.py's CLI can share the exact same
    compatibility target instead of a second implementation that could
    drift."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_pack_toml(self, versions_body):
        (self.repo_root / "pack.toml").write_text(
            'name = "Sickos"\nversion = "0.22.0"\n\n[versions]\n' + versions_body,
            encoding="utf-8",
        )

    def test_reads_minecraft_and_the_one_loader_key(self):
        self._write_pack_toml('minecraft = "1.21.1"\nneoforge = "21.1.250"\n')
        self.assertEqual(
            mod_eligibility.read_pack_targets(self.repo_root), ("1.21.1", "neoforge"),
        )

    def test_missing_minecraft_key_raises(self):
        self._write_pack_toml('neoforge = "21.1.250"\n')
        with self.assertRaises(ValueError):
            mod_eligibility.read_pack_targets(self.repo_root)

    def test_no_loader_key_raises(self):
        self._write_pack_toml('minecraft = "1.21.1"\n')
        with self.assertRaises(ValueError):
            mod_eligibility.read_pack_targets(self.repo_root)

    def test_two_loader_keys_raises(self):
        self._write_pack_toml('minecraft = "1.21.1"\nneoforge = "21.1.250"\nfabric = "0.15.0"\n')
        with self.assertRaises(ValueError):
            mod_eligibility.read_pack_targets(self.repo_root)


if __name__ == "__main__":
    unittest.main()
