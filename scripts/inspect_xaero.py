"""Read only Xaero installation/configuration inventory; no gameplay commands."""
import importlib.util
import json
import os
import stat

spec = importlib.util.spec_from_file_location("deploy", ".schematic-tools/scripts/server_deploy.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

try:
    with d.sftp_session(os.environ) as sftp:
        root = os.environ["SERVER_SFTP_PATH"].rstrip("/")
        for folder in ("mods", "config", "defaultconfigs"):
            base = root + "/" + folder
            attr = d.attributes(sftp, base)
            if attr is None:
                continue
            if not stat.S_ISDIR(attr.st_mode):
                raise ValueError("Expected inventory directory")
            matches = [item for item in sftp.listdir_attr(base) if "xaero" in item.filename.lower()]
            print(folder + "_xaero_entries:", json.dumps([item.filename for item in matches]), flush=True)
            for item in matches:
                if stat.S_ISDIR(item.st_mode):
                    nested = sftp.listdir_attr(base + "/" + item.filename)
                    print("xaero_config_directory:", json.dumps(folder + "/" + item.filename),
                          "entries:", json.dumps([entry.filename for entry in nested]), flush=True)
        print("Xaero read-only inventory complete; no server commands or writes.", flush=True)
except Exception as error:
    print("Xaero inventory failed:", type(error).__name__, flush=True)
    raise SystemExit(1)
