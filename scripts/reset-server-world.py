#!/usr/bin/env python3
"""Explicit operator-only world reset; never called by normal release deployment."""
import importlib.util
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

import paramiko


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def replace_property(text, key, value):
    pattern = rf"(?m)^{re.escape(key)}=[^\r\n]*"
    if len(re.findall(pattern, text)) > 1:
        raise ValueError("Duplicate server property")
    return re.sub(pattern, f"{key}={value}", text) if re.search(pattern, text) else text.rstrip() + f"\n{key}={value}\n"


def remove_tree(sftp, path):
    for item in sftp.listdir_attr(path):
        if item.filename in (".", "..") or "/" in item.filename:
            raise ValueError("Unsafe directory entry")
        child = path + "/" + item.filename
        if stat.S_ISDIR(item.st_mode):
            remove_tree(sftp, child)
        else:
            sftp.remove(child)
    sftp.rmdir(path)


def reset(sftp, env, sync, deploy):
    old, new = env["EXPECTED_WORLD"], env["NEW_WORLD"]
    if old == new or any(not re.fullmatch(r"world-[A-Za-z0-9_-]+", name) for name in (old, new)):
        raise ValueError("World names must be distinct safe world-prefixed directories")
    root = env["SERVER_SFTP_PATH"]
    properties = sync.joined(root, "server.properties")
    original = sync.read_remote(sftp, properties)
    if sync.level_name(original) != old:
        raise ValueError("Active world differs from the explicitly confirmed world")
    old_path, new_path = sync.joined(root, old), sync.joined(root, new)
    if not stat.S_ISDIR(sftp.lstat(old_path).st_mode):
        raise ValueError("Old world is not a real directory")
    sftp.stat(old_path + "/level.dat")
    try:
        sftp.lstat(new_path)
    except FileNotFoundError:
        pass
    else:
        raise ValueError("Refusing to replace an existing destination world")
    host, port, password, _ = deploy.rcon_config(env)
    response = deploy.rcon_command(host, port, password, "list")
    if not response.startswith("There are 0 of a max of"):
        raise ValueError("World reset requires an empty server")
    deploy.rcon_command(host, port, password, "save-all flush")
    updated = replace_property(replace_property(original, "level-name", new), "gamemode", "survival")
    temporary = properties + ".reset-tmp"
    with sftp.open(temporary, "wb") as output:
        output.write(updated.encode("utf-8"))
    sftp.posix_rename(temporary, properties)
    if sync.read_remote(sftp, properties) != updated:
        raise ValueError("Server properties readback mismatch")
    print("Fresh survival world selected; restarting Minecraft", flush=True)
    deploy.restart_and_wait(env)
    deploy.rcon_command(host, port, password, "save-all flush")
    if sync.level_name(sync.read_remote(sftp, properties)) != new:
        raise ValueError("Server did not retain the new world selection")
    if not stat.S_ISREG(sftp.lstat(new_path + "/level.dat").st_mode):
        raise ValueError("Fresh world was not created; old world retained")
    print("Fresh world is running; deleting the explicitly requested old test world", flush=True)
    remove_tree(sftp, old_path)
    print("Fresh survival world verified; old world deleted", flush=True)


def main():
    sync = module("server_config_sync", Path(__file__).with_name("sync-server-config.py"))
    deploy = sync.schematic_deploy()
    env = os.environ
    port = deploy.upload_config(env)
    with tempfile.TemporaryDirectory() as temporary:
        known = Path(temporary) / "known_hosts"
        known.write_text(env["SERVER_SFTP_KNOWN_HOSTS"])
        known.chmod(0o600)
        key = None
        if env.get("SERVER_SFTP_PRIVATE_KEY"):
            key = Path(temporary) / "key"
            key.write_text(env["SERVER_SFTP_PRIVATE_KEY"])
            key.chmod(0o600)
        with paramiko.SSHClient() as client:
            client.load_host_keys(str(known))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            client.connect(env["SERVER_SFTP_HOST"], port=port, username=env["SERVER_SFTP_USERNAME"],
                           password=env.get("SERVER_SFTP_PASSWORD") or None,
                           key_filename=str(key) if key else None, allow_agent=False, look_for_keys=False,
                           timeout=30, auth_timeout=30, banner_timeout=30)
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(120)
                reset(sftp, env, sync, deploy)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"World reset failed: {type(error).__name__}; inspect state before retrying", file=sys.stderr)
        raise SystemExit(1) from None
