"""Regenerate Default Options' server list with: uv run --with nbtlib this-file."""

from pathlib import Path

import nbtlib


target = Path(__file__).resolve().parents[1] / "config/defaultoptions/servers.dat"
target.parent.mkdir(parents=True, exist_ok=True)
nbtlib.File({
    "servers": nbtlib.List[nbtlib.Compound]([
        nbtlib.Compound({
            "name": nbtlib.String("Sickos"),
            "ip": nbtlib.String("breezy-trident848.modrinth.gg"),
        }),
    ]),
}).save(target, gzipped=False)
