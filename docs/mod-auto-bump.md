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
  ("neoforge") and whose `version_type` is `"release"` (a `"beta"`/
  `"alpha"` entry is skipped even if its `version_number` sorts higher
  than a real release's -- `version_type` and `version_number` are
  independent fields on Modrinth, so semver ordering alone cannot be
  trusted to keep a prerelease out of the public pack), and returns the
  one with the highest semver `version_number` among those.
- `fetch_project_versions`'s `fetcher` can be built with
  `make_fetcher(token=...)` to attach `MODRINTH_TOKEN` as an
  `Authorization` header on the version-list request itself, not only on
  the later single-version metadata lookup -- some projects (the
  documented DA case) are not resolvable anonymously at all, so a
  version-list call that never carries the token can fail before the
  token-gated call downstream gets a chance to help.
- `decide_update(pin, candidate, previous_version_number=None)` turns a
  resolved candidate into one of `no_candidate` / `already_pinned`
  (dedup by Modrinth version id -- a replayed/retried dispatch is a
  no-op) / `downgrade_refused` / `candidate`. The downgrade check only
  runs when a caller supplies the previously-pinned version *number*
  (packwiz pins only store the version *id*). `scripts/bump_mod.py`
  supplies it by fetching the pinned version id's own metadata from
  Modrinth (one extra `GET /v2/version/{id}` call) rather than parsing it
  from the pin's filename -- filenames are not uniform enough across 78
  different mods to parse reliably, unlike DA's own fixed
  `dynamicatmosphere-<version>.jar` scheme.

**Explicit scope limit**: compatibility here means the candidate's own
declared `game_versions`/`loaders`. `modrinth_compat.py` also has
`find_dependency_conflicts(candidate, pinned_mod_ids)`, wired into
`scripts/bump_mod.py`'s write path: it refuses a candidate that declares
another *currently-pinned* project `incompatible` -- an unambiguous
conflict. It does **not** attempt a full dependency-graph solve (version
ranges, or treating a `required` dependency on a project that is not
currently pinned as a conflict -- Modrinth's data cannot tell this pass
whether that absence is a genuinely missing requirement or a
local-only/out-of-pack mod by design, like Herobrine API/Baritone). That
fuller solve is a real gap, not an oversight -- see "Status."

## Identity/hash verification -- shared with DA, not duplicated

`scripts/modrinth_metadata.py`'s `verify_version_file_metadata()` is the
exact check `bump-dynamic-atmosphere.py`'s AC3 gate (version id, project
id, version number, exactly-one-file-by-name, sha512/sha1/url) already has
reviewed and tested, extracted so both the DA engine and any future
generalized write-path call the same implementation. `scripts/bump_mod.py`
re-fetches `GET /v2/version/{id}` fresh and verifies it against exactly
the candidate `modrinth_compat.py` picked from the version-LIST response,
before any pin is ever written.

**What this gate does and does not establish**, stated precisely rather
than by analogy to DA: both the list response and this fresh re-fetch
come from Modrinth itself, so this is a consistency check between two
Modrinth endpoints (catches a corrupted/truncated list response, a
mismatched file entry, or a transient inconsistency), not a check against
an independently-sourced, untrusted event the way DA's `repository_dispatch`
payload is (that payload originates outside Modrinth and outside this
repo entirely). A previous draft of this document described the two as
equivalent; they are not, and the distinction matters for what this gate
can and cannot catch (MINECRAFT-362 review).

## No-downgrade / replay comparator -- shared with DA, not duplicated

`scripts/semver_compare.py` is `bump-dynamic-atmosphere.py`'s own
SemVer-2.0-with-prerelease comparator, extracted the same way, for the
same reason: one implementation of "is this a downgrade?", not two that
could drift.

`scripts/bump_mod.py`'s own comparison needs the currently-pinned
version's `version_number`, which it gets by fetching that version id's
metadata from Modrinth (a pin only stores the version *id*). That lookup
fails CLOSED: if it errors (404, network failure, or a response with no
`version_number`), the engine refuses to bump rather than skipping the
no-downgrade check -- a skipped check here would mean an unpublished or
unreachable currently-pinned version silently disables the exact
"stale-event handling without downgrades" guarantee this check exists to
provide (MINECRAFT-362 review; previously this degraded to dedup-only
safety on a lookup failure, which is the behaviour this paragraph
corrects).

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

## Write path: scripts/bump_mod.py

