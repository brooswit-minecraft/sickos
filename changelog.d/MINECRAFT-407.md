bump: minor

### Changed
- Dynamic Vehicles 0.65.1 -> 0.66.0: the Pathfinder minigame now arrives at
  the chosen checkpoint chunk at any Y, with no terrain-accessibility,
  safe-landing, or chunk-move checks -- lava, water, caves, vegetation and
  partial-layer terrain are all valid checkpoints; reaching and making a
  checkpoint accessible is the player's job (MINECRAFT-403). Route
  identity/determinism, order, rewards and exactly-once credit are
  unchanged.
- Isometric Camera 0.2.0 -> 0.3.0 (client-only): doubles the active
  isometric camera's default framing distance from 6 to 12 blocks (3x
  vanilla's 4-block base, replacing the previous 1.5x). Yaw/pitch/focus,
  screen-relative movement, F5 transitions, inactive passthrough, and
  native collision/occlusion clamping are unchanged.

Dynamic Backpacks, Dynamic Atmosphere, Dynamic Terrain, Dynamic Population,
Battle Axes, Despawn, Skateboards, and Whips are unchanged. Deliberately
excludes MINECRAFT-401 (drivetrain rework, still an open unmerged draft PR),
MINECRAFT-390 (population Kings/combat, in progress) and MINECRAFT-406
(/dvflat exact-XYZ change, in progress/blocked, not published) -- these
pins are the exact v0.66.0/v0.3.0 release heads, which do not contain any
of them.
