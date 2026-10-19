import importlib.util
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "sync-server-config.py"
SPEC = importlib.util.spec_from_file_location("sync_server_config", MODULE_PATH)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class MemorySftp:
    def __init__(self, files):
        self.files = {path: bytes(content) for path, content in files.items()}
        self.renames = 0

    def open(self, path, mode):
        if mode == "rb":
            return io.BytesIO(self.files[path])
        if mode == "wb":
            stream = io.BytesIO()
            original_close = stream.close
            stream.close = lambda: (self.files.__setitem__(path, stream.getvalue()), original_close())[1]
            return stream
        raise AssertionError(mode)

    def posix_rename(self, source, target):
        self.renames += 1
        self.files[target] = self.files.pop(source)

    def remove(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    def stat(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o600)


def full_valid_data():
    """A complete server-config.json shaped object covering every ALLOWED field, at
    DA's own default for each (so it round-trips through load_tuning unchanged)."""
    data = {}
    for (section, key), (_minimum, _maximum, _kind, default) in sync.ALLOWED.items():
        data.setdefault(section, {})[key] = default
    return data


class ServerConfigSyncTest(unittest.TestCase):
    def test_tuning_schema_accepts_only_bounded_owned_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            data = full_valid_data()
            path.write_text(json.dumps(data))
            expected = {
                field: (value if kind is int else float(value))
                for field, (_minimum, _maximum, kind, value) in sync.ALLOWED.items()
            }
            self.assertEqual(expected, sync.load_tuning(path))

            for field, (minimum, maximum, kind, _default) in sync.ALLOWED.items():
                section, key = field
                original = data[section][key]
                invalid_values = [minimum - 1 if kind is int else minimum - 1.0,
                                   maximum + 1 if kind is int else maximum + 1.0,
                                   True]
                if kind is int:
                    invalid_values.append(original + 0.5)
                for invalid in invalid_values:
                    data[section][key] = invalid
                    path.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):
                        sync.load_tuning(path)
                data[section][key] = original

            path.write_text(json.dumps({"integrations": {"other": 1}}))
            with self.assertRaises(ValueError):
                sync.load_tuning(path)

            incomplete = full_valid_data()
            del incomplete["runtime"]["simulationSkipChance"]
            path.write_text(json.dumps(incomplete))
            with self.assertRaises(ValueError):
                sync.load_tuning(path)

            client_side = full_valid_data()
            client_side.setdefault("smoke", {})["smokeOpticalDensity"] = 1.0
            path.write_text(json.dumps(client_side))
            with self.assertRaises(ValueError):
                sync.load_tuning(path)

    def test_patch_preserves_comments_and_unowned_values(self):
        original = "# keep me\n[runtime]\nsimulationIntervalTicks = 200 # cadence\n\n[integrations]\ncreateFanTransportPerRpm = 0.1 # old\n"
        updated = sync.patch_toml(
            original, {("integrations", "createFanTransportPerRpm"): 1.0})
        self.assertIn("# keep me", updated)
        self.assertIn("simulationIntervalTicks = 200 # cadence", updated)
        self.assertIn("createFanTransportPerRpm = 1.0 # old", updated)

    def test_sync_supports_legacy_world_serverconfig_layout(self):
        target = "/srv/minecraft/world-two/serverconfig/dynamicatmosphere-server.toml"
        sftp = MemorySftp({
            "/srv/minecraft/server.properties": b"motd=Sickos\nlevel-name=world-two\n",
            target: b"[runtime]\nsimulationIntervalTicks = 200\n[integrations]\ncreateFanTransportPerRpm = 0.1\n",
        })
        sync.synchronize(sftp, "/srv/minecraft", {
            ("integrations", "createFanTransportPerRpm"): 1.0,
        })
        self.assertIn(b"createFanTransportPerRpm = 1.0", sftp.files[target])
        self.assertFalse(any(".tmp-" in path for path in sftp.files))
        self.assertEqual(1, sftp.renames)
        sync.synchronize(sftp, "/srv/minecraft", {
            ("integrations", "createFanTransportPerRpm"): 1.0,
        })
        self.assertEqual(1, sftp.renames)

    def test_sync_supports_modern_root_config_layout(self):
        target = "/srv/minecraft/config/dynamicatmosphere-server.toml"
        sftp = MemorySftp({
            "/srv/minecraft/server.properties": b"level-name=world\n",
            target: b"[integrations]\ncreateFanTransportPerRpm = 0.1\n",
        })
        sync.synchronize(sftp, "/srv/minecraft", {
            ("integrations", "createFanTransportPerRpm"): 1.0,
        })
        self.assertIn(b"createFanTransportPerRpm = 1.0", sftp.files[target])

    def test_sync_prefers_the_per_world_override_whenever_it_exists(self):
        # NeoForge's own SERVER-type ModConfig loading (DynamicAtmosphereMod's
        # own documented behavior, confirmed there by decompiling NeoForge
        # 21.1's ServerLifecycleHooks.class) actively loads the per-world
        # serverconfig/ file once it exists, not the global config/ copy --
        # this is not an ambiguity to refuse, it's the ordinary, expected
        # state for any world that has ever had DA's config version checked.
        global_path = "/srv/minecraft/config/dynamicatmosphere-server.toml"
        world_path = "/srv/minecraft/world/serverconfig/dynamicatmosphere-server.toml"
        sftp = MemorySftp({
            "/srv/minecraft/server.properties": b"level-name=world\n",
            global_path: b"[integrations]\ncreateFanTransportPerRpm = 0.2\n",
            world_path: b"[integrations]\ncreateFanTransportPerRpm = 0.1\n",
        })
        sync.synchronize(sftp, "/srv/minecraft", {
            ("integrations", "createFanTransportPerRpm"): 1.0,
        })
        self.assertIn(b"createFanTransportPerRpm = 1.0", sftp.files[world_path])
        self.assertIn(b"createFanTransportPerRpm = 0.2", sftp.files[global_path])  # left untouched

    def test_sync_patches_a_fresh_worlds_bare_version_stub_override(self):
        # Observed live on MINECRAFT-378 immediately after a world reset: the
        # brand-new world's per-world override existed as only
        # `configVersion = 1` (DA writes exactly that, and nothing else, the
        # first time a world is loaded, before the file is ever populated).
        # That stub is still the active override NeoForge will read for this
        # world -- patch_toml() filling in the managed sections/keys it's
        # missing is the correct outcome, not a special case.
        global_path = "/srv/minecraft/config/dynamicatmosphere-server.toml"
        stub_path = "/srv/minecraft/world-new/serverconfig/dynamicatmosphere-server.toml"
        sftp = MemorySftp({
            "/srv/minecraft/server.properties": b"level-name=world-new\n",
            global_path: b"[integrations]\ncreateFanTransportPerRpm = 0.1\n",
            stub_path: b"configVersion = 1\n",
        })
        sync.synchronize(sftp, "/srv/minecraft", {
            ("integrations", "createFanTransportPerRpm"): 1.0,
        })
        self.assertIn(b"createFanTransportPerRpm = 1.0", sftp.files[stub_path])
        self.assertIn(b"configVersion = 1", sftp.files[stub_path])
        self.assertEqual(b"[integrations]\ncreateFanTransportPerRpm = 0.1\n", sftp.files[global_path])

    def test_rejects_unsafe_world_and_server_root(self):
        with self.assertRaises(ValueError):
            sync.level_name("level-name=../world\n")
        with self.assertRaises(ValueError):
            sync.safe_root("/srv/../root")

    def test_regenerated_fields_detects_value_sitting_at_da_default(self):
        original = "[obsidianPowder]\nportalBlockEmission = 100\n"
        found = sync.regenerated_fields(
            original, {("obsidianPowder", "portalBlockEmission"): 250})
        self.assertEqual([("obsidianPowder", "portalBlockEmission", 100, 250)], found)

    def test_regenerated_fields_ignores_a_field_already_at_the_tuned_value(self):
        original = "[obsidianPowder]\nportalBlockEmission = 250\n"
        found = sync.regenerated_fields(
            original, {("obsidianPowder", "portalBlockEmission"): 250})
        self.assertEqual([], found)

    def test_regenerated_fields_treats_a_missing_section_as_default(self):
        found = sync.regenerated_fields(
            "", {("obsidianPowder", "portalBlockEmission"): 250})
        self.assertEqual([("obsidianPowder", "portalBlockEmission", 100, 250)], found)

    def test_sync_reports_regeneration_in_job_summary_and_still_patches(self):
        target = "/srv/minecraft/config/dynamicatmosphere-server.toml"
        sftp = MemorySftp({
            "/srv/minecraft/server.properties": b"level-name=world\n",
            target: b"[obsidianPowder]\nportalBlockEmission = 100\n",
        })
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory) / "summary.md"
            summary_path.write_text("")
            regenerated = sync.synchronize(sftp, "/srv/minecraft", {
                ("obsidianPowder", "portalBlockEmission"): 250,
            })
            sync.report_regeneration(regenerated, {"GITHUB_STEP_SUMMARY": str(summary_path)})
            self.assertIn(b"portalBlockEmission = 250", sftp.files[target])
            self.assertIn("DA config regeneration detected", summary_path.read_text())
            self.assertIn("portalBlockEmission", summary_path.read_text())


if __name__ == "__main__":
    unittest.main()
