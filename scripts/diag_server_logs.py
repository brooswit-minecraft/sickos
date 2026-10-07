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
                for name in ("", "logs", "crash-reports", "mods"):
                    print(f"\n=== listing {root}/{name} ===")
                    print("\n".join(clean(l) for l in listing(sftp, f"{root}/{name}".rstrip("/") or "/")))
                print("\n=== first error lines in logs/latest.log ===")
                with sftp.open(f"{root}/logs/latest.log", "rb") as h:
                    allv = h.read().decode("utf-8", "replace").splitlines()
                hits = [i for i, l in enumerate(allv) if "ERROR" in l or "Exception" in l]
                print(f"total lines {len(allv)}, error hits {len(hits)}")
                if hits:
                    print("\n".join(clean(l) for l in allv[max(0, hits[0] - 3):hits[0] + 40]))
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
