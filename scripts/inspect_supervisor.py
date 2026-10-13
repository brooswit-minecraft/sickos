"""Inspect only startup file metadata and fixed launch-script characteristics."""
import importlib.util
import os
import stat

spec = importlib.util.spec_from_file_location("deploy", ".schematic-tools/scripts/server_deploy.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

try:
    with d.sftp_session(os.environ) as sftp:
        root = os.environ["SERVER_SFTP_PATH"].rstrip("/")
        for name in (d.SUPERVISOR, "run.sh", "start.sh"):
            attr = d.attributes(sftp, root + "/" + name)
            print(name, "exists:", attr is not None, flush=True)
            if not attr:
                continue
            print(name, "regular:", stat.S_ISREG(attr.st_mode), "executable:", bool(attr.st_mode & 0o111), flush=True)
            if not stat.S_ISREG(attr.st_mode):
                continue
            with sftp.open(root + "/" + name, "rb") as source:
                content = source.read(65537)
            if name == d.SUPERVISOR:
                print("supervisor_matches_pinned_body:", content == d.SUPERVISOR_BODY, flush=True)
            else:
                print(name, "starts_with_shebang:", content.startswith(b"#!"), "contains_CRLF:", b"\r\n" in content,
                      "references_java:", b"java" in content, "references_neoforge:", b"neoforge" in content, flush=True)
        print("Read-only startup inspection complete.", flush=True)
except Exception as error:
    print("Startup inspection failed:", type(error).__name__, flush=True)
    raise SystemExit(1)
