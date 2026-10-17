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
        self.write("dynamic-despawn.pw.toml", GITHUB_RELEASE_PIN)
        eligible, untracked = discover_eligible_pins(self.repo_root)
        self.assertEqual(eligible, [])
        self.assertEqual(len(untracked), 1)
        self.assertIn("not Modrinth-tracked", untracked[0].reason)

    def test_excluded_local_only_name_is_never_eligible_even_if_pinned(self):
        self.write("baritone.pw.toml", EXCLUDED_PIN)
        eligible, untracked = discover_eligible_pins(self.repo_root)
        self.assertEqual(eligible, [])
        self.assertEqual(len(untracked), 1)
        self.assertIn("excluded", untracked[0].reason)

    def test_mixed_set(self):
        self.write("dynamic-atmosphere.pw.toml", MODRINTH_PIN)
        self.write("default-options.pw.toml", CLIENT_SIDE_PIN)
        self.write("dynamic-despawn.pw.toml", GITHUB_RELEASE_PIN)
        self.write("baritone.pw.toml", EXCLUDED_PIN)
        eligible, untracked = discover_eligible_pins(self.repo_root)
        self.assertEqual({p.name for p in eligible}, {"Dynamic Atmosphere", "Default Options"})
        self.assertEqual({p.name for p in untracked}, {"Dynamic Despawn", "Baritone"})

    def test_empty_mods_dir(self):
        eligible, untracked = discover_eligible_pins(self.repo_root)
        self.assertEqual(eligible, [])
        self.assertEqual(untracked, [])


if __name__ == "__main__":
    unittest.main()
