"""Read-only release, runtime-log, and active-world verification."""
import importlib.util
import json
import os
import re
import stat

spec = importlib.util.spec_from_file_location("deploy", ".schematic-tools/scripts/server_deploy.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

try:
    with d.sftp_session(os.environ) as sftp:
        root = os.environ["SERVER_SFTP_PATH"].rstrip("/")
        manifest = d.read_manifest(sftp, root + "/" + d.MANIFEST)
        print("installed_manifest_version:", json.dumps(manifest.get("version")), flush=True)
        assert manifest.get("version") == "0.54.0"
        assert not d.attributes(sftp, root + "/" + d.PENDING)
        print("pending_marker: absent", flush=True)
        selected = [p for p in manifest["files"] if re.search(r"/(?:dynamicwhips|magicavoxel-loader)[^/]*\.jar$", p)]
        assert any("dynamicwhips-0.7.0.jar" in p for p in selected)
        assert any("magicavoxel-loader-0.1.3.jar" in p for p in selected)
        for path in selected:
            assert d.remote_hash(sftp, root + "/" + path) == manifest["files"][path]
            print("verified_mod:", json.dumps(path), flush=True)
        level = d.read_level_name(sftp, root)
        print("active_world_path:", json.dumps(root + "/" + level), flush=True)
        for suffix in ("", "_nether", "_the_end"):
            path = root + "/" + level + suffix
            attr = d.attributes(sftp, path)
            if attr:
                print("existing_world_directory:", json.dumps(path), "directory:", stat.S_ISDIR(attr.st_mode), flush=True)
        for name in ("DIM-1", "DIM1", "dimensions", "level.dat", "playerdata", "advancements", "stats"):
            path = root + "/" + level + "/" + name
            if d.attributes(sftp, path):
                print("world_component:", json.dumps(name), flush=True)
        candidate = root + "/world-sickos-0-54-0"
        print("fresh_world_candidate_exists:", bool(d.attributes(sftp, candidate)), flush=True)
        with sftp.open(root + "/logs/latest.log", "rb") as source:
            size = source.stat().st_size
            assert size <= 32 * 1024 * 1024
            source.prefetch(size, max_concurrent_requests=32)
            log = source.read().decode("utf-8", "replace")
        for line in log.splitlines():
            if re.search(r"dynamicwhips.*0\.7\.0|magicavoxel.*0\.1\.3|Done \(|Preparing level", line, re.I):
                print("runtime_evidence:", d.tail_lines(line, 1)[0], flush=True)
    d.wait_until_ready(os.environ)
    print("rcon_readiness: success", flush=True)
except Exception as error:
    print("Verification failed:", type(error).__name__, flush=True)
    raise SystemExit(1)
