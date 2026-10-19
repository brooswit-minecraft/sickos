bump: minor

### Added
- Immersive Caves 1.4.2 (NeoForge 1.21.1 native build -- not the Forge
  1.20.1 jar a prior lookup mistakenly flagged as the only available one).
- Structurify 2.0.43 (structure placement/overlap/biome control, replacing
  the need for datapack-based structure configuration), with its required
  client-only dependency YetAnotherConfigLib (YACL) 3.8.2.

### Removed from this release: Tectonic Layers
Tectonic Layers 2.0.1 was pinned, then pulled after an actual disposable
server boot test (not just a dependency/metadata check) reproduced a
reliable, repeated crash: `LayeredBiomeSource.getNoiseBiome` (tectonic
layers) calls into Biolith's `EndBiomePlacement.sampleEndNoise`, which
throws a `NullPointerException` on `temperatureNoise` -- Biolith is
bundled inside this pack's existing Quark 4.1-487 pin (biolith-neoforge
3.0.14). This happened hundreds of times during ordinary spawn-chunk
generation on a brand-new world with no prior save, and crashed the
server outright both times it was tested. Tectonic Layers' own listing
already flags End/biome compatibility as "experimental"; this is that
risk materializing, not a rare edge case. Not shipping it until this is
resolved (upstream fix, a compatible Biolith/TerraBlender version, or
confirmation there's a config path around it) -- re-adding it needs a
fresh disposable boot test of its own, not just a dependency-closure pass.

### Excluded (not in this release)
Blocked by sickos/AGENTS.md's Modrinth-only policy ("Only add mods hosted
on Modrinth with native NeoForge builds... do not add CurseForge-only
jars"); CurseForge-only, no Modrinth project exists for them:
- Scorched Guns Neoforged + AsYasya's WW1 Weaponry (hard-requires
  Framework, which is CurseForge/GitHub-only -- no Modrinth path exists to
  ship these compliantly without it)
- Framework, Curios (both only needed as the above's dependencies)
- I Need Skating
- Create: Structured

These remain approved-scope per MINECRAFT-419 but need an explicit call on
amending AGENTS.md's hosting policy, or on dropping them, before they can
ship -- tracked on that ticket, not resolved by this PR.
