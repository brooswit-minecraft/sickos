#!/usr/bin/env python3
"""Place one demo Car in the live world over RCON, on flat grass near a chosen spot.

This is deliberately not a general RCON shell. It runs a fixed set of commands it
builds itself (flat-ground probes, a chunk forceload, one summon, and read-backs of
the car); the only inputs are two coordinates and a search radius. It never changes
blocks, players, gamemode or settings, and it does nothing if a car already exists.
"""
import os
import re
import socket
import struct
import sys

CAR = "dynamicvehicles:car"


def rcon_session(host, port, password):
    sock = socket.create_connection((host, port), timeout=30)

    def packet(ident, kind, body):
        data = struct.pack("<ii", ident, kind) + body.encode() + b"\0\0"
        sock.sendall(struct.pack("<i", len(data)) + data)
        size = struct.unpack("<i", _read(sock, 4))[0]
        payload = _read(sock, size)
        return struct.unpack("<ii", payload[:8])[0], payload[8:-2].decode(errors="replace")

    ident, _ = packet(1, 3, password)
    if ident == -1:
        raise SystemExit("RCON authentication failed")
    return lambda command: packet(2, 2, command)[1]


def _read(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise SystemExit("RCON connection closed")
        data += chunk
    return data


def flat_grass_probe(x, z):
    """A command that passes only if (x, z) is grass at the surface with flat grass 2 blocks out each way."""
    base = f"execute positioned {x} 0 {z} positioned over world_surface"
    checks = [
        "if block ~ ~ ~ minecraft:air",
        "if block ~ ~-1 ~ minecraft:grass_block",
        "if block ~2 ~-1 ~ minecraft:grass_block", "if block ~-2 ~-1 ~ minecraft:grass_block",
        "if block ~ ~-1 ~2 minecraft:grass_block", "if block ~ ~-1 ~-2 minecraft:grass_block",
        "if block ~2 ~ ~ minecraft:air", "if block ~-2 ~ ~ minecraft:air",
        "if block ~ ~ ~2 minecraft:air", "if block ~ ~ ~-2 minecraft:air",
        "if block ~ ~1 ~ minecraft:air", "if block ~ ~2 ~ minecraft:air",
    ]
    return base + " " + " ".join(checks) + " run say probe"


def main():
    env = os.environ
    cx, cz, radius = int(env["CENTER_X"]), int(env["CENTER_Z"]), int(env["RADIUS"])
    if abs(cx) > 20000 or abs(cz) > 20000 or not 0 <= radius <= 160:
        raise SystemExit("coordinates or radius out of range")
    run = rcon_session(env["SERVER_RCON_HOST"], int(env["SERVER_RCON_PORT"]), env["SERVER_RCON_PASSWORD"])

    existing = run(f"execute if entity @e[type={CAR}]")
    if "passed" in existing:
        print("A car already exists; not placing another.")
    else:
        # Spiral outward in 8-block steps until a flat grass spot passes the probe.
        spot = None
        for ring in range(0, radius // 8 + 1):
            for dx in range(-ring, ring + 1):
                for dz in range(-ring, ring + 1):
                    if max(abs(dx), abs(dz)) != ring:
                        continue
                    x, z = cx + dx * 8, cz + dz * 8
                    run(f"forceload add {x} {z}")
                    if run(flat_grass_probe(x, z)).strip() not in ("", "Test failed"):
                        spot = (x, z)
                        break
                if spot:
                    break
            if spot:
                break
        if not spot:
            raise SystemExit("no flat grass spot found within the radius; try another centre or a larger radius")
        x, z = spot
        print(f"Flat grass found at x={x} z={z}; summoning the car")
        print(run(f"execute positioned {x} 0 {z} positioned over world_surface run summon {CAR} ~ ~ ~ {{Rotation:[0f,0f]}}"))

    for query in ("Pos", "UUID", "Rotation"):
        print(query, run(f"data get entity @e[type={CAR},limit=1,sort=nearest] {query}"))
    print("Players online:", run("list"))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as error:
        print(f"place-demo-car failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
