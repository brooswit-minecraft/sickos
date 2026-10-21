#!/usr/bin/env python3
"""Metadata-only SFTP preflight. Never call backup/deploy/RCON helpers."""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys


def entry_type(mode):
    if mode is None:
        return "unknown"
    for check, name in ((stat.S_ISREG, "file"), (stat.S_ISDIR, "directory"),
                        (stat.S_ISLNK, "symlink")):
        if check(mode):
            return name
    return "other"


def metadata(sftp, path):
    try:
        attr = sftp.lstat(path)
    except FileNotFoundError:
        return {"path": path, "type": "absent", "size_bytes": None}
    return {"path": path, "type": entry_type(attr.st_mode), "size_bytes": attr.st_size}


def inspect(sftp, root):
    if (not root.startswith("/") or "\\" in root
            or any(p in (".", "..") for p in root.split("/"))
            or any(ord(c) < 32 for c in root)):
        raise ValueError("Expected an absolute server directory")
    root = root.rstrip("/")
    directory = root + "/backups"
    backup = metadata(sftp, directory)
    backup["entries"] = []
    # Refuse to follow a backup-directory symlink. Absent is evidence, not an
    # instruction to create it. Permission/transport failures propagate.
    if backup["type"] == "directory":
        backup["entries"] = [
            {"name": attr.filename, "type": entry_type(attr.st_mode),
             "size_bytes": attr.st_size,
             "backup_stage": attr.filename.startswith(".schematic-backup-stage-")}
            for attr in sorted(sftp.listdir_attr(directory), key=lambda a: a.filename)
        ]
        backup["entry_count"] = len(backup["entries"])
    else:
        backup["entry_count"] = None
    with sftp.open(root + "/server.properties", "rb") as source:
        properties = source.read(1024 * 1024 + 1)
    if len(properties) > 1024 * 1024:
        raise ValueError("server.properties exceeds inspection limit")
    levels = re.findall(r"^level-name=([^\r\n]+)\r?$", properties.decode("utf-8"), re.MULTILINE)
    if len(levels) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]+", levels[0]):
        raise ValueError("Expected one simple level-name")
    world = root + "/" + levels[0]
    return {"backups": backup, "level_name": levels[0],
            "world_locations": [metadata(sftp, world + suffix)
                                for suffix in ("", "_nether", "_the_end", "/DIM-1", "/DIM1")],
            "scope": "Immediate directory metadata only; sizes are SFTP attributes, not recursive totals. Live, non-atomic observation; no archive integrity or player-state check."}


def main():
    # Same established SFTP session as inspect-reset-target.py, loaded from the
    # workflow's immutable Schematic checkout; importing does not deploy.
    spec = importlib.util.spec_from_file_location(
        "schematic_server_deploy", Path(".schematic-tools/scripts/server_deploy.py"))
    deploy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy)
    with deploy.sftp_session(os.environ) as sftp:
        report = inspect(sftp, os.environ["SERVER_SFTP_PATH"])
    # JSON escapes remote names/control characters; never print properties or
    # exception messages (which may contain connection/credential details).
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Backup inspection failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
