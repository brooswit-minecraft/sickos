# Sickos

See [CONTRIBUTING.md](CONTRIBUTING.md) for version categories and the automated
release process. Features advance minor; fixes advance patch.

A [Create](https://modrinth.com/mod/create)-focused Minecraft **1.21.1 / NeoForge
21.1.250** modpack, built from the [schematic](https://github.com/brooswit-minecraft/schematic)
template below. Pinned mods live under `mods/`; `Rediculous Ore Generation` and
`Immersive Weathering` are intentionally omitted — neither has a 1.21.1 release.
`Project Atmosphere` (and its library dependency, GabouLibs) is also omitted: Modrinth
stopped indexing both as of 2026-09-10 (see `docs/unknown-files-options.md`), so the pack
can no longer pin them without triggering the Modrinth App's "Unknown files" warning.

## Dynamic Atmosphere

**Sickos 0.23.0 is a breaking update. Back up your world before upgrading.**
Dust, Ender Gas, Exhaust, Void Gas and Slime atmosphere data saved by earlier
versions is cleared when the world loads; there is no migration by design.
The old `violence` server and client config sections reset to their defaults
under a new `voidGas` section, so re-apply any tuning you had. The client and
the server must run the same version; the network protocol changed. Smoke is
lighter in this version: if you played earlier versions, your client keeps
its old, saved `smokeOpticalDensity` setting until you open
`config/dynamicatmosphere-client.toml` and set it to `2`, or delete that file
to take the new default. This is a client-side visual setting only; it does
not affect the server or world data.

Sickos 0.23.2 pins Dynamic Atmosphere 0.20.2-alpha.1 for client and server.
The Vapor spawn gate is scoped to the Overworld only: natural and chunk-generation
monster spawns in the Nether and End no longer require dense local Vapor and follow
vanilla rules again. The Overworld rule (qualifying terrain above, or Vapor strictly
more than half full) is unchanged, and Endermen and Ender Gas are untouched.
Create fan transfers run independently every five seconds, without a skip roll.
Positive RPM pulls evenly from the five non-facing neighbors before pushing forward.
Negative RPM draws from the facing neighbor before distributing evenly to the other
five. Intake works from an empty fan cell, and blocked output retains intake.
Fans affect every material's 4x4x4 cells: Vapor, Smoke, Dust, Exhaust, Ender Gas,
Void Gas, and Slime. Void Gas and Slime became fan-transportable once they moved
onto the same 4x4x4 cell size as the rest.
Destinations with empty space can be overfilled, causing normal pressure handling
and possible block destruction. Fan cadence and movement counters are in status.
Ender Gas now uses 4x4x4 cells, the same size as every other atmosphere material.
Random full-moon Ender Gas bursts are removed, while portal and other sources remain.
Lava now produces one tenth as much Smoke (4 units instead of 40), including
add/remove events. Existing material is preserved.
This minor gameplay release enables all seven independent materials: Vapor, Smoke,
Dust, Ender Gas, Void Gas, Exhaust, and Slime. Producers and interactions include
movement dust, explosion smoke, portal gas, crop growth, suffocation, and mob spawning.
Smoke has twice the optical density; Void Gas and Slime have four times; Ender Gas has 40x.
All materials share configurable 200-tick simulation and 300-tick scheduled production
intervals, and now share the same 4x4x4 cell size. Crying Obsidian produces Ender Gas.
Fan intake and output each share a budget of 1.0 material per absolute RPM per pass.
A 256 RPM fan requests up to 256 units per stage per supported material, bounded by
available material and the numeric storage ceiling, not spare capacity. Shortfalls
redistribute and integer remainders rotate to avoid fixed-axis bias.
The mod fixes X/Z and source-order bias in spreading.
Smoke interaction chances are ten times their old defaults,
capped at 100%. Gameplay and rendering settings are configurable without rebuilding.
Pressure can break neighboring blocks with a chance proportional to the source
cell's empty space. Liquids count as capacity, but liquids and bedrock prevent downward
transfers. Update both sides together for protocol 10; no world reset is required.
Peaceful Nights is removed: natural surface hostile spawning instead requires
more than 50% Vapor fullness, without consuming material, and retains other normal
spawn restrictions. Existing worlds and accumulated material are preserved.
The preceding rendering optimization remains: near-volume rendering uses four
slices instead of eight while preserving integrated opacity and skips empty geometry.
The preceding 0.16.0 minor release added snow/ice vapor.
Sampled surface snow and ice add 40 material without removing blocks, using the
existing producer gate and WORLD_SURFACE heightmap.
Each due simulation turn has a 50% chance to skip work until its next normal turn;
the 200-tick base schedule remains. Loaded-chunk capacity caching avoids repeated
terrain scans until air occupancy changes, and redundant sync sorting/persistence
rewrites are removed. No world reset or accumulated-atmosphere clear is performed.
The fluid-transport false-emission fix remains enabled.
Biome-driven evaporation remains; existing accumulated atmosphere is not removed.
Loaded-chunk producer passes run every 15 seconds with a 10% per-chunk gate.
Vapor volumes use Minecraft's current fog/horizon color; Smoke is black, replacing
local light-based grayscale and distance color blending. Loaded-chunk producers remain.
Condensation behavior
per due check and the persistent client visual cache are retained.

**BREAKING behavior: 0.9.0 includes default-enabled destructive pressure, which
can damage terrain and player builds without claim/protected-area support.
Back up your world before upgrading. A downgrade does not restore broken blocks;
restore the backup to undo terrain damage. This is a minor bump under the 0.x
breaking-change policy. This release retains this terrain-damage risk;
hosted runtime/client verification is left to user testing.**

**New water condensation also changes the world: water sources flow normally
and can wet builds. Back up worlds before upgrading.** On each cell's scheduled
check, fullness above 50% gives a linear chance: 0% at 50%, 5% at 75%, and a
maximum of 10% at or above 100%. At or below 50%, no placement occurs. A successful
roll tries one water source at a random air block in the same cell, never solids.
Only successful placement consumes 25% of the current material, rounded down
with a minimum of 1 unit. No air or failed placement consumes nothing. Ultrawarm
dimensions, including the Nether, skip both placement and consumption. This uses
the fixed 200-tick (10-second at 20 TPS) simulation cadence, subject to work budgets; it is
not Minecraft rain. No data or world reset is required.

Water fog, clouds, cloud-height rain emissions, and dark exposed ground feed 4x4x4-block cells. The new
grid equalizes fullness across six face neighbors, with capacity proportional to
vacant air blocks and opacity based on fullness. Zero-air cells block transfer;
these coarse checks do not simulate exact airtight walls. Overfull excess seeks
nearby capacity farther outward within bounded loaded-area searches. Confirmed
blockage can trigger bounded pressure destruction; unknown unloaded boundaries
and exhausted budgets leave work pending, never authorize destruction. The weakest-hardness
eligible source-cell block breaks with drops; once source blocks are gone,
relief proceeds outward. Each broken block adds 1 material unit. Intrinsically
unbreakable blocks are exempt; excess that still cannot escape remains blocked
and reported, not deleted.

There is no natural decay: daylight stops the dark-ground source but does not
clear existing fog. Simulation now uses fixed 200-tick checks (10 seconds at
20 TPS), replacing the old size-based 250-tick cadence. Producers independently
schedule passes every 300 ticks (15 seconds at 20 TPS) across all loaded chunks.
Each chunk has a random 10% default gate and one random X/Z column per pass.
A bounded fair queue allows backlog, so a scheduled pass need not finish within
15 seconds. No chunks are force-loaded. Cache/render/sync intervals are unchanged.
Each passed rain check splits 320 units: a random integer 0..320 goes to ground,
and the remainder to a uniformly random height between ground and Y=192.
The total is not doubled. High-terrain clouds and dark exposed-ground sources remain.
Sampled water now evaporates: plain water fluid blocks become air; waterlogged
blocks retain their host with WATERLOGGED cleared. Non-water solids and unsupported
water-containing hosts are preserved. This removes real water, including condensed
water, and natural fluid updates may refill it. No world reset or migration is required.
Evaporation traces contiguous water down the sampled column and targets its bottom.
If bottom water is directly above magma, it bypasses the temperature roll and also
targets the original surface, once if both coincide. The outer 10% chunk gate remains.
Otherwise, scheduled water evaporation gets a second chance
of `clamp(biome temperature / 2, 0, 1)`: temperature 0.8 gives 40%, 2 gives 100%,
and 0 or below never evaporates. Only the scheduled producer uses this roll.
Every successful non-transport water-to-nonwater mutation, including manual removals, emits
`round(10 + 70 * clamp(biome downfall, 0, 1))` material units (10 dry to 80 wet).
Downfall is a biome humidity proxy, not instantaneous rain or weather; climate
comes from the loaded chunk's biome. Humidity is captured at removal and queued
amounts are added without a second producer emission. Ordinary water-level
changes, failed mutations, and chunk unloads do not trigger this source.

Fluid transport during vanilla/Flowing Fluids fluid ticks does not emit removal
material. The fluid-tick scope includes Flowing Fluids 1.0.6's injected movement
and always clears on return or exception. Direct bucket/removal, block replacement,
and scheduled atmospheric evaporation outside transport still emit by humidity.
This fix does not delete accumulated atmosphere or reset worlds; existing material
and simulation backlog remain.

After bounded spreading, a selected due cell with at most 10 units can move its
entire amount into an existing, loaded cell on one of the four horizontal faces or directly below (never above), with strictly more
material and enough free capacity for the whole amount. Equal amounts never merge.
Prefer the largest eligible destination with deterministic ties; no new cell,
chunk load, or pressure overflow is created. The empty source is removed and both
changes are persisted and synchronized. Solitary or blocked cells retain material.

Live cell visibility follows Minecraft's tracked chunks and effective client render
distance, loaded chunks, and frustum, without fixed atmospheric radii or
nearest-cell caps. Protocol 6 requires updating both client and server together;
large snapshots complete with world identity, scope, and chunk freshness. Delta/full sync
remain 20/200 ticks; work remains at most 128 source cells per tick. Pressure is
still limited to four attempts per sampling interval.
Sparse amounts save with Minecraft chunks and restore on
reload/restart, with capacity recomputed. Unload releases the simulation mirror;
there is no range-based deletion or global 1,024-cell cap. No world reset is
required, but both atmospheric amounts and terrain damage persist. Update
client and server together. Operators can use `/dynamicatmosphere status` and
`/dynamicatmosphere demo` for runtime checks; this remains an alpha simulation.

Client rendering uses three non-overlapping LOD bands. With Minecraft client
view distance `V` expressed in blocks, render 4x4x4-block volumes below `V/2`,
8x8x8 in `[V/2, V)`, and 16x16x16 in `[V, 2V]`. Smoke uses the same sizes
and bands. Both stop at 2V, including cached fallback. Every LOD uses the same
detailed slice spacing as nearby atmosphere; distant cells are still aggregated.
Each coarser volume recursively averages eight children, including empty volumes;
coarse parents are not drawn on top of their finer children. This reduces the
number of volumes and slices at distance, not server simulation resolution.
Actual performance requires user verification; no measured FPS improvement or
runtime verification is claimed. Cache/render/sync intervals remain unchanged.
Aligned boundary volumes can remain finer; selection is spatially bounded and
cached in 16-block camera regions. New views use temporary coarse cached
coverage while refining, and unloaded near chunks retain 16-block cached fog.
Parents and detailed children never render over each other.
Vapor uses Minecraft's current fog/horizon color from one snapshot per frame;
Smoke is black. Mixed-material slices merge far-to-near across bounded GPU batches.
The Vapor disk cache format is preserved; Smoke has independent client state.

The client visual cache retains previously seen areas across sessions and
adds coarse far Vapor out to twice the client view distance. It is approximate
and can be stale, not server simulation or a way to load distant chunks. A stable
world UUID in server SavedData separates worlds; a newly reset world gets a fresh
UUID. No reset is required for this upgrade.

Client data lives under `gameDirectory/dynamicatmosphere-cache`, keyed by hashed
server/world/dimension/layout identity. Changed chunks are written atomically
every 10 seconds and on disconnect. Disk limits are 64 MiB and 8,192 files;
RAM restore is limited to 200,000 cells. Fresh server observations supersede
cached visuals for their scope; cached visuals never restore server material.
Local runtime tests are intentionally skipped. CI and hosted status verification
do not replace user-run server/client visual and save/reload checks.

## HarvestCraft

Sickos 0.3.0 adds Pam's HarvestCraft 2 Food Core, Crops, Trees, and Food Extended
on both client and server, using the author's NeoForge 1.21.1 Modrinth builds.
Existing worlds are retained. Explore newly generated chunks for natural crop
gardens and fruit trees; existing terrain is not regenerated. Clients must update
to the same pack release as the server.

The separate **Reset hosted world** workflow is destructive, manual maintenance:
dispatch with `confirm=RESET` only when intentionally starting over. It waits for
the shared deployment lock, selects a fresh world, restarts and verifies it,
grants `brooswit` operator access, then deletes the previous world without backup.
It never runs on releases. Normal updates preserve the world.

Everything from here down is inherited from the template, kept intact so future
`git merge template/main` pulls stay cheap.

## Default multiplayer server

Fresh installations include **Sickos** at `breezy-trident848.modrinth.gg` in
the multiplayer list. Client-only Default Options and Balm load the bundled
`config/defaultoptions/servers.dat` only when the player has no existing server
list. Updates preserve player-added servers; no graphics or keybinding defaults
are bundled.

To change the default entry, edit `scripts/generate-default-servers.py`, run
`uv run --with nbtlib scripts/generate-default-servers.py`, then `make refresh`.
Commit the generated file and index with a `pack.toml` version bump; CI packages
and publishes it through the normal release workflow.

## Managed Modrinth listing

The public Modrinth listing is repository-owned. Edit structured fields in
`.modrinth/project.json` and formatted copy in `.modrinth/description.md`.
After a merge to `main`, `.github/workflows/modrinth-sync.yml` updates the
Modrinth project and verifies the resulting values by reading them back.

Do not put credentials in either file. CI reads `MODRINTH_PROJECT_ID` from a
repository variable and `MODRINTH_TOKEN` from a repository secret. Moderation,
permissions, members, monetization, gallery media, and deletion remain manual
controls and are intentionally outside the sync schema.

---

# schematic

A [packwiz](https://packwiz.infra.link) modpack **template** for Minecraft **1.20.1 /
Forge** (both are defaults you can change) — clone it, edit a couple of fields, and you
have a modpack project that validates and builds itself on every push, with release and
server-update automation available too. CI, release, and server-update are provided as
**reusable GitHub Actions workflows**, called — from the thin stubs already sitting in
this repo — at their upstream location in this template, pinned `@v1`, so you get all
three without writing any workflow logic yourself.

[brooswit-factory/schematic-example](https://github.com/brooswit-factory/schematic-example)
is the living example built from this template: a real pack (starting from the [Create](https://modrinth.com/mod/create)
mod) that began life as a clone of this repo. Look there for what a filled-in version of
this template looks like.

## Getting started

```sh
git clone https://github.com/brooswit-minecraft/schematic.git <yours>
cd <yours>
git remote rename origin template
git remote add origin <your repo url>
git push -u origin main
```

`template` stays as the upstream you can pull future improvements from (see
[Updating from the template](#updating-from-the-template) below); `origin` becomes your
own repo.

## Rename checklist

Almost nothing to rename. Edit the pack identity in `pack.toml`:

```toml
name = "my-modpack"      # -> your pack's name
author = "your-name"     # -> you
```

and, if you want a Minecraft version or mod loader other than the defaults, the
`[versions]` block (`minecraft`, `forge`) too — the reusable workflows derive the
Modrinth game-versions/loaders from these.

Then regenerate the index and commit:

```sh
make refresh
git add pack.toml index.toml
git commit -m "Rename pack"
```

That's it. The build artifact name (`<name>-<version>.mrpack`) is derived from
`pack.toml` automatically, and so are the Modrinth game version and loader it publishes
under — nothing to rename in the Makefile or the workflows. The Modrinth project you
publish *to* is a separate thing: it's set by the `MODRINTH_PROJECT_ID` repo variable,
not by anything in `pack.toml` (see [Secrets & variables](#secrets--variables) below).

Nothing under `.github/workflows` needs editing or deleting — see
[Template-only files](#template-only-files) below for why.

Verify with `grep -ri schematic .`: the only remaining hits should be this README, the
`uses: brooswit-minecraft/schematic/.github/workflows/reusable-<name>.yml@v1` line in
each of `ci.yml`, `release.yml`, and `server-update.yml`, and the
`if: github.repository == 'brooswit-minecraft/schematic'` guard in `tag-v1.yml`. **Leave
all of those alone** — the `uses:` lines point at this project's upstream reusable
workflows, not at your own pack, and the `tag-v1.yml` guard is what keeps that
template-only file from creating a tag in your repo (see
[Template-only files](#template-only-files) below). Renaming any of them will break the
thing they exist to do.

Optionally, you can also replace the copyright holder in `LICENSE` with your own name —
that's not required for anything to work; it's your call whether the template's MIT
license and holder should carry over to your fork.

## Updating from the template

### Pulling template changes

This repo keeps evolving — Makefile fixes, README clarifications, improvements to the
reusable workflows. Pull those into your own pack with:

```sh
git fetch template
git merge template/main                                  # expect conflicts
git checkout ORIG_HEAD -- pack.toml index.toml mods      # your pack content wins, conflicted or not
git checkout --theirs -- <other conflicted files you have not customised>
make refresh
git add -A
git commit
```

`ORIG_HEAD` is your branch tip as it was immediately before the merge — checking out
`pack.toml`, `index.toml`, and `mods/` from it restores **your own content** there no
matter what happened during the merge.

That last point matters: git only reports a conflict on a file **both sides changed**.
If the template deletes or changes a file you never touched — most importantly a mod
file in `mods/`, or `index.toml` — there's no conflict, and the merge silently applies
the template's version, which for a deleted mod means it's just gone with nothing to
resolve. That's the intended behaviour for tooling files (`Makefile`, workflow stubs)
that should track the template automatically, but it's exactly why the
`git checkout ORIG_HEAD -- pack.toml index.toml mods` step above is unconditional
rather than only for conflicted paths — it protects your pack content whether or not git
flagged a conflict on it.

One caveat: `git checkout ORIG_HEAD -- ...` only restores paths that existed on your
branch; it never deletes, so a file the template adds under `mods/` (such as its
placeholder `mods/.gitkeep`) is kept — harmless, because `.packwizignore` keeps it out
of the exported pack.

For the `--theirs` line, "other conflicted files you have not customised" typically
means `Makefile` — take the template's version of it unless you've made local edits
worth preserving. `README.md` is the
one file you have almost certainly customised — reconcile it by hand: keep your
pack-specific prose and fold in template improvements where they still apply. If you
previously deleted `.github/workflows/tag-v1.yml` locally and the template has since
changed it, you'll see it as a delete/modify conflict instead of a clean merge; per
[Template-only files](#template-only-files) above, you don't need to delete it in
the first place, so the simplest fix is to keep the template's version
(`git checkout --theirs -- .github/workflows/tag-v1.yml`) rather than re-deleting it.

### The pinned workflow stubs

`ci.yml`, `release.yml`, and `server-update.yml` each pin their `uses:` line to `@v1` —
a moving tag kept pointed at this repo's `main`. Fixes and improvements to the reusable
workflows they call reach your repo automatically, with **zero merge effort** on your
part.

`v1` promises backwards-compatible inputs, secrets, and variable names; anything that
would break your workflow ships as a `v2` instead. If you'd rather not receive moving
updates, pin a specific tag or commit SHA in place of `@v1` in your stub's `uses:` line.

## Template-only files

One file under `.github/workflows` is template-only, and safe to leave in place:

`tag-v1.yml` keeps the `v1` tag on **this** repo pointed at its own `main`. It is
guarded by a `github.repository` check, so it is inert in any repo cloned from this
template — the job is skipped entirely, so it creates no tag in your repo.

This repo does **not** keep local `reusable-ci.yml`, `reusable-release.yml`, or
`reusable-server-update.yml` copies. Your stubs (`ci.yml`, `release.yml`,
`server-update.yml`) call those `workflow_call` definitions **upstream**, at
`brooswit-minecraft/schematic/.github/workflows/reusable-<name>.yml@v1` — a local copy
would never run, so none is kept. Unlike `tag-v1.yml` above, these three have no guard
that makes them harmless to leave in place, so if a future `git merge template/main`
re-adds one as a delete/modify conflict (the template's own history predates this
cleanup), resolve it by deleting the file again rather than keeping the template's
version — a one-time conflict per file, not a recurring one.

`ci.yml`, `release.yml`, and `server-update.yml` are the three stubs you, as a consumer
of this template, need to care about — `tag-v1.yml` above needs no attention at all.

## Secrets & variables

Everything below is optional. With none of them set, `ci.yml` still builds and uploads
the `.mrpack` as a workflow artifact, `release.yml` still builds and attaches it to the
GitHub Release, and any workflow step that needs a secret **skips cleanly** (the run
still finishes green) when it isn't configured.

| Name | Kind | Used by | Purpose |
|---|---|---|---|
| `MODRINTH_TOKEN` | secret | `release.yml`, `server-update.yml` | Auth token for publishing to Modrinth and updating a Modrinth-hosted server |
| `MODRINTH_PROJECT_ID` | variable | `release.yml`, `server-update.yml` | Identifies which Modrinth project to publish to / follow |
| `MODRINTH_SERVER_ID` | variable | `server-update.yml` | The Modrinth-hosted server to keep in sync |
| `SICKOS_DISPATCH_APP_ID` | variable | `da-auto-bump.yml` | GitHub App ID used to mint the push token for an automated DA bump. Not set today. |
| `SICKOS_DISPATCH_APP_PRIVATE_KEY` | secret | `da-auto-bump.yml` | Private key for the App above. Not set today. |
| `SICKOS_DISPATCH_TOKEN` | secret | `da-auto-bump.yml` | Fine-grained PAT fallback push credential, used only if the App pair above is not configured. Not set today. |
| `SICKOS_AUTOBUMP_PAUSED` | variable | `da-auto-bump.yml` | Pause switch for the automated DA bump. Unset today (automation runs). |

None of the three `SICKOS_DISPATCH_*` credential entries above exists in
this repository yet, so every DA dispatch today takes the fail-closed
no-credential path described in
[Automated Dynamic Atmosphere bumps](#automated-dynamic-atmosphere-bumps)
below. `SICKOS_AUTOBUMP_PAUSED` is a separate switch, unrelated to
credentials: unset (as it is today) or any value other than exactly
`true` means the automation runs.

## Releasing

`.github/workflows/release.yml` cuts a release. To publish a new version:

1. Choose the bump using [CONTRIBUTING.md](CONTRIBUTING.md), update `pack.toml`,
   refresh and validate the pack, then push the change to `main`.
2. On the pack-changing push, the workflow:
   - reads the version from `pack.toml`,
   - builds `build/<name>-<version>.mrpack` with the same `make` targets used locally
     and in CI,
   - creates the GitHub Release and version tag, attaching the `.mrpack`,
   - publishes the same file to Modrinth, if Modrinth is configured (see
     [Secrets & variables](#secrets--variables) above).

You can also dry-run the whole build-and-package path without creating a Release, via
`workflow_dispatch`:

```sh
gh workflow run release.yml --ref <branch> -f version=0.0.1-test
```

This builds and uploads the `.mrpack` as a workflow artifact but skips the
Release-asset step (there is no Release object to attach to) and the Modrinth publish
step, the same as any run without Modrinth configured.

For an auto-bump push specifically (a commit carrying a `DA-Auto-Bump` trailer),
`.github/workflows/da-auto-bump-notes.yml` additionally edits that release's GitHub
body afterward; see [docs/release-notes-plumbing.md](docs/release-notes-plumbing.md)
for what it does and does not cover.

### Automated Dynamic Atmosphere bumps

`.github/workflows/da-auto-bump.yml` triggers on a `repository_dispatch`
sent by Dynamic Atmosphere's own release workflow (`event_type:
dynamic-atmosphere-released`), runs `scripts/bump-dynamic-atmosphere.py`
(see [docs/da-auto-bump.md](docs/da-auto-bump.md)), and, when it prepares a
real bump, commits and pushes it to `main` -- which triggers the ordinary
`release.yml` chain above, exactly as a hand-pushed version bump would.
Full contract, exit-code mapping, and the CI wiring: see
[docs/da-auto-bump.md](docs/da-auto-bump.md)'s "CI wiring" section. Logic
lives in `scripts/da-auto-bump-dispatch.py`
(`tests/test_da_auto_bump_dispatch.py`); the workflow YAML only wires
triggers, permissions, and step outputs.

**Pause switch.** The Actions repository variable `SICKOS_AUTOBUMP_PAUSED`
is checked before anything else -- before a token is minted or anything is
checked out. Exactly `true` pauses; unset or any other value runs.

```sh
# Pause: decline every dispatch until unpaused.
gh variable set SICKOS_AUTOBUMP_PAUSED --repo brooswit-minecraft/sickos --body true

# Resume:
gh variable delete SICKOS_AUTOBUMP_PAUSED --repo brooswit-minecraft/sickos
```

A paused run exits successfully and does nothing else, but a paused
dispatch is **not queued** -- Dynamic Atmosphere fires this dispatch once
per version. To catch up once unpaused: use "Re-run all jobs" on the
skipped run (it re-evaluates the pause variable and reuses that dispatch's
original payload), wait for a re-delivered dispatch, or rely on the
story-3 scheduled Modrinth poll backstop. Disabling the workflow itself in
the Actions UI is a harder stop than the pause variable: it silently
**drops** dispatches sent while disabled, with nothing to re-run and
nothing queued, so prefer the pause variable.

**Credentials.** A commit pushed with the default `GITHUB_TOKEN` does not
trigger `release.yml`'s push trigger, so this workflow mints its own push
token: a GitHub App token (`SICKOS_DISPATCH_APP_ID` +
`SICKOS_DISPATCH_APP_PRIVATE_KEY`, restricted to `contents: write`),
falling back to a fine-grained PAT (`SICKOS_DISPATCH_TOKEN`) if the App is
not configured. If exactly one half of the App pair is set, the run warns
loudly naming the missing half and falls back to the PAT if there is one.

**Fail-closed.** If the engine prepares a real bump and neither credential
is configured, the run fails with an error naming the exact missing
variable/secret(s), and uploads a workflow artifact holding the staged
patch, the notes file, and the summary JSON for inspection -- nothing is
pushed, and the artifact never contains a token. This is the path every
real DA release takes today, since none of the three credential names is
configured yet.

**Idempotency and concurrency.** A dispatch for an already-pinned version
exits successfully with no commit and no push. All dispatches share one
`concurrency` group with `cancel-in-progress: false`, so two dispatches
can never interleave into competing pushes -- but with
`cancel-in-progress: false`, only one *pending* run per group is kept: a
third dispatch arriving while one run is in progress and one is already
queued **replaces** the queued one. The guard prevents interleaving, not
loss; the story-3 poll backstop and Dynamic Atmosphere's own re-delivery
are the mitigation for a dropped dispatch.

**Evidence.** Every run (paused, no-op, bumped and pushed, bumped with no
credential, or failed) writes a job summary: the DA version, the Modrinth
version id, whether both hashes were verified, the category received and
which mapping rule fired, the previous and new pack version, the Modrinth
vouch-check result, the notes file path, whether `listing_review_required`
fired, and the pushed commit sha (or why there isn't one).

**Commit trailer.** A pushed bump's commit message ends with a
`DA-Auto-Bump: <da_version> <modrinth_version_id>` trailer, alone in its
own final paragraph (a blank line before it) -- this exact shape is what
`da-auto-bump-notes.yml` above gates on; see
[docs/da-auto-bump.md](docs/da-auto-bump.md)'s "Trailer format" section
for why the shape matters.

**Listing review issue.** When the engine sets `listing_review_required`
(non-mechanical listing text may now be stale -- see
[docs/da-auto-bump.md](docs/da-auto-bump.md)), the push still happens
exactly as normal; `listing_review_required` never holds or fails a push.
After a successful push, the workflow additionally opens one GitHub issue
listing the exact flagged lines so a human sees it without opening
Actions. A failure to open that issue only warns; it never fails the run.

## Deploying to a Modrinth Server

`.github/workflows/server-update.yml` runs after the Release workflow succeeds, or on demand
via `workflow_dispatch` (with an optional `version` input; it otherwise falls back to
the `version` field in `pack.toml`). Publishing to Modrinth happens on release (see
[Releasing](#releasing) above); a [Modrinth-hosted server](https://modrinth.com/servers)
follows the published project automatically — this workflow re-points it at the
newly-published version and restarts it.

One-time setup:

1. Buy a Modrinth Server.
2. Install the pack on it once from your Modrinth project, so its upstream points at
   your project.
3. Find the server's id: it's the UUID in the dashboard URL
   `modrinth.com/hosting/manage/<server_id>`. It's also returned as `server_id` by
   `rinth servers list` (or `GET https://archon.modrinth.com/modrinth/v0/servers`).
4. Set the `MODRINTH_SERVER_ID` and `MODRINTH_PROJECT_ID` repo variables (and
   `MODRINTH_TOKEN`, if you haven't already set it for publishing).

This **skips cleanly** (the workflow still finishes green) when `MODRINTH_SERVER_ID` /
`MODRINTH_TOKEN` aren't configured, so this works out of the box on a fresh clone — you
opt in by adding the variable/secret above whenever you're ready. Once configured,
`MODRINTH_PROJECT_ID` is required too — the workflow fails loudly rather than skipping
if it's missing. The workflow re-points and restarts the server via the
[`rinth`](https://github.com/brooswit-minecraft/rinth) CLI, invoked at a pinned version
through `bunx`, so nothing needs installing in your repo.

### Live server tuning

Repository-owned settings in `server-config.json` are synchronized by
`.github/workflows/server-tuning.yml` whenever that file changes, or on manual
dispatch. The workflow reads `level-name` from the hosted `server.properties`, then
requires exactly one existing Dynamic Atmosphere config: the modern
`config/dynamicatmosphere-server.toml` path or the legacy
`<level-name>/serverconfig/dynamicatmosphere-server.toml` path. It updates only the
listed fields through strict-host-key SFTP and verifies the atomic upload. It uses the same `SERVER_SFTP_*` repository
variables and secrets as the SFTP deployment route and shares that route's
concurrency lock. NeoForge reloads this server config; the tuning workflow does not
restart Minecraft.

`runtime.simulationSkipChance` applies to every atmospheric material (default
0.75). `enderGas.portalBlockEmission` sets the integer amount emitted per portal
block per producer pass (default 100, zero disables it). These join
`integrations.createFanTransportPerRpm` in the repository-owned tuning file.
The independent fan pass uses `integrations.createFanIntervalTicks` (100 ticks,
five seconds) and `integrations.maxFanChunksPerTick` (32). It never uses the
ordinary simulation skip setting. These values are also synchronized by CI.

## Working on the pack

World resets are separate from releases. The manual `Reset world` workflow requires
the exact current world directory and a new, nonexistent `world-`-prefixed directory.
It requires an empty server, saves, selects a fresh survival world, restarts, verifies
the new world was created, then deletes the old world. This deliberately deletes old
builds, inventories, and progression; normal deployments never invoke it.

```sh
packwiz modrinth add <slug>     # e.g. packwiz modrinth add jei
packwiz remove <name>           # e.g. packwiz remove jei
```

Both commands update `index.toml` for you. Commit the resulting `mods/<name>.pw.toml`
along with the changed `index.toml` and `pack.toml` (`packwiz refresh` writes the new
index hash into `pack.toml`'s `[index]` block) — **CI fails if the index does not
match what is on disk.**

packwiz also has `packwiz curseforge add` and `packwiz url add` if a mod is not on
Modrinth. Note that `packwiz modrinth export` restricts downloads to domains Modrinth
allows, so URL-sourced mods may not be exportable.

```sh
make check     # fails if the committed index.toml is stale
make refresh   # rewrite index.toml after changing files by hand
make build     # -> build/<name>-<version>.mrpack
make clean     # remove build/ and bin/
```

### Prerequisites

- [packwiz](https://packwiz.infra.link) — the pack manager. It publishes no tagged
  releases, so this repo pins a commit SHA (`PACKWIZ_REF` in the `Makefile`).
- [Go](https://go.dev) 1.23+, only if you want `make tools` to install that pinned
  packwiz for you. If you already have a packwiz on your `PATH`, it is used instead.

```sh
make tools     # installs the pinned packwiz into ./bin (needs Go)
```

### What is in the repo

```
pack.toml                           pack metadata — name, version, Minecraft and Forge versions
index.toml                          generated file list with hashes; do not edit by hand
mods/                                one *.pw.toml file per mod, pinning a version and its hash — empty by default
.packwizignore                      repo files (docs, CI, Makefile) kept out of the pack
.github/workflows/ci.yml            validates the index and builds the .mrpack on every push
.github/workflows/release.yml       cuts a release (see Releasing above)
.github/workflows/server-update.yml keeps a Modrinth-hosted server in sync (see Deploying to a Modrinth Server above)
.github/workflows/tag-v1.yml        template-only (see Template-only files above)
Makefile                            the build entry point, shared by humans and CI
```

No jars are committed — `.pw.toml` files reference downloads by URL and hash, and
packwiz fetches them at export time.

### CI

`.github/workflows/ci.yml` runs on every push to `main` and on every pull request. It
installs the pinned packwiz, fails if `packwiz refresh` produces a diff, builds the
pack, and uploads the resulting `.mrpack` as a workflow artifact.

## Removing a path you don't need

- No Modrinth publishing or releases? Delete `.github/workflows/release.yml`, the
  [Releasing](#releasing) section above, and the `MODRINTH_TOKEN` /
  `MODRINTH_PROJECT_ID` rows in the [secrets & variables](#secrets--variables) table.
- No server to keep in sync? Delete `.github/workflows/server-update.yml`, the
  [Deploying to a Modrinth Server](#deploying-to-a-modrinth-server) section, and the
  `MODRINTH_SERVER_ID` row in the [secrets & variables](#secrets--variables) table
  above.
