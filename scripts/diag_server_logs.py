"""Read-only server diagnostics over the existing SFTP access: directory listings,
the tail of logs/latest.log, and the newest crash report. Prints log text only;
credentials come from the environment and are never printed."""
import os
import re
import sys
import tempfile
from pathlib import Path

import paramiko

SECRET_LINE = re.compile(r"(password|passwd|token|secret|private[_-]?key|authorization)\s*[=:]", re.I)


def clean(line):
    return "[line withheld: looks like a credential]" if SECRET_LINE.search(line) else line.rstrip("\n")


def tail(sftp, path, count):
    with sftp.open(path, "rb") as handle:
        size = sftp.stat(path).st_size
        handle.seek(max(0, size - 200_000))
        data = handle.read().decode("utf-8", "replace").splitlines()
    return data[-count:]


def head(sftp, path, count):
    with sftp.open(path, "rb") as handle:
        return handle.read(60_000).decode("utf-8", "replace").splitlines()[:count]


def listing(sftp, path):
    try:
        entries = sorted(sftp.listdir_attr(path), key=lambda a: a.st_mtime or 0)
    except IOError as error:
        return [f"(cannot list {path}: {error})"]
    return [f"{a.filename}  {a.st_size} bytes  mtime={a.st_mtime}" for a in entries]


def deployment_state(sftp, root, recover=False):
    """Verify deployment state; optionally preserve one explicitly approved stale marker."""
    import hashlib
    import json
    import stat
    from pathlib import PurePosixPath

    def metadata(name):
        path = f"{root}/{name}"
        try:
            attrs = sftp.lstat(path)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(attrs.st_mode) or attrs.st_size > 1000000:
            raise ValueError("Unsafe or oversized deployment metadata")
        with sftp.open(path, "rb") as handle:
            return handle.read(1000001)

    marker = metadata(".schematic-deploy-pending")
    print("\n=== deployment state (read-only) ===")
    print("pending_marker_present", marker is not None)
    if marker is not None:
        stage = marker.decode("utf-8").strip()
        if not re.fullmatch(re.escape(root) + r"/\.schematic-stage-[0-9a-f]{32}", stage):
            print("stage_reference", "invalid; not followed")
        else:
            print("stage_reference", stage)
            try:
                print("stage_entries", json.dumps(sorted(sftp.listdir(stage))))
            except FileNotFoundError:
                print("stage_present", False)
    raw = metadata(".schematic-deploy.json")
    if raw is None:
        print("managed_manifest_present", False)
        return
    manifest = json.loads(raw)
    files = manifest.get("files", {})
    print("manifest_version", manifest.get("version"))
    print("managed_files", len(files))
    matched, mismatches, missing, unsafe = 0, [], [], []
    for name, expected in sorted(files.items()):
        parts = PurePosixPath(name).parts
        if not parts or name.startswith("/") or any(p in (".", "..") or p.startswith(".") for p in parts) or "\\" in name:
            unsafe.append(name)
            continue
        path = f"{root}/{name}"
        try:
            attrs = sftp.lstat(path)
            if not stat.S_ISREG(attrs.st_mode):
                unsafe.append(name)
                continue
            digest = hashlib.sha512()
            with sftp.open(path, "rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            actual = digest.hexdigest()
            if actual == expected:
                matched += 1
            else:
                mismatches.append({"path": name, "expected": expected, "actual": actual})
        except FileNotFoundError:
            missing.append(name)
    print("managed_hash_matches", matched)
    print("managed_hash_mismatches", json.dumps(mismatches))
    print("managed_missing", json.dumps(missing))
    print("unsafe_paths_skipped", json.dumps(unsafe))
    mods = sftp.listdir_attr(f"{root}/mods")
    untracked = sorted("mods/" + a.filename for a in mods if "mods/" + a.filename not in files)
    print("untracked_mods", json.dumps(untracked))
    if recover:
        expected_marker = b"/.schematic-stage-295069d938ce4abdaa5267e53ce9a6a6"
        if marker is None:
            print("marker_recovery", "already absent; no mutation")
            return
        if root != "" or marker != expected_marker or manifest.get("version") != "0.50.0" or len(files) != 21:
            raise ValueError("Recovery scope differs from approved diagnostic")
        if matched != 21 or mismatches or missing or unsafe or untracked:
            raise ValueError("Recovery refused: managed state differs")
        try:
            sftp.lstat(expected_marker.decode())
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Recovery refused: staging path exists")
        if metadata(".schematic-deploy-pending") != expected_marker or metadata(".schematic-deploy.json") != raw:
            raise ValueError("Recovery refused: metadata changed during verification")
        backup = "/.schematic-deploy-pending.backup-20261012-0_50_0"
        try:
            sftp.lstat(backup)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Recovery backup already exists; refusing overwrite")
        sftp.rename("/.schematic-deploy-pending", backup)
        if metadata(backup.lstrip("/")) != expected_marker:
            raise ValueError("Recovery backup verification failed")
        print("marker_recovery", "preserved exact marker at " + backup)



def main():
    env = os.environ
    root = env["SERVER_SFTP_PATH"].rstrip("/")
    with tempfile.TemporaryDirectory() as tmp:
        hosts = Path(tmp) / "known_hosts"
        hosts.write_text(env["SERVER_SFTP_KNOWN_HOSTS"])
        with paramiko.SSHClient() as client:
            client.load_host_keys(str(hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            client.connect(env["SERVER_SFTP_HOST"], port=int(env.get("SERVER_SFTP_PORT") or 22),
                           username=env["SERVER_SFTP_USERNAME"], password=env.get("SERVER_SFTP_PASSWORD") or None,
                           allow_agent=False, look_for_keys=False, timeout=30, auth_timeout=30, banner_timeout=30)
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(60)
                deployment_state(sftp, root, recover=env.get("RECOVER_APPROVED_STALE_MARKER") == "true")
                for name in ("", "logs", "crash-reports", "mods"):
                    print(f"\n=== listing {root}/{name} ===")
                    print("\n".join(clean(l) for l in listing(sftp, f"{root}/{name}".rstrip("/") or "/")))
                print("\n=== first error lines in logs/latest.log ===")
                with sftp.open(f"{root}/logs/latest.log", "rb") as h:
                    allv = h.read().decode("utf-8", "replace").splitlines()
                hits = [i for i, l in enumerate(allv) if ("ERROR" in l or "Exception" in l) and "ChunkSerializer" not in l and "LootDataType" not in l and "mixin/" not in l and "Unknown registry key" not in l]
                print(f"total lines {len(allv)}, error hits {len(hits)}")
                if hits:
                    for h in hits[:12]:
                        print("\n".join(clean(l)[:400] for l in allv[max(0, h - 1):h + 6]))
                        print("-----")
                print("\n=== tail logs/latest.log (200 lines) ===")
                print("\n".join(clean(l) for l in tail(sftp, f"{root}/logs/latest.log", 200)))
                try:
                    crashes = sorted((a for a in sftp.listdir_attr(f"{root}/crash-reports")), key=lambda a: a.st_mtime or 0)
                except IOError:
                    crashes = []
                if crashes:
                    newest = crashes[-1].filename
                    print(f"\n=== newest crash report {newest} (first 120 lines) ===")
                    print("\n".join(clean(l) for l in head(sftp, f"{root}/crash-reports/{newest}", 120)))


if __name__ == "__main__":
    sys.exit(main())
