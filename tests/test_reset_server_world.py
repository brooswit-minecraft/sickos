import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('reset_world', Path(__file__).parents[1] / 'scripts/reset-server-world.py')
reset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reset)


class ResetWorldTest(unittest.TestCase):
    def test_properties_preserve_other_settings(self):
        original = '# settings\nlevel-name=world-old\nmotd=Sickos\ngamemode=creative\n'
        updated = reset.replace_property(reset.replace_property(original, 'level-name', 'world-new'), 'gamemode', 'survival')
        self.assertEqual('# settings\nlevel-name=world-new\nmotd=Sickos\ngamemode=survival\n', updated)
        with self.assertRaises(ValueError):
            reset.replace_property('level-name=a\nlevel-name=b\n', 'level-name', 'world-new')

    def test_reset_rejects_same_or_unsafe_world_before_io(self):
        for new in ('world-old', '../world-new', 'world-new/nested'):
            sftp = Mock()
            with self.assertRaises(ValueError):
                reset.reset(sftp, {'EXPECTED_WORLD': 'world-old', 'NEW_WORLD': new}, Mock(), Mock())
            self.assertEqual([], sftp.mock_calls)

    def test_old_world_is_not_deleted_unless_new_world_is_ready(self):
        env = {'EXPECTED_WORLD': 'world-old', 'NEW_WORLD': 'world-new', 'SERVER_SFTP_PATH': '/'}
        sync = Mock()
        sync.joined.side_effect = lambda root, suffix: '/' + suffix
        sync.read_remote.return_value = 'level-name=world-old\n'
        sync.level_name.side_effect = ['world-old', 'world-new']
        sftp = Mock()
        def lstat(path):
            if path == '/world-old':
                return types.SimpleNamespace(st_mode=0o40755)
            raise FileNotFoundError(path)
        sftp.lstat.side_effect = lstat
        sftp.open.return_value.__enter__ = Mock(return_value=Mock())
        sftp.open.return_value.__exit__ = Mock(return_value=False)
        deploy = Mock()
        deploy.rcon_config.return_value = ('host', 1, 'secret', 120)
        deploy.rcon_command.return_value = 'There are 0 of a max of 20 players online:'
        sync.read_remote.side_effect = ['level-name=world-old\n', 'level-name=world-new\ngamemode=survival\n',
                                        'level-name=world-new\ngamemode=survival\n']
        with patch.object(reset, 'remove_tree') as remove:
            with self.assertRaises(FileNotFoundError):
                reset.reset(sftp, env, sync, deploy)
            remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
