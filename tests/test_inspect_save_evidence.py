import contextlib
import importlib.util
import io
import logging
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('evidence', ROOT / 'scripts/inspect-save-evidence.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def line(stamp='2026-10-21T21:49:40Z', message='[Rcon: Automatic saving is now enabled]'):
    return f'[{stamp}] [Server thread/INFO] [minecraft/MinecraftServer]: {message}\n'.encode()


class Remote:
    # A capability-limited fake: every operation other than these fails.
    def __init__(self, data):
        self.data = data
        self.calls = []
        self.offset = 0
        self.mode = stat.S_IFREG
        self.changed = False
        self.read_done = False

    def attr(self):
        return SimpleNamespace(st_mode=self.mode, st_size=len(self.data),
                               st_mtime=2 if self.changed and self.read_done else 1)

    def lstat(self, path):
        self.calls.append(('lstat', path))
        if path in ('/server', '/server/logs'):
            return SimpleNamespace(st_mode=stat.S_IFDIR)
        assert path == '/server/logs/latest.log'
        return self.attr()

    def open(self, path, mode):
        self.calls.append(('open', path, mode))
        assert (path, mode) == ('/server/logs/latest.log', 'rb')
        return self

    def stat(self):
        self.calls.append(('stat',))
        return self.attr()

    def seek(self, offset):
        self.calls.append(('seek', offset))
        self.offset = offset

    def read(self, size):
        self.calls.append(('read', size))
        assert 0 <= size <= m.LIMIT
        self.read_done = True
        return self.data[self.offset:self.offset + size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class EvidenceTests(unittest.TestCase):
    def test_read_bound_and_no_mutations(self):
        remote = Remote(b'x' * (m.LIMIT + 100) + b'\n' + line())
        self.assertEqual(m.read_tail(remote, '/server/'), line())
        self.assertEqual([c for c in remote.calls if c[0] == 'read'], [('read', m.LIMIT)])
        self.assertIn(('seek', len(remote.data) - m.LIMIT), remote.calls)
        self.assertEqual([c for c in remote.calls if c[0] == 'open'],
                         [('open', '/server/logs/latest.log', 'rb')])

    def test_small_empty_and_exact_limit(self):
        for data in (b'', line(), b'x' * m.LIMIT):
            remote = Remote(data)
            m.read_tail(remote, '/server')
            self.assertEqual([c for c in remote.calls if c[0] == 'read'], [('read', len(data))])

    def test_partial_lines_are_discarded(self):
        self.assertEqual(m.read_tail(Remote(line().rstrip(b'\n')), '/server'), b'')
        data = b'x' * 200 + line() + b'x' * (m.LIMIT - len(line()))
        self.assertEqual(m.read_tail(Remote(data), '/server'), b'')

    def test_changed_or_short_read_fails(self):
        remote = Remote(line())
        remote.changed = True
        with self.assertRaises(ValueError):
            m.read_tail(remote, '/server')
        remote = Remote(line())
        with patch.object(remote, 'read', return_value=b''):
            with self.assertRaises(ValueError):
                m.read_tail(remote, '/server')

    def test_invalid_paths_and_symlinks_fail_before_open(self):
        for root in ('relative', '/server/../other', '/server\n', '/server\\other'):
            remote = Remote(line())
            with self.assertRaises(ValueError):
                m.read_tail(remote, root)
            self.assertEqual(remote.calls, [])
        for mode in (stat.S_IFLNK, stat.S_IFDIR):
            remote = Remote(line())
            remote.mode = mode
            with self.assertRaises(ValueError):
                m.read_tail(remote, '/server')
            self.assertFalse(any(c[0] == 'open' for c in remote.calls))
        remote = Remote(line())
        with patch.object(remote, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFLNK)):
            with self.assertRaises(ValueError):
                m.read_tail(remote, '/server')

    def test_inclusive_seconds_and_explicit_utc_only(self):
        for second in range(60):
            events, _, success = m.classify(line(f'2026-10-21T21:49:{second:02}Z'))
            self.assertEqual(success, 30 <= second <= 50)
            self.assertEqual(bool(events), success)
        self.assertTrue(m.classify(line('2026-10-21 21:49:30+00:00'))[2])
        for stamp in ('2026-10-20T21:49:40Z', '2026-10-22T21:49:40Z',
                      '2025-10-21T21:49:40Z', '2026-10-21T21:49:40+01:00',
                      '2026-10-21T21:49:40', '2026-10-21T21:49:40.999Z',
                      '2026-10-21T21:48:40Z', '2026-10-21T21:49:60Z'):
            self.assertEqual(m.classify(line(stamp))[0], [])
            self.assertFalse(m.classify(line(stamp))[2])

    def test_clock_only_and_conflicting_evidence_fail_closed(self):
        self.assertEqual(m.classify(line('21:49:40')),
                         ([], 'save-evidence: unconfirmed-date-time', False))
        self.assertFalse(m.classify(line() + line('21:49:40'))[2])
        for message in ('Automatic saving is now disabled', 'Saving is already turned off'):
            self.assertFalse(m.classify(line() + line(message=message))[2])
        self.assertFalse(m.classify(line(message='Saved the game'))[2])
        self.assertFalse(m.classify(b'')[2])

    def test_untrusted_malformed_and_secret_lines_not_emitted(self):
        malicious = [
            line(message='<PlayerName> Automatic saving is now enabled'),
            line(message='[PlayerName: Automatic saving is now enabled]'),
            line(message='RCON running on 192.0.2.1:1234'),
            line(message='token=TOPSECRET'),
            line(message='Automatic saving is now enabled trailing'),
            b'\x1b[31m' + line(), b'prefix ' + line(), line().replace(b'INFO', b'WARN'),
            line().replace(b'Server thread', b'Async Chat Thread'),
            line().replace(b'minecraft/MinecraftServer', b'untrusted/logger'),
            line().replace(b'enabled', b'enabled\x00'),
            line().replace(b'enabled', b'enabled\xff'),
        ]
        for raw in malicious:
            self.assertEqual(m.classify(raw)[0], [])
            self.assertFalse(m.classify(raw)[2])
        events, _, success = m.classify(b''.join(malicious) + line() * 500)
        self.assertTrue(success)
        self.assertEqual(events, [('2026-10-21T21:49:40Z', 'rcon-save-enabled')])

    def run_main(self, helper, env=None, argv=None):
        output, errors = io.StringIO(), io.StringIO()
        settings = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                    'SERVER_SFTP_PATH': '/server'} if env is None else env
        previous = logging.root.manager.disable
        try:
            with patch.dict(m.os.environ, settings, clear=True), patch.object(m.sys, 'argv', argv or ['script']), \
                    patch.object(m, 'load_helper', return_value=helper) as load, \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result = m.main()
                calls = load.call_count
        finally:
            logging.disable(previous)
        return result, output.getvalue(), errors.getvalue(), calls

    def test_main_suppresses_transport_output_and_exception_details(self):
        @contextlib.contextmanager
        def session(env):
            print('TOPSECRET 192.0.2.1 PlayerName')
            logging.critical('TOPSECRET')
            raise RuntimeError('TOPSECRET')
            yield
        result = self.run_main(SimpleNamespace(sftp_session=session))
        self.assertEqual(result[:3], (1, 'save-evidence: unconfirmed\n', ''))

    def test_main_success_and_fixed_output(self):
        @contextlib.contextmanager
        def session(env):
            yield Remote(line())
        result = self.run_main(SimpleNamespace(sftp_session=session))
        self.assertEqual(result[:3], (0, '2026-10-21T21:49:40Z rcon-save-enabled\n'
                                     'save-evidence: observed-enabled-explicit-utc\n', ''))

    def test_invocation_gates_before_connection(self):
        for env in ({}, {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main'},
                    {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/topic'}):
            self.assertEqual(self.run_main(None, env=env)[3], 0)
        self.assertEqual(self.run_main(None, argv=['script', '/other'])[3], 0)

    def test_helper_integrity_fails_before_import(self):
        with patch.object(Path, 'read_bytes', return_value=b'untrusted'), \
                patch.object(m.importlib.util, 'spec_from_file_location') as imported:
            with self.assertRaises(ValueError):
                m.load_helper()
            imported.assert_not_called()

    def test_workflow_contract(self):
        workflow = (ROOT / '.github/workflows/inspect-save-evidence.yml').read_text()
        self.assertIn('on:\n  workflow_dispatch:\npermissions:\n  contents: read\n', workflow)
        self.assertIn("if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'", workflow)
        self.assertIn('ref: f27f43679e34a4975e8e1b18cdf6528877c6e66d', workflow)
        for forbidden in ('inputs:', 'SERVER_RCON', 'secrets: inherit', 'workflow_call:',
                          'workflow_run:', 'push:', 'pull_request:', 'contents: write'):
            self.assertNotIn(forbidden, workflow)
        for name in ('HOST', 'PORT', 'PATH'):
            self.assertIn('SERVER_SFTP_' + name + ': ${{ vars.SERVER_SFTP_' + name + ' }}', workflow)
        for name in ('USERNAME', 'PASSWORD', 'PRIVATE_KEY', 'KNOWN_HOSTS'):
            self.assertIn('SERVER_SFTP_' + name + ': ${{ secrets.SERVER_SFTP_' + name + ' }}', workflow)


if __name__ == '__main__':
    unittest.main()
