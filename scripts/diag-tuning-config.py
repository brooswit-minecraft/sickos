#!/usr/bin/env python3
"""Read-only diagnostic: dump the resolved DA server config candidate(s) and why resolution fails/succeeds."""
import importlib.util
import os
from pathlib import Path
import sys


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    sync = module("server_config_sync", Path(__file__).with_name("sync-server-config.py"))
    deploy = sync.schematic_deploy()
    env = os.environ
    with deploy.sftp_session(env) as sftp:
        root = env["SERVER_SFTP_PATH"]
        properties_path = "/server.properties" if sync.safe_root(root) == "/" else sync.safe_root(root) + "/server.properties"
        properties = sync.read_remote(sftp, properties_path)
        world = sync.level_name(properties)
        print("live level-name:", world, flush=True)
        for candidate in sync.config_candidates(root, world):
            try:
                mode = sftp.stat(candidate).st_mode
                import stat
                print(f"candidate {candidate}: exists, regular={stat.S_ISREG(mode)}", flush=True)
            except FileNotFoundError:
                print(f"candidate {candidate}: absent", flush=True)
        try:
            resolved = sync.resolve_config_path(sftp, root, world)
            print("resolved config path:", resolved, flush=True)
            content = sync.read_remote(sftp, resolved)
            print("--- content ---", flush=True)
            print(content, flush=True)
            print("--- end content ---", flush=True)
        except Exception as error:
            print("resolve_config_path failed:", type(error).__name__, str(error), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Diagnostic failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
