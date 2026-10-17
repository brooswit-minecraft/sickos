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

**Breaking changes fail closed by default (AC4), with a real path to
publish one by hand (third review round).** The "no generic
Migration-section extraction" limitation above means a breaking bump has
real notes saying "a human must write migration/reset instructions" with
no actual instructions attached -- auto-publishing that placeholder is
not the same as satisfying AC4's "include BREAKING release notes plus
migration/reset instructions" requirement. `run_engine(..., category=
"breaking", allow_breaking=False)` (the default) therefore refuses with
`EXIT_MIGRATION_MISSING` / outcome `"breaking_change_requires_human_review"`
instead of writing anything. `allow_breaking=True` alone is not enough to
proceed: it must come with non-empty `migration_text` (else
`EXIT_CONFIG_ERROR` / `"migration_text_required"`), which `render_notes`
then writes verbatim under its own "Migration / reset instructions"
heading in place of the old placeholder -- so the ONE path that can
publish a breaking change is also the one place real instructions are
required to exist, not merely implied by a flag.

`scripts/bump_mod.py` is otherwise import-only (no CLI), so a second
round's error message naming an `--allow-breaking` flag named a command
that did not exist -- the module now has a real `argparse` entry point
(`python3 scripts/bump_mod.py --help`) specifically for this
human-in-the-loop case: explicit candidate fields, `--category breaking
--allow-breaking --migration-text-file <path>`. `scripts/poll-mod-updates.py`
never passes `allow_breaking=True` and has no such flag of its own, so a
poll-derived breaking candidate is always reported for a human to handle
through this CLI, never auto-published.

When a bump does proceed (breaking or not), its notes file also includes
the candidate's own Modrinth changelog, fenced (`_fenced()`) so the
changelog's OWN markdown headings cannot restructure the surrounding
notes file the way a verbatim splice did in the prior round, and capped
at `MAX_CHANGELOG_CHARS` so one mod's long changelog cannot make the
notes file unbounded.

23 tests in `tests/test_bump_mod.py` cover: already-pinned/no-candidate
no-ops, a real bump (pin rewrite, pack-version bump, notes, staging),
the breaking-change fail-closed gate, its `allow_breaking=True` +
`migration_text` override (and refusal when `migration_text` is
missing), the CLI entry point's own argument validation and file-reading
(`TestCliMain`), `_fenced`'s defanging/truncation (`TestFenced`),
downgrade refusal, the identity/hash gate's failure modes (wrong
project, forged bytes, a 404 lookup), and gate-failure/dirty-tree
restore. The no-downgrade check's own previous-version lookup fails
CLOSED, not open: if that lookup itself fails (e.g. a 404 because the
currently-pinned version was unpublished), the engine refuses to bump
rather than silently skipping the check -- see "No-downgrade / replay
comparator" above and `test_unparsable_previous_metadata_fails_closed_not_open`.
The no-downgrade check's own "cannot compare non-semver versions" outcome
is `"unsupported_version_scheme"`, deliberately distinct from the generic
`"config_error"` -- see the Trigger section below for why.

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

**At most one real bump per run, by design (second review round).** A
first implementation committed every eligible mod's bump in the same run
(each its own commit) and pushed them commit-by-commit so each got its
own push event. That IS enough to get each bump its own `release.yml`
trigger -- but `release.yml` has its OWN concurrency group
(`sickos-release`; see `.github/workflows/release.yml`) with the exact
same "a run arriving while one is in-progress and one is pending
replaces the pending one" semantics as `mod-auto-bump`'s own group, and a
release run takes minutes while these pushes fire seconds apart. Verified
against a live, read-only resolution of all 78 eligible pins: a run that
found today's 9 real candidates would commit and push 9 pack versions
and publish roughly 2 of them, leaving ~7 on `main` with a real pin
change and a committed `docs/releases/<version>.md` but no release --
`release.yml`'s own skipped-version detector is explicitly non-fatal, so
nothing catches this automatically. `poll_and_bump` now stops as soon as
it makes one real bump commit; every pin before it (no-op or otherwise)
is still in this run's outcome list, and every pin after it is simply
absent -- picked up by the next scheduled run 20 minutes later, not
reported as any kind of failure. The script itself still never pushes,
matching the engine's own convention; the workflow pushes that one
commit, if any.

**Not every non-success outcome is a failure worth paging on -- but
"resolution_failed" as a WHOLE CLASS is not safe to wave through
(third review round).** `"unsupported_version_scheme"` (the no-downgrade
comparator cannot evaluate a non-semver pinned version) and
`"breaking_change_requires_human_review"` (the fail-closed gate above)
are each established by one specific, deterministic check that always
reaches the same verdict for the same pin -- genuinely permanent,
genuinely safe to keep out of the exit code unconditionally
(`scripts/poll-mod-updates.py`'s `EXPECTED_GAP_OUTCOMES`). `"resolution_failed"`
is different: `modrinth_compat.py`'s version-list fetch catches bare
`Exception`, so it is a catch-all for an HTTP 5xx, a timeout, a DNS
failure, a malformed response, an expired `MODRINTH_TOKEN`, or a project
genuinely deleted/privated -- none of which are the one specific,
confirmed "this project 404s anonymously" fact. A prior round of this
fix treated the whole outcome class as expected, which meant a live
Modrinth outage (or any of the above) could silently stop the mechanism
while the scheduled job stayed green every 20 minutes -- confirmed: with
all 78 pins returning HTTP 503 at that head, `main()` exited 0.

The actual fix is an explicit allowlist, not a class-wide exemption:
`KNOWN_ANONYMOUS_404_MOD_IDS` names the 4 SPECIFIC projects individually
confirmed (by the same live, read-only, authorized check) to 404
anonymously today (Dynamic Atmosphere, Dynamic Skateboards, Dynamic
Terrain, Dynamic Vehicles). `_is_expected_gap()` only treats
`"resolution_failed"` as expected when the failing pin's `mod_id` is on
that list; a `"resolution_failed"` for anything else fails the run. On
top of that, an aggregate guard in `main()` fails the run regardless of
the allowlist if LITERALLY EVERY pin examined failed to resolve at all
(the signature of a systemic problem, not the documented per-pin
baseline, which always has some mix of `already_pinned`/`no_candidate`/
`bumped` even accounting for the known-gap pins). The 6 non-semver pins
(Create: Easy Structures, Cristel Lib, Geophilic, IceAndFire Community
Edition, Quark, Zeta) don't need an allowlist the same way, since
`"unsupported_version_scheme"` is already established per-pin by a
single deterministic comparison, not a catch-all.

Because `poll_and_bump` stops at the first bump, most runs never reach
most pins, so the per-outcome `::warning::` lines above are intermittent
precisely on the runs that work. `main()` therefore also prints a
standing `::notice::` reminder every run, independent of what that run's
loop actually reached, naming all 10 known-uncovered pins from the
static lists above (`KNOWN_ANONYMOUS_404_MOD_IDS`,
`KNOWN_NON_SEMVER_PIN_NAMES`) -- these 10 pins are, as of this writing,
simply not covered by this mechanism: the 4 anonymous-inaccessible ones
need `MODRINTH_TOKEN` to be set (still held, see "Status"), and the 6
non-semver ones need either a human bump or a different comparator this
document does not attempt.

Push-gating and credential-checking are driven by whether the run's
summary recorded a real bump, not by the script's overall exit code, so
an unexpected failure elsewhere in the same run never blocks pushing a
bump this run already made -- the job still fails afterward (so the
failure is visible), but only after that push.

14 tests in `tests/test_poll_mod_updates.py` cover: Dynamic Atmosphere's
pin considered like any other (no-op when nothing is newer for it, bumped
when something is), no-op when nothing is newer for a mod, a single bump
actually committing with the right trailers, a changelog-derived category
reaching the engine, only the FIRST of two simultaneously-eligible mods
bumping in one run (the second is left for the next run), an incompatible
candidate correctly read as no-candidate, a downgrade reported (not
crashed), one mod's failure not blocking another mod's success in the
same run, a breaking candidate refused rather than auto-published, and
`main()`'s exit-code classification itself: expected gaps alone (via the
allowlist) keep the run green, a `resolution_failed` for a mod_id NOT on
the allowlist still fails it, a genuine unexpected failure still fails
it, and the aggregate "every pin failed to resolve" guard fails the run
even when every one of those failures is individually allowlisted.

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
  (`derive_category_from_changelog`, heading-only -- a bare free-text
  "breaking change(s)" match was removed after it matched "No breaking
  changes.") (35 tests).
- `scripts/modrinth_metadata.py` -- identity/hash verification, extracted
  from and still used by the DA engine (12 tests, plus DA's own 74 still
  pass unchanged).
- `scripts/semver_compare.py` -- no-downgrade comparator, extracted from
  and still used by the DA engine (10 tests).
- The direct-pin-vs-packwiz decision above.
- `Release-Only: true` on DA's generated commits (see `da-auto-bump.md`).
- `scripts/bump_mod.py` -- the generalized write path, with the
  no-downgrade lookup now failing closed, its own "cannot compare
  non-semver versions" outcome now distinct (`unsupported_version_scheme`,
  not generic `config_error`), a fail-closed `allow_breaking` gate on
  breaking changes requiring real `migration_text`
  (`EXIT_MIGRATION_MISSING` / `breaking_change_requires_human_review`,
  `EXIT_CONFIG_ERROR` / `migration_text_required`), a real `argparse` CLI
  entry point for that human-in-the-loop path (`cli_main`, `--help`), the
  candidate's own changelog now included in generated notes (fenced and
  length-capped via `_fenced`), and `assert_only_pin_changed_under_mods`
  checking its own `git diff` call's exit code instead of passing
  vacuously on a git failure (23 tests).
- `scripts/poll-mod-updates.py` + `.github/workflows/mod-auto-bump-poll.yml`
  -- the generalized trigger, now covering Dynamic Atmosphere's own pin
  too, reading `pack.toml`'s `[versions]` shape strictly (raises rather
  than silently defaulting to `None, None`), threading `MODRINTH_TOKEN`
  into the version-list query, deriving category from each candidate's
  changelog, making AT MOST ONE bump commit per run (so it never asks
  `release.yml` to run twice before the first run finishes), and
  classifying outcomes against an explicit allowlist
  (`KNOWN_ANONYMOUS_404_MOD_IDS`) plus an aggregate "did anything resolve
  at all" guard -- not a blanket exemption for the whole
  `"resolution_failed"` outcome class -- so the scheduled job is neither
  red by construction nor silently green through an outage (14 tests).
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
- **10 of the 78 eligible pins are not covered by this mechanism today**
  (established by a one-time, authorized, read-only resolution against
  live Modrinth, done under the review process for this ticket -- no
  dispatch, credential, write, or publication probe): 4 projects return
  HTTP 404 to an anonymous caller (Dynamic Atmosphere, Dynamic
  Skateboards, Dynamic Terrain, Dynamic Vehicles) and need `MODRINTH_TOKEN`
  set to become resolvable -- still held, per the no-live-run item above;
  6 pins' currently-pinned `version_number` is not valid semver (Create:
  Easy Structures, Cristel Lib, Geophilic, IceAndFire Community Edition,
  Quark, Zeta) and so can never pass the no-downgrade comparator -- these
  need either a human bump to a parseable version, or a different
  comparator this document does not attempt. Both classes report as
  `::warning::` lines (when a run's loop reaches them) and the
  `"resolution_failed"`/`"unsupported_version_scheme"` outcomes (see
  "Trigger" above), not a failed run -- the 4 anonymous-404 ids are an
  explicit, individually-confirmed allowlist (`KNOWN_ANONYMOUS_404_MOD_IDS`),
  NOT a blanket exemption for every `"resolution_failed"`, and `main()`
  also prints a standing reminder every run regardless of what that
  run's loop reached, so this list stays visible without paging on it
  every 20 minutes.
