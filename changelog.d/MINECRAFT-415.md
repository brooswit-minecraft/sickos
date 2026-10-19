bump: minor

### Changed
- Dynamic Vehicles 0.66.2 -> 0.67.0: Pathfinder's checkpoint HUD now
  reuses Dispatcher's waypoint presentation (world-projected marker,
  off-screen edge clamping, distance readout, compact dark backing)
  instead of the old fixed-corner arrow/count/countdown overlay, so
  navigation stays in view instead of requiring a corner glance
  (MINECRAFT-411). Dispatcher's own threat coloring and legacy-HUD
  toggle are unchanged; the default Pathfinder corner overlay is
  suppressed now that the shared marker replaces it. Server-authoritative
  checkpoint order, count, deadline, dimension/expiry handling, saved
  routes, and exactly-once rewards from MINECRAFT-409 are preserved.

Dynamic Population stays at 0.3.0-alpha.1, Isometric Camera stays at
0.3.0 (the MINECRAFT-408 focus-anchor correction remains open and
excluded), and all other pins are unchanged.
