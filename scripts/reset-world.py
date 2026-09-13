"""Explicit operator maintenance, never invoked by the release workflow."""
import importlib.util
import os
from pathlib import Path
import re
import stat
import tempfile

import paramiko


def replace_world(properties, new):
    matches = re.findall(r'^level-name=([^\r\n]+)\r?$', properties, re.M)
    if len(matches) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]+', matches[0]):
        raise ValueError('Expected one simple level-name; refusing ambiguous paths')
    old = matches[0]
    if not (old == 'world' or old.startswith('world-')) or old == new:
        raise ValueError('Unsafe or unchanged world path')
    return old, re.sub(r'^level-name=[^\r\n]+', 'level-name=' + new, properties, flags=re.M)


def remove_tree(sftp, path):
    # Never follow symlinks outside the explicitly selected old world.
    for item in sftp.listdir_attr(path):
        child = path + '/' + item.filename
        if stat.S_ISDIR(item.st_mode):
            remove_tree(sftp, child)
        else:
            sftp.remove(child)
    sftp.rmdir(path)


def main():
    if os.environ.get('CONFIRM_RESET') != 'RESET':
        raise ValueError('Explicit RESET confirmation required')
    spec = importlib.util.spec_from_file_location('deploy', 'schematic/scripts/server_deploy.py')
    deploy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy)
    env = os.environ
    port = deploy.upload_config(env)
    host, rport, password, _ = deploy.rcon_config(env)
    root = env['SERVER_SFTP_PATH'].rstrip('/')
    new = 'world-harvestcraft-' + env['GITHUB_RUN_ID']
    with tempfile.TemporaryDirectory() as temp:
        known = Path(temp) / 'known_hosts'
        known.write_text(env['SERVER_SFTP_KNOWN_HOSTS'])
        key = None
        if env.get('SERVER_SFTP_PRIVATE_KEY'):
            key = Path(temp) / 'key'
            key.write_text(env['SERVER_SFTP_PRIVATE_KEY'])
            key.chmod(0o600)
        with paramiko.SSHClient() as client:
            client.load_host_keys(str(known))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            client.connect(env['SERVER_SFTP_HOST'], port=port,
                           username=env['SERVER_SFTP_USERNAME'],
                           password=env.get('SERVER_SFTP_PASSWORD') or None,
                           key_filename=str(key) if key else None,
                           look_for_keys=False, allow_agent=False, timeout=30)
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(60)
                properties_path = root + '/server.properties'
                with sftp.open(properties_path, 'rb') as source:
                    original = source.read().decode('utf-8')
                old, updated = replace_world(original, new)
                if not stat.S_ISDIR(sftp.lstat(root + '/' + old).st_mode):
                    raise ValueError('Old world is not a real directory')
                try:
                    sftp.lstat(root + '/' + new)
                except FileNotFoundError:
                    pass
                else:
                    raise ValueError('New world already exists')
                # Repoint only the next launch. Do not touch the active world.
                deploy.rcon_command(host, rport, password, 'save-all flush')
                with sftp.open(properties_path + '.reset-pending', 'wb') as target:
                    target.write(updated.encode('utf-8'))
                sftp.posix_rename(properties_path + '.reset-pending', properties_path)
                deploy.restart_and_wait(env)
                with sftp.open(properties_path, 'rb') as source:
                    current = source.read().decode('utf-8')
                if 'level-name=' + new not in current.splitlines():
                    raise ValueError('World selection did not persist; old world retained')
                sftp.stat(root + '/' + new + '/level.dat')
                print(deploy.rcon_command(host, rport, password, 'op brooswit'))
                with sftp.open(root + '/ops.json', 'rb') as source:
                    import json
                    ops = json.load(source)
                assert any(x['name'].lower() == 'brooswit' and x['level'] >= 2 for x in ops)
                remove_tree(sftp, root + '/' + old)
                print('Fresh world ready:', new, '; removed previous world:', old)
                print('Verified brooswit operator entry and RCON readiness')


if __name__ == '__main__':
    main()
