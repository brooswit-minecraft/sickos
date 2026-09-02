#!/usr/bin/env python3
"""Generate the three sickos Modrinth icon candidates.

All shapes below are drawn from scratch with plain geometry (circles,
polygons, and a sampled sine-wave stroke for the monogram). Nothing here
traces or samples any existing artwork, texture, or font. Requires only
Pillow (python3-pil).

Usage: python3 generate_icons.py
Writes candidate_a_rotation.png, candidate_b_monogram.png and
candidate_c_terrain.png into this directory, plus 64x64/32x32 downscaled
previews for each in a preview/ subfolder (previews are not committed;
see the PR body for what was checked).
"""

import math
import os

from PIL import Image, ImageDraw

SIZE = 512
HERE = os.path.dirname(os.path.abspath(__file__))


def save(img, name):
    path = os.path.join(HERE, name)
    img.convert("RGB").save(path, "PNG", optimize=True)
    print(f"wrote {path} ({os.path.getsize(path)} bytes)")
    return path


# ---------------------------------------------------------------------------
# Candidate A: rotation / mechanism mark.
#
# A four-blade pinwheel: each blade is a plain right triangle offset from
# the hub, rotated in 90 degree steps. The offset (rather than a radial
# spoke) is what reads as "spinning" at a glance. This is an original
# pinwheel silhouette, not a reproduction of Create's cogwheel texture.
# ---------------------------------------------------------------------------

def candidate_a():
    bg = (27, 31, 39)  # dark slate
    blade = (217, 142, 63)  # industrial amber
    hub = (245, 236, 219)  # cream

    img = Image.new("RGBA", (SIZE, SIZE), bg + (255,))
    cx, cy = SIZE // 2, SIZE // 2

    blade_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(blade_layer)
    # One pinwheel blade: hub point, an outward point, and a sideways point.
    d.polygon(
        [(cx, cy), (cx, cy - 190), (cx + 130, cy - 130)],
        fill=blade + (255,),
    )

    composite = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    for angle in (0, 90, 180, 270):
        rotated = blade_layer.rotate(angle, resample=Image.BICUBIC, center=(cx, cy))
        composite = Image.alpha_composite(composite, rotated)

    img = Image.alpha_composite(img, composite)

    d = ImageDraw.Draw(img)
    hub_r = 54
    d.ellipse([cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r], fill=hub + (255,))
    return save(img, "candidate_a_rotation.png")


# ---------------------------------------------------------------------------
# Candidate B: wordmark / monogram.
#
# A bold "S" built as geometry, not text: the path is a sampled sine wave
# (so it curves like an S) stroked with a very thick rounded line. No font
# is used or needed.
# ---------------------------------------------------------------------------

def candidate_b():
    bg = (18, 22, 33)  # near-black navy
    stroke = (247, 179, 61)  # warm amber, high contrast on navy

    img = Image.new("RGB", (SIZE, SIZE), bg)
    d = ImageDraw.Draw(img)

    cx = SIZE // 2
    top, bottom = 96, 416
    amplitude = 108
    width = 122
    r = width // 2
    steps = 400
    for i in range(steps + 1):
        t = i / steps
        y = top + t * (bottom - top)
        x = cx + amplitude * math.sin(2 * math.pi * t)
        d.ellipse([x - r, y - r, x + r, y + r], fill=stroke)

    return save(img, "candidate_b_monogram.png")


# ---------------------------------------------------------------------------
# Candidate C: world / terrain / sky mark.
#
# A horizon: a vertical sky gradient, a sun disc, and a jagged mountain
# silhouette across the bottom. Plays off the worldgen/atmosphere half of
# the pack (Tectonic, Project Atmosphere, Peaceful Nights).
# ---------------------------------------------------------------------------

def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def candidate_c():
    sky_top = (17, 45, 66)
    sky_horizon = (232, 210, 168)
    sun_color = (244, 193, 92)
    mountain_color = (13, 27, 31)

    img = Image.new("RGB", (SIZE, SIZE), sky_top)
    px = img.load()
    horizon_y = int(SIZE * 0.62)
    for y in range(horizon_y):
        t = y / horizon_y
        color = lerp(sky_top, sky_horizon, t)
        for x in range(SIZE):
            px[x, y] = color
    for y in range(horizon_y, SIZE):
        for x in range(SIZE):
            px[x, y] = sky_horizon

    d = ImageDraw.Draw(img)

    sun_cx, sun_cy, sun_r = SIZE // 2, int(SIZE * 0.40), 74
    d.ellipse(
        [sun_cx - sun_r, sun_cy - sun_r, sun_cx + sun_r, sun_cy + sun_r],
        fill=sun_color,
    )

    ridge = [
        (0, horizon_y + 40),
        (70, horizon_y - 60),
        (150, horizon_y + 10),
        (230, horizon_y - 130),
        (300, horizon_y - 20),
        (360, horizon_y - 150),
        (430, horizon_y - 10),
        (512, horizon_y - 70),
        (512, SIZE),
        (0, SIZE),
    ]
    d.polygon(ridge, fill=mountain_color)

    return save(img, "candidate_c_terrain.png")


def make_previews(path):
    preview_dir = os.path.join(HERE, "preview")
    os.makedirs(preview_dir, exist_ok=True)
    base = Image.open(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    for sz in (64, 32):
        small = base.resize((sz, sz), Image.LANCZOS)
        out = os.path.join(preview_dir, f"{stem}_{sz}.png")
        small.save(out, "PNG")
        print(f"wrote {out}")


if __name__ == "__main__":
    paths = [candidate_a(), candidate_b(), candidate_c()]
    for p in paths:
        make_previews(p)
