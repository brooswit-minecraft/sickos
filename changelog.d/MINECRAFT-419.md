bump: minor

### Added
- Immersive Caves 1.4.2 (NeoForge 1.21.1 native build -- not the Forge
  1.20.1 jar a prior lookup mistakenly flagged as the only available one).
- Structurify 2.0.43 (structure placement/overlap/biome control, replacing
  the need for datapack-based structure configuration), with its required
  client-side dependency YetAnotherConfigLib (YACL) 3.8.2.
- Tectonic Layers 2.0.1: stacks the Nether/End/Caves directly into the
  Overworld as a continuous, portal-less layout. Requires Tectonic (already
  pinned) and Lithostitched 1.6.0+ (already pinned, currently 1.8.1). The
  jar's own neoforge.mods.toml declares Amplified Nether as an `optional`
  dependency (will not block loading), but the mod's Modrinth listing
  separately describes it as needed for the deep-Nether layer to use "the
  real Nether source terrain" rather than a fallback -- i.e. optional to
  load, but a real functional tradeoff to skip. Not added here since
  nothing else in the pack needs it and it wasn't requested on its own;
  flagging the tradeoff rather than silently omitting it.

**Breaking: Tectonic Layers is a world-generation/chunk-conversion change,
not a drop-in-safe addition.** Per its own Modrinth listing: "It changes
dimensions, terrain generation, chunk conversion, portals, lighting, fog,
and End behavior... Back up your worlds before installing or updating...
For the best results, use this in a new world. Existing worlds may have
already-generated chunks that do not fully match the stacked layout." This
PR does not claim "existing chunks are left alone" -- that was an earlier,
unverified assumption and has been corrected. No world migration/reset has
been performed or decided by this PR; whether and when to reset the live
server world is a separate decision tracked on MINECRAFT-419, coordinated
with the release owner, and requires a preserved backup before any
irreversible action.

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
