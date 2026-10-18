bump: minor

### Added
- Dynamic Backpacks 0.2.0 -> 0.3.0: adds always-on emerald wallet
  consolidation (MINECRAFT-391).
- Isometric Camera 0.1.0 (new mod, client-only, MINECRAFT-376/396):
  a fourth F5 perspective -- a fixed-angle, Minecraft Dungeons-like
  isometric view with screen-relative WASD movement. Server-unsupported
  (client side only); joining without it on the client simply does not
  offer the new perspective.

### Changed
- Dynamic Vehicles 0.61.0 -> 0.63.1: delivery destinations now place only
  a dispatch board and villager instead of a full structure (MINECRAFT-393),
  and Pathfinder's regional checkpoint-circuit durability check now fails
  closed on an async IO-worker crash instead of silently treating it as
  "no damage" (MINECRAFT-379). Deliberately excludes the newer vehicle
  radio feature (MINECRAFT-394, now on `main`/v0.64.0+) and the unfinished
  superflat garage dimension (MINECRAFT-397, PR #169, still unmerged) --
  this pin stays at v0.63.1, the last release before either landed.
- Dynamic Atmosphere 0.35.0-alpha.1 -> 0.36.0-alpha.1: every atmospheric
  material now tints toward a configurable sun/moon colour by viewing
  angle, bounded so it never fully overrides the material's own colour
  (MINECRAFT-399). Carries the already-released 0.35.1-alpha.1 sandier
  Dust default (`dustColor` `0x8B4513` -> `0xD2B48C`) along the way
  (MINECRAFT-398) -- SPEC-default only: a client config generated before
  this bump keeps its old `dustColor` value until that line is edited or
  the file is regenerated. No action taken here on generated client
  configs; flagged for the client-side owner to handle.

Dynamic Population, Terrain, Battle Axes, Despawn, Skateboards, and Whips
are unchanged. Excludes MINECRAFT-394 (radio), MINECRAFT-397 (superflat
garage dimension), and MINECRAFT-390 (population Kings/combat) as
explicitly unfinished and out of scope for this batch.
