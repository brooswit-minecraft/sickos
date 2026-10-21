#!/usr/bin/env python3
"""Bounded, read-only log evidence; never a command-delivery or readiness proof."""
import contextlib
import hashlib
import importlib.util
import logging
import os
from pathlib import Path
import re
import stat
import sys

LIMIT = 1024 * 1024
HELPER = Path(__file__).resolve().parents[1] / '.schematic-tools/scripts/server_deploy.py'
HELPER_SHA256 = '73db30c0915e1b83732646f351e9c9b46994aff2b156b6600bc2f711871cf41d'
# Only exact server messages, optionally wrapped by the fixed Rcon source.
CATEGORIES = {
    'Automatic saving is now enabled': 'save-enabled',
    'Saving is already turned on': 'save-already-enabled',
    'Automatic saving is now disabled': 'save-disabled',
    'Saving is already turned off': 'save-already-disabled',
    'Saving the game (this may take a moment!)': 'save-started',
    'Saved the game': 'save-completed',
}
LINE = re.compile(
    r'\[(?P<stamp>[^\[\]\r\n]+)\] \[Server thread/INFO\]'
    r'(?: \[minecraft/MinecraftServer\])?: (?P<message>[^\r\n]+)', re.ASCII)
UTC_STAMP = re.compile(r'2026-10-21[T ]21:49:(?P<second>[0-5][0-9])(?:Z|\+00:00)', re.ASCII)
CLOCK = re.compile(r'21:49:(?:3[0-9]|4[0-9]|50)', re.ASCII)


class Discard:
    """Suppress helper/transport diagnostics without retaining peer text."""
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def load_helper():
    if hashlib.sha256(HELPER.read_bytes()).hexdigest() != HELPER_SHA256:
        raise ValueError('helper mismatch')
    spec = importlib.util.spec_from_file_location('save_evidence_sftp', HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def read_tail(sftp, root):
    # The sole content path is fixed beneath the existing server-root mapping.
    if (not root.startswith('/') or '\\' in root
            or any(p in ('.', '..') for p in root.split('/'))
            or any(ord(c) < 32 or ord(c) == 127 for c in root)):
        raise ValueError('invalid root')
    root = root.rstrip('/')
    # Reject symlinks in every component we traverse. SFTP cannot guarantee an
    # atomic no-follow open; concurrent replacement is a documented limitation.
    current = ''
    for component in (root + '/logs').split('/'):
        if component:
            current += '/' + component
            if not stat.S_ISDIR(sftp.lstat(current).st_mode):
                raise ValueError('not directory')
    path = root + '/logs/latest.log'
    before = sftp.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('not regular')
    with sftp.open(path, 'rb') as source:
        opened = source.stat()
        size = opened.st_size
        if (not stat.S_ISREG(opened.st_mode) or type(size) is not int or size < 0
                or (size, opened.st_mtime) != (before.st_size, before.st_mtime)):
            raise ValueError('unstable file')
        start = max(0, size - LIMIT)
        source.seek(start)
        raw = source.read(min(size, LIMIT))  # One bounded read; no prefetch/retry.
        after = source.stat()
        if (not isinstance(raw, bytes) or len(raw) != size - start
                or (after.st_size, after.st_mtime) != (size, opened.st_mtime)):
            raise ValueError('unstable read')
    if start:
        # Discard the first line even if the offset happened to be a boundary.
        _, separator, raw = raw.partition(b'\n')
        if not separator:
            raw = b''
    # An unterminated last line may be an incomplete server write.
    return raw[:raw.rfind(b'\n') + 1]


def classify(raw):
    events = set()
    uncertain = False
    for line in raw.split(b'\n'):
        try:
            text = line.decode('ascii')
        except UnicodeDecodeError:
            continue
        if text.endswith('\r'):
            text = text[:-1]
        match = LINE.fullmatch(text)
        if not match:
            continue
        message = match['message']
        if message.startswith('[Rcon: ') and message.endswith(']'):
            message = message[7:-1]
            source = 'rcon-'
        else:
            source = ''
        category = CATEGORIES.get(message)
        if category is None:
            continue
        stamp = match['stamp']
        dated = UTC_STAMP.fullmatch(stamp)
        if dated and 30 <= int(dated['second']) <= 50:
            events.add(('2026-10-21T21:49:' + dated['second'] + 'Z', source + category))
        elif CLOCK.fullmatch(stamp):
            # Neither the runner clock nor file mtime proves a log's date/TZ.
            uncertain = True
    enabled = any(category in ('save-enabled', 'save-already-enabled',
                               'rcon-save-enabled', 'rcon-save-already-enabled')
                  for _, category in events)
    disabled = any('disabled' in category for _, category in events)
    result = 'save-evidence: unconfirmed'
    if uncertain:
        result = 'save-evidence: unconfirmed-date-time'
    elif enabled and not disabled:
        result = 'save-evidence: observed-enabled-explicit-utc'
    return sorted(events), result, enabled and not disabled and not uncertain


def main():
    try:
        if (len(sys.argv) != 1 or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
                or os.environ.get('GITHUB_REF') != 'refs/heads/main'):
            raise ValueError('invalid invocation')
        # Paramiko may otherwise log peer-controlled exceptions from a thread.
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(Discard()), contextlib.redirect_stderr(Discard()):
            helper = load_helper()
            with helper.sftp_session(os.environ) as sftp:
                raw = read_tail(sftp, os.environ['SERVER_SFTP_PATH'])
            events, result, success = classify(raw)
    except Exception:
        print('save-evidence: unconfirmed')
        return 1
    for stamp, category in events:
        print(stamp + ' ' + category)
    print(result)
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
