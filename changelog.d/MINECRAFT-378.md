bump: minor

### Added
- Dynamic Vehicles 0.55.0 -> 0.59.1 (adds a required `dynamicterrain>=0.28.0`
  dependency and an optional `dynamicatmosphere>=0.20.2-alpha.1` one; both
  are satisfied by this same bump).
- Dynamic Terrain 0.22.3 -> 0.28.0 (surface-override datapack API).
- Dynamic Whips 0.7.0 -> 0.9.0 (adds the Harpoon weapon; requires
  `sable>=2.0.5` and `magicavoxelloader>=0.1.2`, both already satisfied).
- Balm 21.0.65 -> 21.0.66, Create Aeronautics 1.3.1 -> 1.3.2, Integrated API
  1.8.2 -> 1.9.1, Lithostitched 1.8.0+beta4 -> 1.8.1, Nature's Spirit 2.2.5
  -> 2.3.0, Power Grid 0.6.1 -> 0.6.2, Sable 2.0.5 -> 2.0.6, Serene Seasons
  10.1.0.3 -> 10.1.0.9 (beta release; flagged for its own stability review),
  Supplementaries 4.0.1 -> 4.0.2, Tectonic 3.0.26 -> 3.0.28.

### BREAKING
- Dynamic Atmosphere 0.20.3-alpha.1 -> 0.34.0-alpha.1. Discards saved Ender
  Gas with no migration and renames `enderGas.*` configs to
  `obsidianPowder.*`; changes lava-generator water consumption; renames
  existing client/server Dynamic Atmosphere configs aside and regenerates
  defaults. Approved on MINECRAFT-378 for this bounded test rollout only,
  paired with a hosted test-world reset and a recoverable backup of the old
  world before the new world is generated.
