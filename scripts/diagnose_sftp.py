"""Read-only incident diagnosis; never display connection configuration."""
import importlib.util
import json
import os
import stat
from pathlib import Path

spec = importlib.util.spec_from_file_location("deploy", ".schematic-tools/scripts/server_deploy.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

phase = "connect"
try:
    with d.sftp_session(os.environ) as sftp:
        root = os.environ["SERVER_SFTP_PATH"].rstrip("/")
        phase = "pending marker"
        print("pending_marker:", bool(d.attributes(sftp, root + "/" + d.PENDING)), flush=True)
        stages = [x for x in sftp.listdir_attr(root or "/") if x.filename.startswith(".schematic-stage-")]
        print("stage_directories:", len(stages), flush=True)
        for stage in stages:
            print("stage_entries:", len(sftp.listdir_attr(root + "/" + stage.filename)), flush=True)
        phase = "manifest"
        old = d.read_manifest(sftp, root + "/" + d.MANIFEST)
        print("managed_version:", json.dumps(old.get("version")), "managed_files:", len(old["files"]), flush=True)
        phase = "mods inventory"
        untracked = []
        def walk(path, relative):
            for item in sftp.listdir_attr(path):
                child = relative + "/" + item.filename
                if stat.S_ISDIR(item.st_mode):
                    walk(path + "/" + item.filename, child)
                elif child not in old["files"]:
                    untracked.append(child)
        walk(root + "/mods", "mods")
        print("untracked_mods:", json.dumps(untracked), flush=True)
        phase = "managed file integrity"
        changed, missing = [], []
        for path, digest in old["files"].items():
            target = root + "/" + path
            if not d.regular(sftp, target):
                missing.append(path)
            elif d.remote_hash(sftp, target) != digest:
                changed.append(path)
        print("changed_managed_files:", json.dumps(changed), flush=True)
        print("missing_managed_files:", json.dumps(missing), flush=True)
except Exception as error:
    print("Diagnostic failed during", phase, "with", type(error).__name__, flush=True)
    raise SystemExit(1)
