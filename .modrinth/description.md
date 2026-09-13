## What this is

Sickos is a Create-focused modpack for Minecraft 1.21.1 on NeoForge. Mods and required libraries are pinned to exact versions and hashes for repeatable client and server installs.

This is an early, playable build. It is a foundation for the Sickos world rather than a finished pack.

## Included mods

### Gameplay

- Create
- Create: Power Grid
- Create Aeronautics
- Create: Diesel Generators
- Pam's HarvestCraft 2: Food Core, Crops, Trees, and Food Extended
- Dynamic Atmosphere (early cloud/fog visual spike)
- Flowing Fluids
- Countered's Terrain Slabs
- Tectonic
- Serene Seasons
- Peaceful Nights

### Required libraries

- Architectury API
- GlitchCore
- Lithostitched
- Sable
- Default Options and Balm (client-side default server list)

Project Atmosphere and Gabou's Libs were removed in 0.2.1 because their published dependency chain could not produce a working 1.21.1 installation. Our Dynamic Atmosphere alpha is intentionally a small visual/runtime spike, not a full weather simulation. It needs no world reset.

## Install

Download the latest `.mrpack` from [GitHub Releases](https://github.com/brooswit-minecraft/sickos/releases) or the Versions tab, then open it in the Modrinth App, Prism Launcher, or another Modrinth-compatible launcher. Client and server deployments use the same release manifest.

## Source and releases

[GitHub](https://github.com/brooswit-minecraft/sickos) is the primary source for code and releases. Each published version is built by CI from the pinned pack index, attached to its matching GitHub tag, and mirrored to Modrinth.
