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


class ServerConfigSyncTest(unittest.TestCase):
    def test_tuning_schema_accepts_only_bounded_owned_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"integrations": {"createFanTransportPerRpm": 1}}))
            self.assertEqual(
                {("integrations", "createFanTransportPerRpm"): 1.0}, sync.load_tuning(path))
            path.write_text(json.dumps({"integrations": {"other": 1}}))
            with self.assertRaises(ValueError):
                sync.load_tuning(path)
            path.write_text(json.dumps({"integrations": {"createFanTransportPerRpm": 1001}}))
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

    def test_sync_refuses_ambiguous_modern_and_legacy_layouts(self):
        sftp = MemorySftp({
            "/srv/minecraft/server.properties": b"level-name=world\n",
            "/srv/minecraft/config/dynamicatmosphere-server.toml": b"[integrations]\n",
            "/srv/minecraft/world/serverconfig/dynamicatmosphere-server.toml": b"[integrations]\n",
        })
        with self.assertRaises(ValueError):
            sync.synchronize(sftp, "/srv/minecraft", {
                ("integrations", "createFanTransportPerRpm"): 1.0,
            })
        self.assertEqual(0, sftp.renames)

    def test_rejects_unsafe_world_and_server_root(self):
        with self.assertRaises(ValueError):
            sync.level_name("level-name=../world\n")
        with self.assertRaises(ValueError):
            sync.safe_root("/srv/../root")


if __name__ == "__main__":
    unittest.main()
