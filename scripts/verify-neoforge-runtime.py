#!/usr/bin/env python3
"""Fail closed when Sickos' exported and server NeoForge pins diverge.

The historical failure was invisible to a source-only packwiz refresh: Quark's
metadata required NeoForge 21.1.252 while both the published mrpack and server
received 21.1.250.  This verifier checks the exported artifact, Quark's actual
metadata, and the files created by the official NeoForge server installer.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"


def version_tuple(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(?:\.\d+)+", value):
        raise ValueError(f"unsupported NeoForge version {value!r}")
    return tuple(int(part) for part in value.split("."))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out)


def quark_requirement(quark_jar: Path) -> str:
    with zipfile.ZipFile(quark_jar) as archive:
        metadata = archive.read("META-INF/neoforge.mods.toml").decode("utf-8")
    match = re.search(
        r'modId\s*=\s*"neoforge".*?versionRange\s*=\s*"([^"]+)"',
        metadata,
        flags=re.DOTALL,
    )
    require(match is not None, "Quark metadata has no NeoForge dependency range")
    return match.group(1)


def range_allows(requirement: str, version: str) -> bool:
    # Quark 4.1-487 declares the conventional inclusive lower-bound form
    # [21.1.252,).  Refuse any unrecognised form rather than guessing.
    match = re.fullmatch(r"\[\s*(\d+(?:\.\d+)+)\s*,\s*\)", requirement)
    require(match is not None, f"unsupported Quark NeoForge range {requirement!r}")
    return version_tuple(version) >= version_tuple(match.group(1))


def read_expected_pin() -> str:
    with (ROOT / "pack.toml").open("rb") as pack_file:
        return tomllib.load(pack_file)["versions"]["neoforge"]


def verify_export(mrpack: Path, expected_pin: str) -> None:
    with zipfile.ZipFile(mrpack) as archive:
        manifest = json.loads(archive.read("modrinth.index.json"))
    exported_pin = manifest.get("dependencies", {}).get("neoforge")
    require(
        exported_pin == expected_pin,
        f"mrpack NeoForge pin {exported_pin!r} differs from reviewed pin {expected_pin!r}",
    )


def materialize_server(expected_pin: str, directory: Path) -> None:
    installer = directory / f"neoforge-{expected_pin}-installer.jar"
    download(f"{MAVEN}/{expected_pin}/neoforge-{expected_pin}-installer.jar", installer)
    runtime = directory / "server"
    subprocess.run(
        ["java", "-jar", str(installer), "--installServer", str(runtime)],
        check=True,
        cwd=directory,
    )
    expected_server_jar = (
        runtime
        / "libraries/net/neoforged/neoforge"
        / expected_pin
        / f"neoforge-{expected_pin}-server.jar"
    )
    require(expected_server_jar.is_file(), f"materialized server is missing {expected_server_jar}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mrpack", required=True, type=Path)
    args = parser.parse_args()

    expected_pin = read_expected_pin()
    verify_export(args.mrpack, expected_pin)

    with tempfile.TemporaryDirectory(prefix="sickos-neoforge-") as temporary:
        temporary_path = Path(temporary)
        quark_jar = temporary_path / "quark.jar"
        with (ROOT / "mods/quark.pw.toml").open("rb") as quark_file:
            quark = tomllib.load(quark_file)
        download(quark["download"]["url"], quark_jar)
        requirement = quark_requirement(quark_jar)
        require(
            range_allows(requirement, expected_pin),
            f"Quark requires NeoForge {requirement}, but candidate pins {expected_pin}",
        )
        materialize_server(expected_pin, temporary_path)

    print(f"OK: mrpack and materialized server pin NeoForge {expected_pin}; Quark allows {requirement}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
