import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import reset_approved_world as resetter
spec = importlib.util.spec_from_file_location("helpers", ".schematic-tools/tests/test_server_deploy.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


class SFTP(helpers.FakeSFTP):
    def open(self, path, mode):
        handle = super().open(path, mode)
        handle.stat = lambda: self.path(path).stat()
        return handle


class ResetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sftp = SFTP(self.root)
        self.world = self.root / resetter.LEVEL
        (self.world / "DIM1").mkdir(parents=True)
        (self.world / "DIM1/region.dat").write_bytes(b"world")
        (self.root / "server.properties").write_text("level-name=" + resetter.LEVEL + "\n")
        (self.root / resetter.d.MANIFEST).write_text(json.dumps({"schema": 1, "version": "0.54.0", "files": {}}))
        (self.root / "logs").mkdir()
        (self.root / "logs/latest.log").write_bytes(b"Done (10s)\nThread RCON Listener stopped\n")
        (self.root / "outside").mkdir()
        (self.root / "outside/keep").write_bytes(b"preserve")
        self.env = {"SERVER_SFTP_PATH": "/", "SERVER_RCON_HOST": "example.invalid"}

    def test_deletes_only_exact_world_without_following_symlink(self):
        (self.world / "outside-link").symlink_to(self.root / "outside", target_is_directory=True)
        with mock.patch.object(resetter.socket, "create_connection", side_effect=ConnectionRefusedError):
            resetter.reset(self.sftp, self.env)
        self.assertFalse(self.world.exists())
        self.assertEqual((self.root / "outside/keep").read_bytes(), b"preserve")
        self.assertFalse((self.root / "backups").exists())

    def test_running_server_aborts_without_deletion(self):
        with mock.patch.object(resetter.socket, "create_connection", return_value=mock.Mock()):
            with self.assertRaises(ValueError):
                resetter.reset(self.sftp, self.env)
        self.assertTrue((self.world / "DIM1/region.dat").exists())

    def test_different_active_world_aborts(self):
        (self.root / "server.properties").write_text("level-name=other\n")
        with self.assertRaises(ValueError):
            resetter.reset(self.sftp, self.env)
        self.assertTrue(self.world.exists())

    def test_new_start_after_shutdown_aborts(self):
        (self.root / "logs/latest.log").write_bytes(b"Thread RCON Listener stopped\nPreparing level\n")
        with self.assertRaises(ValueError):
            resetter.reset(self.sftp, self.env)
        self.assertTrue(self.world.exists())


if __name__ == "__main__":
    unittest.main()
