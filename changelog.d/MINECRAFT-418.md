bump: minor

### Changed
- Isometric Camera 0.3.0 -> 0.4.0 (client-only): the isometric F5 perspective
  now requests 4x vanilla's base detached distance (16 blocks unobstructed
  by default, up from 12), and pivots around a point 4 blocks below the
  player's eye instead of the eye itself. Wall/roof/terrain collision still
  shortens the actual distance exactly as before -- including right down to
  standing on normal ground, where the lowered pivot itself sits inside
  solid terrain. Every other F5 perspective is unaffected (MINECRAFT-408).

Vehicles, Population, and every other pin are unchanged. This release
carries exactly the one camera pin; no other in-progress work is included.
