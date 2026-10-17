# Generalized mod auto-bump (MINECRAFT-362)

Status and design for "any managed mod's verified release should update
Sickos and cut a release," generalizing `docs/da-auto-bump.md`'s
Dynamic-Atmosphere-only engine to every Modrinth-tracked pin. This
document is honest about what is implemented today versus what remains --
see "Status" below before assuming any of this runs live.

## Enrollment: the pack's own pins are the mapping

`scripts/mod_eligibility.py`'s `discover_eligible_pins()` is the single
source of truth for "which mods are in scope." It scans `mods/*.pw.toml`
and splits them into:

- **Eligible**: a pin with an `[update.modrinth]` table (`mod-id` +
  `version`) -- packwiz's own update-tracking fields, reused rather than
  duplicated into a second enrollment file that could drift from the pack.
  At the time of writing this is 78 of the pack's pins.
- **Untracked**: everything else under `mods/`, for two distinct reasons,
  both reported explicitly rather than silently dropped:
  - `EXCLUDED_MOD_NAMES` (Herobrine API, Baritone): local-only client
    addons that must never enter the public pack. Neither currently has a
    pin at all; this is defence in depth against one being added by
    mistake.
  - No `[update.modrinth]` table: a GitHub-release-only first-party mod.
    At the time of writing this is `dynamic-despawn.pw.toml` and
    `magicavoxel-loader.pw.toml`. Verifying a GitHub release's identity
    needs a different mechanism (GitHub's release-asset API, not
    Modrinth's version-file metadata) -- **not implemented**; these two
    mods are not covered by anything in this document yet.

## Compatibility and no-downgrade resolution

`scripts/modrinth_compat.py`:

- `resolve_latest_compatible_version(mod_id, game_version, loader,
  fetcher)` queries Modrinth's version list for a project, filtered (both
  server-side via the query and re-checked client-side) to versions that
  declare the pack's pinned `game_version` ("1.21.1") and `loader`
  ("neoforge"), and returns the one with the highest semver
  `version_number`.
- `decide_update(pin, candidate, previous_version_number=None)` turns a
  resolved candidate into one of `no_candidate` / `already_pinned`
  (dedup by Modrinth version id -- a replayed/retried dispatch is a
  no-op) / `downgrade_refused` / `candidate`. The downgrade check only
  runs when a caller supplies the previously-pinned version *number*
  (packwiz pins only store the version *id*; a caller needs the prior
  pin's own version string, e.g. parsed from its filename, exactly as
  `bump-dynamic-atmosphere.py`'s `load_da_pin` already does for DA).

**Explicit scope limit**: compatibility here means the candidate's own
declared `game_versions`/`loaders`. It does **not** resolve the
candidate's `dependencies` list against what else is currently pinned in
the pack (a full dependency-graph solve across potentially many mods at
once). That is a real gap, not an oversight -- see "Status."

## Identity/hash verification -- shared with DA, not duplicated

`scripts/modrinth_metadata.py`'s `verify_version_file_metadata()` is the
exact check `bump-dynamic-atmosphere.py`'s AC3 gate (version id, project
id, version number, exactly-one-file-by-name, sha512/sha1/url) already has
reviewed and tested, extracted so both the DA engine and any future
generalized write-path call the same implementation. A candidate resolved
by `modrinth_compat.py` is verifiable against this exact gate before any
pin is ever written, the same way DA's payload is today.

## No-downgrade / replay comparator -- shared with DA, not duplicated

`scripts/semver_compare.py` is `bump-dynamic-atmosphere.py`'s own
SemVer-2.0-with-prerelease comparator, extracted the same way, for the
same reason: one implementation of "is this a downgrade?", not two that
could drift.

## Direct-pin vs. `packwiz update`

**Decision (engineering call, per MINECRAFT-362 PR104 review comment
2026-10-17): keep direct-pin writes, generalized.** This was previously
recorded as "pending owner acceptance"; the reviewer has since confirmed
this is an implementation detail for the engineer to decide, not a further
product vote -- so it is decided here, not deferred again.

