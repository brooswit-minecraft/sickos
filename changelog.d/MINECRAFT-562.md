bump: minor

### BREAKING
- Retire the legacy "Dynamic Terrain" mod entirely. Worlds with placed
  Dynamic Terrain layered-block content (layered dirt/grass/sand/gravel/
  stone/cobblestone/smooth stone/deepslate/cobbled deepslate) lose that
  content's mod-provided behavior; existing placed blocks may become
  unrecognized/ghost blocks on load. **Back up your world before
  upgrading. This requires a fresh world for continued terrain-layer
  gameplay.**
- Retire the legacy "Dynamic Vehicles" mod entirely, including its
  bundled Dispatcher delivery/courier gameplay and its placeholder Car
  entity. Worlds with a placed Car lose it on load. No vehicle-physics
  replacement ships in this test branch (see Removed below) — only the
  Dispatcher/Courier gameplay is preserved, via Courier Craft.

### Added
- Add Layered Slopes 0.2.0, Layered Slope Grading 0.3.0, Smooth Slopes
  (layered-slopes-smooth-terrain) 0.2.1, the Layered Slopes × Flowing
  Fluids compat mod 0.2.1, and Layered Slope Dynamic Paths 0.2.2 as the
  split successor family to Dynamic Terrain, client and server.
- Add Courier Craft 0.2.0 (the Dispatcher/Courier extraction from Dynamic
  Vehicles) as the sole Dispatcher implementation, client and server.
- Raise the isolated NeoForge floor from 21.1.252 to 21.1.265, validated
  against every pinned mod's own declared NeoForge dependency range (no
  mod in the pack declares a floor above 21.1.252, and 16 mods declare no
  NeoForge floor at all).

### Removed
- Remove Dynamic Terrain (dynamicterrain-0.28.2.jar) with no coexistence
  gate.
- Remove Dynamic Vehicles (dynamicvehicles-0.80.0.jar) with no
  coexistence gate. Dynamic Immersive Vehicles (its intended physics
  replacement) and the layered-slopes-dynamic-immersive-vehicles erosion
  compat mod are explicitly NOT added in this branch: neither has a
  stable, publicly pinnable release artifact as of this change (DIV has
  only stale pre-main `v0.1.0-experimental.*` prereleases explicitly
  marked "UNTESTED GAMEPLAY ... do not deploy to a live world"; the
  compat mod has no release/tag at all, only ephemeral CI build
  artifacts), and DIV's own required `sablecompanion` dependency has no
  discoverable Modrinth-hosted NeoForge 1.21.1 build. See MINECRAFT-562
  for the full policy-conflict writeup. This is a known, deliberate,
  test-pack-only gap: this branch ships Courier/Dispatcher gameplay with
  no drivable vehicle.
