"""Delete only the exact world explicitly approved for permanent reset."""
import importlib.util
import os
from pathlib import PurePosixPath
import socket
import stat

spec = importlib.util.spec_from_file_location("deploy", ".schematic-tools/scripts/server_deploy.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
TARGET = "/world-sickos-0-39-0"
LEVEL = "world-sickos-0-39-0"


def require(condition):
    if not condition:
        raise ValueError("State or target differs from the explicit reset approval")


def log_tail(sftp):
    path = "/logs/latest.log"
    require(stat.S_ISREG(sftp.lstat(path).st_mode))
    with sftp.open(path, "rb") as source:
        size = source.stat().st_size
        source.seek(max(0, size - 64000))
        return source.read()


def require_stopped(sftp, env):
    before = log_tail(sftp)
    stopped = before.rfind(b"Thread RCON Listener stopped")
    require(stopped >= 0 and stopped > before.rfind(b"Done (")
            and stopped > before.rfind(b"Preparing level"))
    try:
        connection = socket.create_connection((env["SERVER_RCON_HOST"], int(env.get("SERVER_RCON_PORT") or "25575")), timeout=10)
    except OSError:
        pass
    else:
        connection.close()
        raise ValueError("RCON is reachable; refusing world deletion")
    require(log_tail(sftp) == before)


def collect(sftp, path):
    require(path == TARGET or PurePosixPath(path).is_relative_to(PurePosixPath(TARGET)))
    require(stat.S_ISDIR(sftp.lstat(path).st_mode))
    operations = []
    for item in sftp.listdir_attr(path):
        name = item.filename
        require(name not in ("", ".", "..") and "/" not in name and "\\" not in name
                and not any(ord(c) < 32 for c in name))
        child = path + "/" + name
        mode = sftp.lstat(child).st_mode
        if stat.S_ISDIR(mode):
            operations.extend(collect(sftp, child))
        else:
            # Symlinks are unlinked as entries, never followed outside TARGET.
            operations.append(("remove", child))
    operations.append(("rmdir", path))
    return operations


def reset(sftp, env):
    require(env["SERVER_SFTP_PATH"] == "/")
    require(d.read_level_name(sftp, "") == LEVEL)
    require(d.read_manifest(sftp, "/" + d.MANIFEST).get("version") == "0.54.0")
    require(d.attributes(sftp, "/" + d.PENDING) is None)
    require_stopped(sftp, env)
    root_before = {item.filename for item in sftp.listdir_attr("/") if item.filename != LEVEL}
    operations = collect(sftp, TARGET)
    require(d.read_level_name(sftp, "") == LEVEL)
    require_stopped(sftp, env)
    print("Verified exact approved target and stopped state. Permanently deleting only", TARGET,
          "without backup; entries:", len(operations), flush=True)
    for operation, path in operations:
        require(path == TARGET or PurePosixPath(path).is_relative_to(PurePosixPath(TARGET)))
        getattr(sftp, operation)(path)
    require(d.attributes(sftp, TARGET) is None)
    require({item.filename for item in sftp.listdir_attr("/")} == root_before)
    require(d.read_level_name(sftp, "") == LEVEL)
    print("Verified absent:", TARGET, flush=True)
    print("No backup created. Root entries outside the approved world preserved. Server start was not requested.", flush=True)


if __name__ == "__main__":
    try:
        with d.sftp_session(os.environ) as sftp:
            reset(sftp, os.environ)
    except Exception as error:
        print("Reset stopped:", type(error).__name__, flush=True)
        raise SystemExit(1)