Rationale (the same evidence already gathered for DA, which generalizes
cleanly): a dispatch or poll names an *exact* Modrinth version id,
including for a private/unlisted project Modrinth's own API does not
resolve anonymously. `packwiz update` only moves a pin to whatever its own
lookup considers "latest" -- it cannot be pointed at a specific version id,
so it is the wrong tool for "pin exactly the version this event named."
Direct-pin writing does not skip packwiz's own validation: `make refresh`,
`make check`, and `make build` still run over the rewritten pin before
anything is staged or committed, for every mod, not only DA -- so packwiz
itself still enforces the pack's index/dependency/hash consistency on the
result. If a future mod's Modrinth project becomes anonymously resolvable
and `packwiz update` can be pointed at an exact version id, re-evaluate;
nothing here forecloses that.

## Concurrency / shared-lock / latest-main handling

Not newly built for the general case; **reused** from the existing,
reviewed DA primitive rather than reinvented: `.github/workflows/da-auto-
bump.yml` already serializes every DA dispatch through one `concurrency:
group: da-auto-bump` with `cancel-in-progress: false`, and each run starts
from a fresh `actions/checkout` of the then-current `main` tip. A
generalized dispatch/poll workflow should use exactly this pattern with
one shared group covering *every* enrolled mod (not one group per mod),
so two mods' updates -- or two events for the same mod -- can never
interleave into competing pushes; a later run always recomputes against
whatever the earlier run already pushed. **Not yet wired**: no such
generalized workflow exists yet (see "Status").

## Status

**Implemented, tested, merged to this branch:**
- `scripts/mod_eligibility.py` -- enrollment/exclusion resolution (6 tests).
- `scripts/modrinth_compat.py` -- compatibility filter, candidate
  resolution, no-downgrade/dedup decision (16 tests).
- `scripts/modrinth_metadata.py` -- identity/hash verification, extracted
  from and still used by the DA engine (12 tests, plus DA's own 74 still
  pass unchanged).
- `scripts/semver_compare.py` -- no-downgrade comparator, extracted from
  and still used by the DA engine (10 tests).
- The direct-pin-vs-packwiz decision above.
- `Release-Only: true` on DA's generated commits (see `da-auto-bump.md`).

**Not implemented -- real gaps, not oversights:**
- A generalized **write path**: nothing yet turns a `modrinth_compat`
  candidate into a rewritten pin + pack-version bump + notes + commit for
  an arbitrary mod. `bump-dynamic-atmosphere.py`'s write path (pin
  rendering, README-sentence rewriting, listing-line flagging, Migration
  extraction) is still DA-specific; most other mods have no equivalent
  "pins X version Y" README sentence to rewrite, so a generic write path
  needs its own (simpler) notes/listing handling, not a copy of DA's.
- A generalized **trigger**: no `repository_dispatch` type or scheduled
  workflow calls `discover_eligible_pins`/`resolve_latest_compatible_version`
  for anything other than DA today. The scheduled-poll backstop this
  document assumes (reconciling all 78 eligible pins against Modrinth on a
  schedule) is designed above but not wired into a workflow file.
  "no downgrades from stale events" is a live ground to keep testing that
  wiring against once it exists -- today it exists only as a unit-tested
  pure function, with no event feeding it.
- **Dependency-graph validation** across what's pinned (only each
  candidate's own declared compatibility is checked, not a full solve).
- The two GitHub-release-only mods (`dynamic-despawn`, `magicavoxel-loader`)
  have no verified-identity mechanism at all yet.
- **Dynamic Population**: explicitly deferred per this ticket's own
  prerequisite -- it is not added through this or any path until
  MINECRAFT-29's Fillager integration lands and a compatible verified
  release exists.
- No live dispatch, credential, or publication probe has been run for any
  of this -- unchanged from the existing DA holds.
