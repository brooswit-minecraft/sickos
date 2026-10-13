"""One-time, fail-closed recovery of the diagnosed 0.54.0 deployment incident."""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat

spec = importlib.util.spec_from_file_location("deploy", ".schematic-tools/scripts/server_deploy.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

MISSING = {"mods/dynamicdespawn-0.3.2.jar", "mods/dynamicvehicles-0.43.0.jar"}
UNTRACKED = {"mods/dynamicdespawn-0.3.3.jar", "mods/dynamicvehicles-0.45.1.jar"}


def require(condition):
    if not condition:
        raise ValueError("Server state differs from the diagnosed incident; no automatic recovery")


def recover(sftp, root, desired):
    root = root.rstrip("/")
    d.directory(sftp, root or "/")
    old = d.read_manifest(sftp, root + "/" + d.MANIFEST)
    require(old.get("version") == "0.53.1" and desired.get("version") == "0.54.0")
    marker = root + "/" + d.PENDING
    require(d.regular(sftp, marker) is not None)
    with sftp.open(marker, "rb") as source:
        marker_bytes = source.read(1025)
    require(len(marker_bytes) <= 1024)
    stage = marker_bytes.decode("utf-8")
    require(re.fullmatch(re.escape(root) + r"/\.schematic-stage-[0-9a-f]{32}", stage) is not None)
    d.directory(sftp, stage)
    stages = {root + "/" + x.filename for x in sftp.listdir_attr(root or "/")
              if x.filename.startswith(".schematic-stage-")}
    require(stages == {stage})
    remote_mods = set()
    def walk(path, relative):
        for item in sftp.listdir_attr(path):
            child = d.safe_path(relative + "/" + item.filename)
            if stat.S_ISDIR(item.st_mode):
                walk(path + "/" + item.filename, child)
            else:
                require(stat.S_ISREG(item.st_mode))
                remote_mods.add(child)
    walk(root + "/mods", "mods")
    require(remote_mods - old["files"].keys() == UNTRACKED)
    require({p for p in old["files"] if not d.regular(sftp, root + "/" + p)} == MISSING)
    require(UNTRACKED <= desired["files"].keys())
    # Revalidate every present old/desired file, including the two untracked
    # jars, against known release bytes before moving any recovery metadata.
    for path in old["files"].keys() | desired["files"].keys():
        d.safe_path(path)
        target = root + "/" + path
        if d.regular(sftp, target):
            require(d.remote_hash(sftp, target) in (old["files"].get(path), desired["files"].get(path)))
    backup = root + "/backups/incident-38402755903-deployment"
    require(d.attributes(sftp, backup) is None)
    # Preserve the exact pre-recovery manifest, staging directory, and marker.
    # No pack file, world, or configuration file is removed or overwritten.
    d.directory(sftp, backup, create=True)
    with sftp.open(root + "/" + d.MANIFEST, "rb") as source:
        manifest_bytes = source.read(16 * 1024 * 1024 + 1)
    require(len(manifest_bytes) <= 16 * 1024 * 1024)
    with sftp.open(backup + "/manifest.json", "wx") as target:
        target.write(manifest_bytes)
    with sftp.open(backup + "/manifest.json", "rb") as source:
        require(source.read() == manifest_bytes)
    with sftp.open(marker, "rb") as source:
        require(source.read(1025) == marker_bytes)
    sftp.posix_rename(stage, backup + "/stage")
    sftp.posix_rename(marker, backup + "/pending-marker")
    print("Verified diagnosed incident state against release 0.54.0; preserved staging, manifest, and marker under backups/incident-38402755903-deployment. No pack or world files changed.")


if __name__ == "__main__":
    try:
        desired = json.loads((Path(os.environ["RUNNER_TEMP"]) / "server-files" / d.MANIFEST).read_text())
        with d.sftp_session(os.environ) as sftp:
            recover(sftp, os.environ["SERVER_SFTP_PATH"], desired)
    except Exception as error:
        print("Recovery stopped:", type(error).__name__)
        raise SystemExit(1)
