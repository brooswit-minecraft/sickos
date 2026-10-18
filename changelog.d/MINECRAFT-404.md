bump: minor

### Changed
- Dynamic Vehicles 0.63.1 -> 0.65.1: adds vehicle radios with
  server-configured stations and cycling (MINECRAFT-394), and a separate
  flatland (superflat) vehicle dimension with a there-and-back garage
  command (MINECRAFT-397). Also carries the test-isolation correction to
  `SettlementFounderGameTests` (MINECRAFT-393) that landed alongside the
  garage work. Deliberately excludes the still-open drivetrain rework
  (MINECRAFT-401, PR #172, unmerged) and the unfinished Pathfinder
  chosen-chunk-without-accessibility-checks change (MINECRAFT-403, still
  in progress, no PR yet) -- this pin is the exact v0.65.1 release head,
  which does not contain either.
- Isometric Camera 0.1.0 -> 0.2.0 (client-only): widens the isometric
  camera framing to roughly 1.5x the previous default distance
  (MINECRAFT-402).

Dynamic Backpacks, Dynamic Atmosphere, Dynamic Terrain, Dynamic Population,
Battle Axes, Despawn, Skateboards, and Whips are unchanged. Excludes
MINECRAFT-401 (drivetrain rework), MINECRAFT-403 (Pathfinder chunk
accessibility), and MINECRAFT-390 (population Kings/combat) as explicitly
unfinished and out of scope for this batch.
