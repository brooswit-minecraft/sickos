## Sickos 0.23.0 is a breaking update

Back up your world first. Dust, Ender Gas, Exhaust, Void Gas and Slime that
has already built up is cleared when the world loads. Old violence settings
reset to defaults under a new voidGas section, so set your tuning again. The
client and the server must run the same version.

Known issue: in areas you have already explored, Dust, Ender Gas, Exhaust,
Void Gas and Slime will not appear, because Dynamic Atmosphere cannot read
their old saved data. New areas work normally, and Vapor and Smoke are
unaffected. A fix is expected in the next Dynamic Atmosphere release.

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
- Dynamic Atmosphere (4-block atmospheric cells with water, cloud, rain, and nighttime ground buildup and local decay)
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

Project Atmosphere and Gabou's Libs were removed in 0.2.1 because their published dependency chain could not produce a working 1.21.1 installation. Our Dynamic Atmosphere alpha adds a server-owned atmospheric grid, rendered as translucent cells on the client. Material builds up and decays in place; it does not flow between cells. Update both client and server together. No world reset is needed.

## Install

Download the latest `.mrpack` from [GitHub Releases](https://github.com/brooswit-minecraft/sickos/releases) or the Versions tab, then open it in the Modrinth App, Prism Launcher, or another Modrinth-compatible launcher. Client and server deployments use the same release manifest.

## Source and releases

[GitHub](https://github.com/brooswit-minecraft/sickos) is the primary source for code and releases. Each published version is built by CI from the pinned pack index, attached to its matching GitHub tag, and mirrored to Modrinth.
