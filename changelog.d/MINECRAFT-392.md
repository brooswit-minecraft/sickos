bump: minor

### Added
- Dynamic Backpacks 0.2.0 (new mod, self-published, GitHub-direct pin):
  craftable material/utility backpacks that raise how much of the main
  inventory a worn chest-slot pack protects (half for Stick Pack, all for
  Leather Backpack and up); moving while over the protected amount risks
  dropping a random whole stack per spill check, standing still never
  spills. Plus an Emerald Backpack with a bounded ore-detector scan. See
  `docs/releases/0.60.0.md` for provenance and known limitations.

### Changed
- Dynamic Vehicles 0.59.1 -> 0.61.0: adds Motorboat, Chopper, and Biplane
  (simple arcade movement models, single-seat); fixes the G29 H-pattern
  shifter neutral release and makes the accelerator gear-aware; lava
  contact and sustained fire now damage vehicles through the existing
  health system. Two-client multiplayer-sync validation for the three new
  vehicles is a disclosed, not-yet-closed gap (see release notes).
- Dynamic Terrain 0.28.0 -> 0.28.2: generates layered snowy terrain over
  thin snow cover, and settles grass as dirt on a successful erosion move
  (erosion itself stays opt-in via `erosionEnabled = true`).
- Dynamic Atmosphere 0.34.0-alpha.1 -> 0.35.0-alpha.1: atmosphere physics
  no longer advances during a platform `/tick freeze` (0.34.1-alpha.1),
  and Vapor's rendered opacity now increases as the live fog colour
  darkens (night, dark biomes, weather), fixing Vapor reading as nearly
  invisible at night (0.35.0-alpha.1). In-game before/after visual
  verification of the opacity change is a disclosed, not-yet-closed gap
  (see release notes).

Dynamic Population stays at 0.2.0-alpha.1 (unchanged). Dynamic Battle
Axes, Dynamic Despawn, Dynamic Skateboards, and Dynamic Whips were audited
and found already current; no changes there.
