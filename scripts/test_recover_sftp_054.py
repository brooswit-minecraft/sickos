import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import recover_sftp_054 as recovery
spec = importlib.util.spec_from_file_location("deploy_tests", ".schematic-tools/tests/test_server_deploy.py")
test_helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(test_helpers)


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "mods").mkdir()
        self.sftp = test_helpers.FakeSFTP(self.root)
        self.stage = ".schematic-stage-" + "a" * 32
        (self.root / self.stage).mkdir()
        (self.root / self.stage / "partial.jar").write_bytes(b"partial")
        (self.root / recovery.d.PENDING).write_text("/" + self.stage)
        self.old = {"schema": 1, "version": "0.53.1", "files": {}}
        self.new = {"schema": 1, "version": "0.54.0", "files": {}}
        for path in recovery.MISSING:
            self.old["files"][path] = hashlib.sha512(b"old").hexdigest()
        for path in recovery.UNTRACKED | {"mods/common.jar"}:
            (self.root / path).write_bytes(b"known")
            self.new["files"][path] = hashlib.sha512(b"known").hexdigest()
        self.old["files"]["mods/common.jar"] = self.new["files"]["mods/common.jar"]
        (self.root / recovery.d.MANIFEST).write_text(json.dumps(self.old))
        (self.root / "world").mkdir()
        (self.root / "world/level.dat").write_bytes(b"world must survive")

    def test_preserves_incident_evidence_and_world_without_changing_mods(self):
        recovery.recover(self.sftp, "/", self.new)
        backup = self.root / "backups/incident-38402755903-deployment"
        self.assertEqual((backup / "stage/partial.jar").read_bytes(), b"partial")
        self.assertEqual((backup / "pending-marker").read_text(), "/" + self.stage)
        self.assertEqual(json.loads((backup / "manifest.json").read_text()), self.old)
        self.assertEqual((self.root / "world/level.dat").read_bytes(), b"world must survive")
        for path in self.new["files"]:
            self.assertEqual((self.root / path).read_bytes(), b"known")
        self.assertFalse((self.root / recovery.d.PENDING).exists())

    def test_modified_managed_file_aborts_before_any_mutation(self):
        (self.root / "mods/common.jar").write_bytes(b"operator changed")
        with self.assertRaises(ValueError):
            recovery.recover(self.sftp, "/", self.new)
        self.assertFalse((self.root / "backups").exists())
        self.assertTrue((self.root / recovery.d.PENDING).exists())

    def test_unknown_mod_aborts_before_any_mutation(self):
        (self.root / "mods/unexpected.jar").write_bytes(b"unknown")
        with self.assertRaises(ValueError):
            recovery.recover(self.sftp, "/", self.new)
        self.assertFalse((self.root / "backups").exists())

    def test_marker_cannot_point_at_world(self):
        (self.root / recovery.d.PENDING).write_text("/world")
        with self.assertRaises(ValueError):
            recovery.recover(self.sftp, "/", self.new)
        self.assertFalse((self.root / "backups").exists())


if __name__ == "__main__":
    unittest.main()