Generalizes `bump-dynamic-atmosphere.py`'s write path to any
`EligiblePin` + resolved `VersionCandidate`: re-verifies the candidate
against Modrinth's authoritative version-file metadata right before any
write (same gate DA uses, via `modrinth_metadata.py`), looks up the
*currently pinned* version's own `version_number` (a pin only stores its
version id) to get a real no-downgrade check rather than dedup-only,
downloads and hash-verifies the jar, rewrites the pin
(`bump-dynamic-atmosphere.py`'s `render_da_pin` turned out to already be
fully generic despite its name, so it is reused, not copied), bumps
`pack.toml`, writes a release-notes file, runs the same `make
refresh`/`check`/`build` gates, and restores the tree on any failure.

Deliberately simpler than DA's write path, by design: no README-sentence
rewrite (most mods have no "pins X version Y" sentence to rewrite) and no
Migration-section extraction (no generic release-body convention exists
to extract from). A generic bump instead always sets
`listing_review_required = true` and says so in its own notes file, so
listing copy is flagged for a human rather than silently assumed current.
`scripts/bump-dynamic-atmosphere.py`'s other generic helpers (pack-version
read/bump/write, download+hash verification, gate running) are reused
directly rather than re-implemented, loaded by path since that filename
has hyphens -- the same cross-script-reuse pattern
`da-auto-bump-dispatch.py` already uses for `has_da_auto_bump_trailer`.

15 tests in `tests/test_bump_mod.py` cover: already-pinned/no-candidate
no-ops, a real bump (pin rewrite, pack-version bump, notes, staging),
breaking-category handling, downgrade refusal, the identity/hash gate's
failure modes (wrong project, forged bytes, a 404 lookup), and
gate-failure/dirty-tree restore. The no-downgrade check's own
previous-version lookup fails CLOSED, not open: if that lookup itself
fails (e.g. a 404 because the currently-pinned version was unpublished),
the engine refuses to bump rather than silently skipping the check --
see "No-downgrade / replay comparator" above and
`test_unparsable_previous_metadata_fails_closed_not_open`.

## Trigger: scripts/poll-mod-updates.py + mod-auto-bump-poll.yml

