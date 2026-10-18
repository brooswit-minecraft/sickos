import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('reset_world', Path(__file__).parents[1] / 'scripts/reset-server-world.py')
reset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reset)


_DIR_MODE = 0o40755
_FILE_MODE = 0o100644


def _lstat(ready_paths):
    """ready_paths: dir paths get a directory mode; anything ending in
    '/level.dat' gets a regular-file mode; anything else raises."""
    def lstat(path):
        if path not in ready_paths:
            raise FileNotFoundError(path)
        return types.SimpleNamespace(st_mode=_FILE_MODE if path.endswith('/level.dat') else _DIR_MODE)
    return lstat


class ResetWorldTest(unittest.TestCase):
    def test_properties_preserve_other_settings(self):
        original = '# settings\nlevel-name=world-old\nmotd=Sickos\ngamemode=creative\n'
        updated = reset.replace_property(original, 'level-name', 'world-new')
        self.assertEqual('# settings\nlevel-name=world-new\nmotd=Sickos\ngamemode=creative\n', updated)
        with self.assertRaises(ValueError):
            reset.replace_property('level-name=a\nlevel-name=b\n', 'level-name', 'world-new')

    def test_reset_rejects_same_or_unsafe_world_before_io(self):
        for new in ('world-old', '../world-new', 'world-new/nested', 'world-old-backup'):
            sftp = Mock()
            with self.assertRaises(ValueError):
                reset.reset(sftp, {'EXPECTED_WORLD': 'world-old', 'NEW_WORLD': new}, Mock(), Mock())
            self.assertEqual([], sftp.mock_calls)

    def test_reset_preserves_current_gamemode_instead_of_forcing_survival(self):
        env = {'EXPECTED_WORLD': 'world-old', 'NEW_WORLD': 'world-new', 'SERVER_SFTP_PATH': '/'}
        sync = Mock()
        sync.joined.side_effect = lambda root, suffix: '/' + suffix
        sync.level_name.side_effect = ['world-old', 'world-new']
        sftp = Mock()
        sftp.lstat.side_effect = _lstat({'/world-old'})
        sftp.open.return_value.__enter__ = Mock(return_value=Mock())
        sftp.open.return_value.__exit__ = Mock(return_value=False)
        deploy = Mock()
        deploy.rcon_config.return_value = ('host', 1, 'secret', 120)
        deploy.rcon_command.return_value = 'There are 0 of a max of 20 players online:'
        sync.read_remote.side_effect = ['level-name=world-old\ngamemode=creative\n',
                                         'level-name=world-new\ngamemode=creative\n',
                                         'level-name=world-new\ngamemode=creative\n']
        with self.assertRaises(FileNotFoundError):
            # New world's level.dat is never made ready by this fixture, so the
            # function stops right after the properties swap it's verifying here.
            reset.reset(sftp, env, sync, deploy)
        written = sftp.open.return_value.__enter__.return_value.write.call_args[0][0]
        self.assertEqual(b'level-name=world-new\ngamemode=creative\n', written)

    def test_old_world_is_not_deleted_unless_new_world_is_ready(self):
        env = {'EXPECTED_WORLD': 'world-old', 'NEW_WORLD': 'world-new', 'SERVER_SFTP_PATH': '/'}
        sync = Mock()
        sync.joined.side_effect = lambda root, suffix: '/' + suffix
        sync.level_name.side_effect = ['world-old', 'world-new']
        sftp = Mock()
        sftp.lstat.side_effect = _lstat({'/world-old'})
        sftp.open.return_value.__enter__ = Mock(return_value=Mock())
        sftp.open.return_value.__exit__ = Mock(return_value=False)
        deploy = Mock()
        deploy.rcon_config.return_value = ('host', 1, 'secret', 120)
        deploy.rcon_command.return_value = 'There are 0 of a max of 20 players online:'
        sync.read_remote.side_effect = ['level-name=world-old\n', 'level-name=world-new\n',
                                         'level-name=world-new\n']
        with self.assertRaises(FileNotFoundError):
            reset.reset(sftp, env, sync, deploy)
        renames = [call.args for call in sftp.posix_rename.call_args_list]
        self.assertNotIn(('/world-old', '/world-old-backup'), renames)

    def test_reset_refuses_when_a_backup_already_exists(self):
        env = {'EXPECTED_WORLD': 'world-old', 'NEW_WORLD': 'world-new', 'SERVER_SFTP_PATH': '/'}
        sync = Mock()
        sync.joined.side_effect = lambda root, suffix: '/' + suffix
        sync.level_name.return_value = 'world-old'
        sync.read_remote.return_value = 'level-name=world-old\n'
        sftp = Mock()
        sftp.lstat.side_effect = _lstat({'/world-old', '/world-old-backup'})
        deploy = Mock()
        with self.assertRaises(ValueError):
            reset.reset(sftp, env, sync, deploy)
        deploy.rcon_command.assert_not_called()
        deploy.restart_and_wait.assert_not_called()

    def test_old_world_is_untouched_when_restart_fails(self):
        env = {'EXPECTED_WORLD': 'world-old', 'NEW_WORLD': 'world-new', 'SERVER_SFTP_PATH': '/'}
        sync = Mock()
        sync.joined.side_effect = lambda root, suffix: '/' + suffix
        sync.level_name.return_value = 'world-old'
        sftp = Mock()
        sftp.lstat.side_effect = _lstat({'/world-old'})
        sftp.open.return_value.__enter__ = Mock(return_value=Mock())
        sftp.open.return_value.__exit__ = Mock(return_value=False)
        deploy = Mock()
        deploy.rcon_config.return_value = ('host', 1, 'secret', 120)
        deploy.rcon_command.return_value = 'There are 0 of a max of 20 players online:'
        deploy.restart_and_wait.side_effect = TimeoutError('server did not come back up')
        sync.read_remote.side_effect = ['level-name=world-old\n', 'level-name=world-new\n']
        with self.assertRaises(TimeoutError):
            reset.reset(sftp, env, sync, deploy)
        sftp.posix_rename.assert_called_once()  # only the server.properties swap, never a world rename
        self.assertEqual([], sftp.rmdir.mock_calls)
        self.assertEqual([], sftp.remove.mock_calls)

    def test_retry_after_a_prior_successful_backup_fails_safely(self):
        # Simulates re-running the script after a previous run already renamed
        # world-old aside; old_path no longer exists, so this must stop before
        # any rcon/restart action rather than silently touching the backup again.
        env = {'EXPECTED_WORLD': 'world-old', 'NEW_WORLD': 'world-new2', 'SERVER_SFTP_PATH': '/'}
        sync = Mock()
        sync.joined.side_effect = lambda root, suffix: '/' + suffix
        sync.level_name.return_value = 'world-old'
        sync.read_remote.return_value = 'level-name=world-old\n'
        sftp = Mock()
        sftp.lstat.side_effect = _lstat({'/world-old-backup/level.dat'})  # old_path itself absent
        deploy = Mock()
        with self.assertRaises(FileNotFoundError):
            reset.reset(sftp, env, sync, deploy)
        deploy.rcon_command.assert_not_called()
        deploy.restart_and_wait.assert_not_called()
        self.assertEqual([], sftp.posix_rename.mock_calls)

    def test_successful_reset_renames_old_world_aside_instead_of_deleting_it(self):
        env = {'EXPECTED_WORLD': 'world-old', 'NEW_WORLD': 'world-new', 'SERVER_SFTP_PATH': '/'}
        sync = Mock()
        sync.joined.side_effect = lambda root, suffix: '/' + suffix
        sync.level_name.side_effect = ['world-old', 'world-new']
        sftp = Mock()
        sftp.lstat.side_effect = _lstat({'/world-old', '/world-new/level.dat', '/world-old-backup/level.dat'})
        sftp.open.return_value.__enter__ = Mock(return_value=Mock())
        sftp.open.return_value.__exit__ = Mock(return_value=False)
        deploy = Mock()
        deploy.rcon_config.return_value = ('host', 1, 'secret', 120)
        deploy.rcon_command.return_value = 'There are 0 of a max of 20 players online:'
        sync.read_remote.side_effect = ['level-name=world-old\n', 'level-name=world-new\n',
                                         'level-name=world-new\n']
        reset.reset(sftp, env, sync, deploy)
        renames = [call.args for call in sftp.posix_rename.call_args_list]
        self.assertIn(('/world-old', '/world-old-backup'), renames)
        sftp.rmdir.assert_not_called()
        sftp.remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
