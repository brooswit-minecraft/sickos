import importlib.util
import ast
import io
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest

SPEC = importlib.util.spec_from_file_location(
    "inspect_backups", Path(__file__).resolve().parents[1] / "scripts/inspect-backups.py")
inspect_backups = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspect_backups)


def attr(name, mode=stat.S_IFREG, size=42):
    return SimpleNamespace(filename=name, st_mode=mode, st_size=size)


class ReadOnlySFTP:
    # No mutation, recursive reads, RCON or shell API is available.
    def __init__(self):
        self.backup = attr("backups", stat.S_IFDIR)
        self.entries = [attr("old.tar.gz"), attr(".schematic-backup-stage-part", size=3),
                        attr("link", stat.S_IFLNK), attr("nested", stat.S_IFDIR)]
        self.properties = b"level-name=current\nsecret=do-not-report\n"
        self.calls = []

    def lstat(self, path):
        self.calls.append(("lstat", path))
        if path.endswith("/backups"):
            if isinstance(self.backup, Exception):
                raise self.backup
            return self.backup
        if path.endswith("/current"):
            return attr("current", stat.S_IFDIR)
        raise FileNotFoundError(path)

    def listdir_attr(self, path):
        self.calls.append(("listdir_attr", path))
        return self.entries

    def open(self, path, mode):
        assert path == "/server/server.properties" and mode == "rb"
        self.calls.append(("open", path, mode))
        return io.BytesIO(self.properties)


class InventoryTests(unittest.TestCase):
    def test_remote_api_allowlist(self):
        tree = ast.parse(Path(SPEC.origin).read_text())
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == "sftp"}
        self.assertEqual(calls, {"lstat", "listdir_attr", "open"})
        deploy_calls = {node.func.attr for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "deploy"}
        self.assertEqual(deploy_calls, {"sftp_session"})

    def test_all_entries_and_worlds_read_only(self):
        sftp = ReadOnlySFTP()
        result = inspect_backups.inspect(sftp, "/server/")
        backup = result["backups"]
        self.assertEqual(backup["entry_count"], 4)
        self.assertEqual(backup["entries"][0], {
            "name": ".schematic-backup-stage-part", "type": "file",
            "size_bytes": 3, "backup_stage": True})
        self.assertEqual([e["type"] for e in backup["entries"]],
                         ["file", "symlink", "directory", "file"])
        self.assertEqual(len(result["world_locations"]), 5)
        self.assertEqual(result["world_locations"][0]["type"], "directory")
        self.assertEqual(result["world_locations"][1]["type"], "absent")
        self.assertNotIn("do-not-report", str(result))
        self.assertEqual(sum(c[0] == "listdir_attr" for c in sftp.calls), 1)
        self.assertEqual(sum(c[0] == "open" for c in sftp.calls), 1)

    def test_absent_symlink_and_non_directory_are_not_listed(self):
        for backup, expected in ((FileNotFoundError(), "absent"),
                                 (attr("backups", stat.S_IFLNK), "symlink"),
                                 (attr("backups"), "file")):
            with self.subTest(expected=expected):
                sftp = ReadOnlySFTP()
                sftp.backup = backup
                result = inspect_backups.inspect(sftp, "/server")["backups"]
                self.assertEqual(result["type"], expected)
                self.assertIsNone(result["entry_count"])
                self.assertFalse(any(c[0] == "listdir_attr" for c in sftp.calls))

    def test_permission_error_is_not_absent(self):
        sftp = ReadOnlySFTP()
        sftp.backup = PermissionError("sensitive details")
        with self.assertRaises(PermissionError):
            inspect_backups.inspect(sftp, "/server")

    def test_empty_directory(self):
        sftp = ReadOnlySFTP()
        sftp.entries = []
        self.assertEqual(inspect_backups.inspect(sftp, "/server")["backups"]["entry_count"], 0)

    def test_invalid_root_rejected_before_io(self):
        for root in ("relative", "/a/../b", "/a/./b", "/a\\b", "/a\nb"):
            sftp = ReadOnlySFTP()
            with self.assertRaises(ValueError):
                inspect_backups.inspect(sftp, root)
            self.assertEqual(sftp.calls, [])

    def test_invalid_or_oversized_properties_fail(self):
        for properties in (b"", b"level-name=../escape\n", b"level-name=a\nlevel-name=b\n",
                           b"x" * (1024 * 1024 + 1)):
            sftp = ReadOnlySFTP()
            sftp.properties = properties
            with self.assertRaises(ValueError):
                inspect_backups.inspect(sftp, "/server")


if __name__ == "__main__":
    unittest.main()
