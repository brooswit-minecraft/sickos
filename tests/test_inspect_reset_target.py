import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    'inspect_reset_target', Path(__file__).parents[1] / 'scripts/inspect-reset-target.py')
inspect_target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspect_target)


class InspectResetTargetTest(unittest.TestCase):
    def test_level_name_and_gamemode_parse(self):
        properties = "level-name=world-old\nmotd=Sickos\ngamemode=creative\n"
        self.assertEqual("world-old", inspect_target.level_name(properties))
        self.assertEqual("creative", inspect_target.gamemode(properties))

    def test_gamemode_missing_is_none(self):
        self.assertIsNone(inspect_target.gamemode("level-name=world-old\n"))

    def test_world_status_distinguishes_absent_present_and_broken(self):
        sftp = Mock()

        def lstat(path):
            if path == '/world-old':
                return types.SimpleNamespace(st_mode=0o40755)
            if path == '/world-not-a-dir':
                return types.SimpleNamespace(st_mode=0o100644)
            raise FileNotFoundError(path)
        sftp.lstat.side_effect = lstat

        def stat_call(path):
            if path == '/world-old/level.dat':
                return types.SimpleNamespace(st_mode=0o100644)
            raise FileNotFoundError(path)
        sftp.stat.side_effect = stat_call

        self.assertEqual("present", inspect_target.world_status(sftp, '/world-old'))
        self.assertEqual("absent", inspect_target.world_status(sftp, '/world-missing'))
        self.assertEqual("exists, not a directory", inspect_target.world_status(sftp, '/world-not-a-dir'))

    def test_world_status_present_directory_without_level_dat(self):
        sftp = Mock()
        sftp.lstat.return_value = types.SimpleNamespace(st_mode=0o40755)
        sftp.stat.side_effect = FileNotFoundError
        self.assertEqual("exists, missing level.dat", inspect_target.world_status(sftp, '/world-empty'))

    def test_inspect_reports_mismatch_and_never_issues_a_mutating_rcon_command(self):
        sftp = Mock()
        sftp.open.return_value.__enter__ = Mock(
            return_value=Mock(read=Mock(return_value=b"level-name=world-live\ngamemode=survival\n")))
        sftp.open.return_value.__exit__ = Mock(return_value=False)
        sftp.lstat.side_effect = FileNotFoundError
        env = {"EXPECTED_WORLD": "world-old", "NEW_WORLD": "world-new", "SERVER_SFTP_PATH": "/"}
        deploy = Mock()
        deploy.rcon_config.return_value = ("host", 1, "secret", 120)
        deploy.rcon_command.return_value = "There are 0 of a max of 20 players online:"
        report = inspect_target.inspect(sftp, env, deploy)
        self.assertEqual("world-live", report["live_level_name"])
        self.assertFalse(report["expected_world_matches_live"])
        self.assertTrue(report["zero_players"])
        self.assertEqual("world-old-backup", report["proposed_backup_path"])
        deploy.rcon_command.assert_called_once_with("host", 1, "secret", "list")


if __name__ == '__main__':
    unittest.main()
