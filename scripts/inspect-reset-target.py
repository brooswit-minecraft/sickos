#!/usr/bin/env python3
"""Read-only preflight evidence for scripts/reset-server-world.py; no writes, no restart, no rcon mutation.

Takes the exact same EXPECTED_WORLD/NEW_WORLD inputs the real reset script
takes, and reports everything a reviewer needs to approve a dispatch of that
script without guessing: the live level-name and gamemode, the player count,
and whether the old/new/backup paths are in the state the reset script's own
preflight checks require. Performs the same read-only `list` rcon query the
reset script itself uses to confirm zero players; issues no other rcon
command (no save-all, no stop/restart) and no SFTP write of any kind.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def level_name(properties):
    matches = re.findall(r"^level-name=([^\r\n]+)\r?$", properties, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("Expected exactly one level-name in server.properties")
    return matches[0]


def gamemode(properties):
    matches = re.findall(r"^gamemode=([^\r\n]*)\r?$", properties, re.MULTILINE)
    return matches[0] if len(matches) == 1 else None


def world_status(sftp, path):
    try:
        attr = sftp.lstat(path)
    except FileNotFoundError:
        return "absent"
    if not stat.S_ISDIR(attr.st_mode):
        return "exists, not a directory"
    try:
        sftp.stat(path + "/level.dat")
    except FileNotFoundError:
        return "exists, missing level.dat"
    return "present"


def inspect(sftp, env, deploy):
    old, new = env["EXPECTED_WORLD"], env["NEW_WORLD"]
    root = env["SERVER_SFTP_PATH"].rstrip("/")
    properties_path = (root or "") + "/server.properties"
    with sftp.open(properties_path, "rb") as source:
        properties = source.read().decode("utf-8")
    live_level = level_name(properties)
    report = {
        "expected_world": old,
        "proposed_new_world": new,
        "proposed_backup_path": old + "-backup",
        "live_level_name": live_level,
        "expected_world_matches_live": live_level == old,
        "live_gamemode": gamemode(properties),
        "old_world_status": world_status(sftp, (root or "") + "/" + old),
        "new_world_status": world_status(sftp, (root or "") + "/" + new),
        "backup_path_status": world_status(sftp, (root or "") + "/" + old + "-backup"),
    }
    host, port, password, _ = deploy.rcon_config(env)
    response = deploy.rcon_command(host, port, password, "list")
    report["rcon_list_response"] = response
    report["zero_players"] = response.startswith("There are 0 of a max of")
    return report


def main():
    sync = module("server_config_sync", Path(__file__).with_name("sync-server-config.py"))
    deploy = sync.schematic_deploy()
    env = os.environ
    with deploy.sftp_session(env) as sftp:
        report = inspect(sftp, env, deploy)
    print(json.dumps(report, indent=2), flush=True)
    print("Read-only inspection complete; no writes, restart, or save issued.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Inspection failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
