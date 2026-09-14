#!/usr/bin/env python3
"""Synchronize repository-owned Dynamic Atmosphere tuning over strict SFTP."""

import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import uuid

import paramiko
import tomlkit


CONFIG_PATH = "dynamicatmosphere-server.toml"
MAX_REMOTE_CONFIG_BYTES = 1024 * 1024
ALLOWED = {
    ("integrations", "createFanTransportPerRpm"): (0.0, 1000.0),
    ("integrations", "createFanIntervalTicks"): (1, 72000),
    ("integrations", "maxFanChunksPerTick"): (1, 10000),
    ("runtime", "simulationSkipChance"): (0.0, 1.0),
    ("enderGas", "portalBlockEmission"): (0, 1_000_000),
}


def load_tuning(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Server tuning must be a JSON object")
    flattened = {}
    for section, values in data.items():
        if not isinstance(section, str) or not isinstance(values, dict):
            raise ValueError("Server tuning sections must be objects")
        for key, value in values.items():
            field = (section, key)
            if field not in ALLOWED:
                raise ValueError(f"Unsupported server tuning field: {section}.{key}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Server tuning field must be a finite number: {section}.{key}")
            minimum, maximum = ALLOWED[field]
            if not minimum <= value <= maximum:
                raise ValueError(f"Server tuning field is outside its supported range: {section}.{key}")
            if field in {("enderGas", "portalBlockEmission"),
                         ("integrations", "createFanIntervalTicks"),
                         ("integrations", "maxFanChunksPerTick")}:
                if not isinstance(value, int):
                    raise ValueError(f"Server tuning field must be an integer: {section}.{key}")
                flattened[field] = value
            else:
                flattened[field] = float(value)
    if set(flattened) != set(ALLOWED):
        raise ValueError("Server tuning must define every repository-owned field exactly once")
    return flattened


def level_name(properties):
    matches = re.findall(r"^level-name=([^\r\n]+)\r?$", properties, re.MULTILINE)
    if len(matches) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]+", matches[0]):
        raise ValueError("Expected exactly one simple level-name in server.properties")
    return matches[0]


def safe_root(value):
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("Unsafe SERVER_SFTP_PATH")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValueError("Unsafe SERVER_SFTP_PATH")
    return str(path).rstrip("/") or "/"


def joined(root, suffix):
    root = safe_root(root)
    return "/" + suffix if root == "/" else f"{root}/{suffix}"


def config_candidates(root, world):
    return (
        joined(root, f"config/{CONFIG_PATH}"),
        joined(root, f"{world}/serverconfig/{CONFIG_PATH}"),
    )


def resolve_config_path(sftp, root, world):
    existing = []
    for candidate in config_candidates(root, world):
        try:
            mode = sftp.stat(candidate).st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("Dynamic Atmosphere config candidate is not a regular file")
        existing.append(candidate)
    if len(existing) != 1:
        raise ValueError("Expected exactly one Dynamic Atmosphere server config candidate")
    return existing[0]


def patch_toml(original, tuning):
    document = tomlkit.parse(original)
    before = document.unwrap()
    for (section, key), value in tuning.items():
        if section not in document:
            document[section] = tomlkit.table()
        elif not hasattr(document[section], "unwrap") or not isinstance(document[section].unwrap(), dict):
            raise ValueError(f"Remote TOML section is not a table: {section}")
        document[section][key] = value
    rendered = tomlkit.dumps(document)
    verified = tomlkit.parse(rendered).unwrap()
    for section, key in tuning:
        previous_section = before.get(section)
        if isinstance(previous_section, dict):
            previous_section.pop(key, None)
            if not previous_section:
                before.pop(section, None)
        current_section = verified.get(section)
        if isinstance(current_section, dict):
            current_section.pop(key, None)
            if not current_section:
                verified.pop(section, None)
    if before != verified:
        raise ValueError("Refusing TOML update that changes non-owned settings")
    return rendered


def read_remote(sftp, path):
    with sftp.open(path, "rb") as source:
        content = source.read(MAX_REMOTE_CONFIG_BYTES + 1)
    if len(content) > MAX_REMOTE_CONFIG_BYTES:
        raise ValueError("Remote config exceeds the supported size")
    return content.decode("utf-8")


def synchronize(sftp, root, tuning):
    normalized_root = safe_root(root)
    properties_path = "/server.properties" if normalized_root == "/" else f"{normalized_root}/server.properties"
    properties = read_remote(sftp, properties_path)
    target = resolve_config_path(sftp, root, level_name(properties))
    original = read_remote(sftp, target)
    updated = patch_toml(original, tuning)
    if updated == original:
        return
    temporary = f"{target}.tmp-{uuid.uuid4().hex}"
    try:
        with sftp.open(temporary, "wb") as output:
            output.write(updated.encode("utf-8"))
        sftp.posix_rename(temporary, target)
    finally:
        try:
            sftp.remove(temporary)
        except OSError:
            pass
    readback = read_remote(sftp, target)
    parsed = tomlkit.parse(readback)
    for (section, key), expected in tuning.items():
        if float(parsed[section][key]) != expected:
            raise ValueError("Remote config readback did not match repository tuning")
    if readback != updated:
        raise ValueError("Remote config readback differed from the atomic upload")


def schematic_deploy():
    path = Path(os.environ.get(
        "SCHEMATIC_SERVER_DEPLOY", ".schematic-tools/scripts/server_deploy.py"))
    spec = importlib.util.spec_from_file_location("schematic_server_deploy", path)
    if spec is None or spec.loader is None:
        raise ValueError("Unable to load Schematic server deployment helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    env = os.environ
    tuning = load_tuning(env.get("SERVER_TUNING_FILE", "server-config.json"))
    port = schematic_deploy().upload_config(env)
    with tempfile.TemporaryDirectory() as temporary:
        known_hosts = Path(temporary) / "known_hosts"
        known_hosts.write_text(env["SERVER_SFTP_KNOWN_HOSTS"], encoding="utf-8")
        known_hosts.chmod(0o600)
        key_file = None
        if env.get("SERVER_SFTP_PRIVATE_KEY"):
            key_file = Path(temporary) / "key"
            key_file.write_text(env["SERVER_SFTP_PRIVATE_KEY"], encoding="utf-8")
            key_file.chmod(0o600)
        with paramiko.SSHClient() as client:
            client.load_host_keys(str(known_hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            client.connect(
                env["SERVER_SFTP_HOST"], port=port, username=env["SERVER_SFTP_USERNAME"],
                password=env.get("SERVER_SFTP_PASSWORD") or None,
                key_filename=str(key_file) if key_file else None,
                allow_agent=False, look_for_keys=False, timeout=30, auth_timeout=30, banner_timeout=30)
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(60)
                synchronize(sftp, env["SERVER_SFTP_PATH"], tuning)
    print("Server tuning synchronized and verified")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Server tuning sync failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
