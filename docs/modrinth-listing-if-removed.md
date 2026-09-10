# Corrected Modrinth listing text, IF this branch is ever merged

This is not applied anywhere. It is a draft correction to the live sickos
Modrinth listing (`description` and `body` on project `RuhnnPqO`), prepared
so that if a human picks this option, fixing the storefront text is a copy
edit instead of a fresh investigation.

Read live from `.github/workflows/rinth-listing.yml` (`workflow_dispatch`,
read-only) at 2026-09-10T19:37:55Z, before this branch changed anything.
Whoever applies this should re-read the live listing first in case it
changed since — see `docs/unknown-files-options.md` for the full record and
the caveats on this text.

## `description` field

No change needed. Current text does not name individual mods or a count:

```
An early build of a Create focused modpack for Minecraft 1.21.1 on NeoForge.
```

## `body` field

Two changes from the live text: the mod count in the opening line ("Eight"
to "Seven"), and removing the "Project Atmosphere" line from the Mods list.
GabouLibs was never named in this listing (it is Project Atmosphere's
library dependency, not one of the ten mods the human named), so its
removal needs no listing edit.

```markdown
## What this is

A Create focused modpack for Minecraft 1.21.1 on NeoForge. Seven mods plus their required dependencies, pinned to exact versions and hashes.

This is an early build. It is playable now, but treat it as a starting point rather than a finished pack.

## Where it is going

We have big plans for sickos. The pack will be improved regularly, and alongside the mods listed below we intend to write and release our own mods, built for the way we want this world to play. Those will show up here as they are ready.

## Mods

- Create
- Create: Power Grid
- Create: Aeronautics
- Flowing Fluids
- Countered's Terrain Slabs
- Tectonic
- Peaceful Nights

## Install

Download the .mrpack file from the Versions tab and open it in the Modrinth App, Prism Launcher, or any other launcher that reads Modrinth packs. The same file works for a server.

## Source

Built and published from github.com/brooswit-minecraft/sickos. Each release on this page is cut by CI from a pinned index file, so a version here matches a tag in that repository.
```

Nothing else on the listing (title, categories, icon, license) references either
mod by name as far as this read shows; this draft only touches `description`
and `body`.
