bump: minor

### Changed
- Dynamic Population 0.2.0-alpha.1 -> 0.3.0-alpha.1: adds persistent,
  visible faction Kings and threshold-driven population battles
  (>=500-density combat, a >250-density sunlight exception, and a
  day/night spawn-rate schedule). Explicitly unverified/open, not
  waived: King visual rendering, a real dedicated-server restart cycle
  for King identity, live below-threshold Fillager flee/recovery and
  promotion-while-panicking, live 499/500/501 density crossings on a
  real dedicated server, a decisive (non-inconclusive) multi-entity
  combat death, and installed third-party-mod attacker compatibility.
  This release is an explicit scoped user playtest build, not a claim
  that the above passed (MINECRAFT-390).
- Dynamic Vehicles 0.66.0 -> 0.66.2: Pathfinder now evaluates arrival
  every server tick instead of every 20 (a player/vehicle crossing the
  target chunk entirely between samples previously got no credit at
  all), adds a player-visible chat confirmation on intermediate
  checkpoints, and fixes the HUD guidance arrow to rotate relative to
  the actual rendered camera yaw instead of the player entity's yaw
  (MINECRAFT-409). The originally reported "stopped in the chunk,
  nothing happened" case's precise root cause remains unproven; the
  fixes address demonstrated gaps, not a confirmed reproduction of
  that report.

Isometric Camera stays at 0.3.0: the MINECRAFT-408 16-block/-4
focus-anchor correction is still an open, unmerged PR under re-review
and is deliberately excluded from this release. Dynamic Backpacks,
Dynamic Atmosphere, Dynamic Terrain, Battle Axes, Despawn,
Skateboards, and Whips are unchanged.