The scheduled-reconciliation backstop this document designed above is now
wired: `.github/workflows/mod-auto-bump-poll.yml` runs on a 20-minute
schedule (and `workflow_dispatch`), with its own pause switch
(`SICKOS_MODBUMP_PAUSED`, same `== "true"` rule as DA's) and the same
credential selection (`da-auto-bump-dispatch.py select-credential`, reused
directly -- no new secrets). `scripts/poll-mod-updates.py` discovers every
eligible pin, **including Dynamic Atmosphere's own pin** (see the
docstring on `EXCLUDED_PIN_PATHS` for why it is no longer excluded --
dedup-by-version-id already makes running both mechanisms against the
same pin safe, and excluding it left a DA dispatch that gets dropped by
the shared concurrency group with nothing to recover it), resolves each
pin's latest compatible candidate, and runs `bump_mod.run_engine` on any
real candidate. The category passed to `bump_mod.run_engine` is derived
from the candidate's own Modrinth changelog
(`modrinth_compat.derive_category_from_changelog` -- AC4; there is no
trigger payload here to supply one the way DA's dispatch gets).

Each successful bump is committed (not just staged) before the next mod
is considered, so a second mod's bump reads the pack-version the first
mod's commit just advanced -- two real, compatible updates discovered in
the same poll become two sequential releases, never a lost or
overwritten one. One mod failing its identity/hash gate does not stop the
rest of the run; every mod's outcome (including failures) is in the
run's JSON summary. The script itself never pushes, matching the engine's
own convention. The workflow pushes each bump's commit SEPARATELY, one at
a time, in the order they were made -- not once at the end for the whole
run: a single push at the end would only ever trigger `release.yml` for
the LAST commit's `pack.toml` version, leaving every earlier bump in the
same run committed but never released (its own `docs/releases/*.md`
orphaned). Pushing commit-by-commit gives each bump its own push event
and its own release. Push-gating and credential-checking are driven by
whether the run's summary recorded any `"bumped"` outcome, not by the
script's overall exit code, so one mod's unresolvable failure never
discards another mod's already-committed, verified bump in the same run
-- the job still fails afterward (so the failure is visible), but only
after whatever succeeded is already pushed.

9 tests in `tests/test_poll_mod_updates.py` cover: Dynamic Atmosphere's
pin considered like any other (no-op when nothing is newer for it, bumped
when something is), no-op when nothing is newer for a mod, a single bump
actually committing with the right trailers, a changelog-derived category
reaching the engine, two mods bumping and coalescing into two sequential
pack versions in one run, an incompatible candidate correctly read as
no-candidate, a downgrade reported (not crashed), and one mod's failure
not blocking another mod's success in the same run.

`scripts/da-auto-bump-dispatch.py` gained `build_generic_commit_message`
for this -- same shape as `build_commit_message`, with its own
`Mod-Auto-Bump: <mod-id> <version-id>` trailer (never `DA-Auto-Bump`, so
nothing that greps commit history for either trailer specifically can
confuse a generic bump for a DA one) and the same `Release-Only: true`.

## Concurrency / shared-lock / latest-main handling

**Wired, not only designed.** `.github/workflows/da-auto-bump.yml`'s
`concurrency.group` was renamed from `da-auto-bump` to `mod-auto-bump`,
and `mod-auto-bump-poll.yml` uses that same group name -- GitHub Actions
concurrency groups serialize across *different* workflow files that share
a group name, not only within one file, so a DA dispatch and a scheduled
poll run now queue against each other, not just against runs of their own
kind. Both still start from a fresh `actions/checkout` of the
then-current `main` tip, so a later run always recomputes against
whatever the earlier run already pushed.

## Status

**Implemented, tested, merged to this branch:**
- `scripts/mod_eligibility.py` -- enrollment/exclusion resolution (6 tests).
- `scripts/modrinth_compat.py` -- compatibility filter (including
  `version_type == "release"` prerelease exclusion), candidate
  resolution, no-downgrade/dedup decision, token-attaching fetcher
  (`make_fetcher`), and changelog-based category derivation
  (`derive_category_from_changelog`) (34 tests).
- `scripts/modrinth_metadata.py` -- identity/hash verification, extracted
  from and still used by the DA engine (12 tests, plus DA's own 74 still
  pass unchanged).
- `scripts/semver_compare.py` -- no-downgrade comparator, extracted from
  and still used by the DA engine (10 tests).
- The direct-pin-vs-packwiz decision above.
- `Release-Only: true` on DA's generated commits (see `da-auto-bump.md`).
- `scripts/bump_mod.py` -- the generalized write path, with the
  no-downgrade lookup now failing closed and `assert_only_pin_changed_
  under_mods` checking its own `git diff` call's exit code instead of
  passing vacuously on a git failure (15 tests).
- `scripts/poll-mod-updates.py` + `.github/workflows/mod-auto-bump-poll.yml`
  -- the generalized trigger, now covering Dynamic Atmosphere's own pin
  too, reading `pack.toml`'s `[versions]` shape strictly (raises rather
  than silently defaulting to `None, None`), threading `MODRINTH_TOKEN`
  into the version-list query, deriving category from each candidate's
  changelog, and pushing each bump commit as its own push event instead
  of collapsing a multi-mod run into one push (9 tests).
- The shared `mod-auto-bump` concurrency group, covering both mechanisms.
- `modrinth_compat.find_dependency_conflicts` -- refuses a candidate that
  declares a currently-pinned project `incompatible` (6 tests, plus 3
  integration tests through `bump_mod.run_engine`).

**Not implemented -- real gaps, not oversights:**
- **Full dependency-graph validation**: `find_dependency_conflicts` only
  catches the unambiguous `incompatible`-and-currently-pinned case (see
  above). It does not resolve a `required` dependency's version range,
  and a `required` dependency on a project that is not currently pinned
  is not treated as a conflict (this pass cannot tell a genuinely missing
  requirement from a local-only/out-of-pack mod by design).
- The two GitHub-release-only mods (`dynamic-despawn`, `magicavoxel-loader`)
  have no verified-identity mechanism at all yet -- a different source
  (GitHub's release-asset API, not Modrinth's version-file metadata) would
  be needed, and nothing here builds it.
- **Dynamic Population**: explicitly deferred per this ticket's own
  prerequisite -- it is not added through this or any path until
  MINECRAFT-29's Fillager integration lands and a compatible verified
  release exists.
- No live run of either mechanism has happened: no credential has been
  set, `mod-auto-bump-poll.yml` has never actually fired against the real
  Modrinth API, and MODRINTH_TOKEN's read-readiness against each mod's
  project remains untested live -- unchanged from the existing DA holds.
  Unit tests exercise every decision path against mocked Modrinth
  responses; they are not live-run evidence.
