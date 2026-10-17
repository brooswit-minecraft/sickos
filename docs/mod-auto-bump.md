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
    mistake. `scripts/poll-mod-updates.py` reports a pin matching this
    list as its own `"excluded_local_only"` outcome, unconditionally
    expected rather than reconciled against a per-pin list -- its
    `pin_path` can never be known in advance (a human names the file),
    so a list-based guard would otherwise fail every run forever the
    moment one of these intentionally-excluded pins exists, defeating
    the exclusion mechanism this ticket itself requires (thirteenth
    review round).
  - No `[update.modrinth]` table: a GitHub-release-only first-party mod.
    At the time of writing this is 4 pins: `dynamic-despawn.pw.toml` and
    `magicavoxel-loader.pw.toml`, plus `dynamic-battle-axes.pw.toml` and
    `dynamic-whips.pw.toml` (added since this count was first written).
    Verifying a GitHub release's identity needs a different mechanism
    (GitHub's release-asset API, not Modrinth's version-file metadata) --
    **not implemented**; these 4 mods are not covered by anything in this
    document yet. `scripts/poll-mod-updates.py` reports each as its own
    `"untracked"` outcome every run (not silently dropped) and reconciles
    the set against `KNOWN_UNTRACKED_PIN_PATHS` by `pin_path`, the same
    way the four Modrinth-side gap classes below are reconciled by
    `mod_id` -- a pin newly losing its `[update.modrinth]` table is a
    coverage regression, not a known gap, and fails the run (twelfth
    review round).

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
  one with the highest semver `version_number` among those -- each
  candidate's `version_number` is normalized through
  `semver_compare.strip_known_version_prefixes(game_version, loader)`
  BEFORE the parseability check and the ordering comparison (ninth
  review round: this function already existed for
  `semver_delta_category`'s sake, but the resolver itself compared raw
  strings, so a mod that bakes its game version/loader into its version
  string -- Xaero's Minimap/World Map, Cristel Lib -- either had every
  candidate skipped as unparsable, permanently reporting a clean
  `no_candidate` with dozens of real releases available, or worse, could
  have a coincidentally-raw-parseable entry win the ordering against
  other entries' un-normalized strings, selecting a genuine downgrade).
- `fetch_project_versions`'s `fetcher` can be built with
  `make_fetcher(token=...)` to attach `MODRINTH_TOKEN` as an
  `Authorization` header on the version-list request itself, not only on
  the later single-version metadata lookup -- some projects (the
  documented DA case) are not resolvable anonymously at all, so a
  version-list call that never carries the token can fail before the
  token-gated call downstream gets a chance to help.
- `decide_update(pin, candidate, previous_version_number=None,
  game_version=None, loader=None)` turns a resolved candidate into one
  of `no_candidate` / `already_pinned` (dedup by Modrinth version id --
  a replayed/retried dispatch is a no-op) / `downgrade_refused` /
  `candidate`. The downgrade check only runs when a caller supplies the
  previously-pinned version *number* (packwiz pins only store the
  version *id*). `scripts/bump_mod.py` supplies it by fetching the
  pinned version id's own metadata from Modrinth (one extra `GET
  /v2/version/{id}` call) rather than parsing it from the pin's filename
  -- filenames are not uniform enough across 78 different mods to parse
  reliably, unlike DA's own fixed `dynamicatmosphere-<version>.jar`
  scheme. Both the candidate's and the previous version's strings are
  normalized through the same `strip_known_version_prefixes` before
  `compare_semver` (ninth round, same reasoning as the resolver above).

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
`make check`, and `make build` still run over the rewritten pin, for
every mod, not only DA -- so packwiz itself still enforces the pack's
index/dependency/hash consistency on the result. The write happens
before any gate runs, but `git add` (staging) happens between `refresh`
and `check`, not after `build` -- `make check` is `git diff --quiet --
pack.toml index.toml mods` against the INDEX, not HEAD, so it only
passes if this run's own writes are staged first (fourteenth review
round: the generalized engine got this ordering wrong for several
rounds, making every real bump fail `make check` every time -- see
`scripts/bump_mod.py`'s own comment on this ordering in `run_engine`). If a future mod's Modrinth project becomes anonymously resolvable
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
human-in-the-loop case: `--pin-path`, `--mod-id`, `--version-id`,
`--category breaking --allow-breaking --migration-text-file <path>`.
`scripts/poll-mod-updates.py` never passes `allow_breaking=True` and has
no such flag of its own, so a poll-derived breaking candidate is always
reported for a human to handle through this CLI, never auto-published.

**The CLI builds its pin and candidate from the pin on disk and real
Modrinth metadata, not from flags (fourth review round).** A third
round's CLI took the candidate's identity/hash fields AND its
compatibility/dependency data as flags -- the former meant an operator
hand-transcribed hashes Modrinth could just be asked for directly, and
the latter meant `game_versions`/`loaders`/`dependencies` defaulted to
empty tuples with no flag to populate them, so the compatibility check
and `find_dependency_conflicts` were both silent no-ops on this path.
Fixed: `_candidate_from_cli` fetches `GET /v2/version/{id}` itself (via
the same `fetch_version_metadata` `run_engine` already uses) and builds
the candidate with `modrinth_compat.version_candidate_from_json` (the
old private `_version_to_candidate`, made public for exactly this),
carrying its real compatibility/dependency data; `_check_compatibility_or_die`
then re-checks it against `mod_eligibility.read_pack_targets` (moved
there from `scripts/poll-mod-updates.py` so both callers share one
implementation) before `run_engine` ever runs. The CLI also defaulted
`--side` to `"both"`, silently moving 6 client-/server-only pins onto
both sides if an operator followed the documented remedy command
verbatim; `_pin_from_cli`/`_resolve_or_die` now default every pin-identity
field (name, side, mod-id, pinned-version-id) from the pin actually on
disk and REFUSE an explicit flag that disagrees with it, rather than
overriding.

**`--mod-id` was the one pin-identity field `_resolve_or_die` had not
been applied to (fifth review round).** It is also the field
`run_engine`'s own identity gate trusts as ground truth
(`expected_project_id=pin.mod_id`): a wrong `--mod-id` is self-consistent
with the metadata fetched for `--version-id` (both just describe
whatever project that version id actually belongs to), so every gate
passes and the wrong mod's jar, hash, and listing publish under the
intended pin's name and side. `_pin_from_cli` now resolves `mod_id`
through `_resolve_or_die` exactly like the other three fields, and
`--mod-id` is optional (defaults from the pin on disk) rather than
required.

**The CLI now commits, with the same trailers the automated path uses,
and an explicit pathspec.** A third round's CLI called `run_engine`
(which only stages) and stopped, leaving the operator to write their own
commit message with no `Release-Only`/`Mod-Auto-Bump` trailer --
`scripts/server_update_policy.should_deploy` reads a trailer-less
message as permission to deploy, so publishing a breaking change through
the one documented path would also restart the live server. `cli_main`
now builds the commit with the same `build_generic_commit_message`
`scripts/poll-mod-updates.py` uses (passing its own `source_text`, so
the commit body no longer claims "via scheduled Modrinth reconciliation"
on a bump a human made by hand) and checks `should_deploy(message)` is
`False` before committing (a plain `if`, not `assert` -- an `assert`
vanishes under `python3 -O`) (`--no-commit` skips this for inspection).
Its printed follow-up guidance names the four intended paths literally
(`pack.toml index.toml <pin> <notes file>`) -- an earlier round's
guidance told the operator to pass `$(git diff --cached --name-only)` as
the pathspec, which expands to EVERY staged path, including anything
else already staged, reintroducing the exact whole-index-sweep bug the
explicit pathspec exists to prevent (seventh review round; confirmed
against real git in a scratch repo with an unrelated file also staged).

`cli_commit_bump` (and, for symmetry, `scripts/poll-mod-updates.py`'s own
`commit_bump`) now commit an EXPLICIT PATHSPEC
(`pack.toml`, `index.toml`, the pin, the notes file) rather than a bare
`git commit`: a bare commit records everything already in the index, so
on a human's working checkout -- where an unrelated in-progress edit
being staged is ordinary, unlike the automated path's always-empty fresh
CI checkout -- it would sweep that unreviewed content into this release
commit too, authored as `sickos-mod-auto-bump[bot]` with `Release-Only:
true`, which `release.yml` then publishes. A pathspec'd `git commit --
<paths>` records a snapshot of exactly those paths and leaves anything
else staged untouched (still staged, neither stolen nor discarded).

Fetching a project Modrinth will not serve anonymously (`GET
/v2/version/{id}` returning HTTP 404) previously surfaced as a bare,
unhandled traceback; `_candidate_from_cli` now catches it and names
`MODRINTH_TOKEN` as the likely remedy.

When a bump does proceed (breaking or not), its notes file also includes
the candidate's own Modrinth changelog, fenced (`_fenced()`) so the
changelog's OWN markdown headings cannot restructure the surrounding
notes file the way a verbatim splice did in the prior round, and capped
at `MAX_CHANGELOG_CHARS` so one mod's long changelog cannot make the
notes file unbounded.

42 tests in `tests/test_bump_mod.py` cover: a real engine-level
game-version+loader-prefixed major bump reaching the breaking gate
(not just `semver_delta_category` in isolation), a previous-pin
metadata id mismatch failing closed, already-pinned/no-candidate
no-ops, a real bump classified by semver delta (not the old
"_default_absent_category" sentinel), a patch-level real-world
fix-only-changelog bump, a major semver delta alone reaching the
breaking gate with no changelog/category involved, a changelog category
unable to downgrade a major semver delta and able to escalate a minor
one, the previous pin's metadata failing closed when its project_id
does not match the pin, the breaking-change fail-closed gate, its
`allow_breaking=True` + `migration_text` override (and refusal when
`migration_text` is missing), the CLI entry point's own argument
validation, side- and mod-id-defaulting/disagreement-refusal,
incompatible-candidate refusal, real compatibility/dependency data
reaching `run_engine`, an anonymous 404 naming `MODRINTH_TOKEN` and a
transport failure (URLError/timeout) exiting cleanly, neither as a bare
traceback, the `--no-commit` guidance naming the four intended paths
literally rather than an unsafe `$(git diff --cached --name-only)`
substitution, and the commit step carrying the Release-Only/Mod-Auto-
Bump trailers (`TestCliMain`), an unrelated staged file surviving a
CLI-triggered commit untouched (`TestCliCommitBump`), `_fenced`'s
defanging/truncation (`TestFenced`), downgrade refusal, the
identity/hash gate's failure modes (wrong project, forged bytes, a 404
lookup), and gate-failure/dirty-tree
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
real candidate. A category hint, derived from the candidate's own
Modrinth changelog (`modrinth_compat.derive_category_from_changelog`),
is passed to `bump_mod.run_engine` -- but see "AC4 category
determination is the semver delta, not the changelog" below for why
this is only a hint now, not the deciding factor.

**AC4 category determination is the semver delta, not the changelog
(sixth review round).** `derive_category_from_changelog` only matches a
conventional "## Breaking" / "## Features" / "## Fixes" markdown
heading. Measured against a live, read-only resolution of all 78
eligible pins: 0 of 15 real update candidates matched it at all -- real
Modrinth changelogs are bullet lists ("- Fixed memory leak in
BlockEntityOnLoadCallback", "fix #88 #92 #95 #101"), not headings. Every
one of those 15 would previously have defaulted to minor regardless of
what it actually changed, and a genuine breaking change announced only
in bullets (no heading) would classify as `None`, reach
`bump_pack_version` as `minor_default_absent_category`, never set
`mapping_rule == "breaking_as_minor"`, and so never reach the
`allow_breaking` fail-closed gate at all -- bypassing the exact
protection that gate exists to provide, simply because the detector
upstream of it never saw the breaking change.

The fix: `run_engine` now derives the REAL category from the semver
delta between the currently-pinned and candidate version numbers (both
already confirmed valid semver by the no-downgrade check that runs just
before this) -- `semver_compare.semver_delta_category`: major component
changed -> `"breaking"`, else minor changed -> `"minor"`, else patch
changed -> `"patch"`. This is an always-available signal a mod author
already committed to, unlike changelog prose. The caller-supplied
category (the changelog hint above, or an explicit `--category` on
`scripts/bump_mod.py`'s CLI) can only ESCALATE the semver-delta
classification, never downgrade it (`semver_compare.more_severe_category`)
-- a changelog heading claiming "breaking" is trusted over a
merely-patch-looking version bump, but a heading that says "fix" on a
major version bump does not get to suppress the breaking classification
the version number itself already declares. The generated notes file's
category note now always states both signals and which one was used,
replacing the old silent "defaulted to minor" wording.

**The semver delta must strip a leading game-version AND/OR loader
prefix, or it never fires for those schemes (seventh and eighth review
rounds).** Some mods version their Modrinth releases with the game
version and/or loader name baked in as a leading component: 4 pins use
`"<game_version>-<mod_version>"` (e.g. `"1.21.1-4.0.1"` -- Supplementaries,
Moonlight Lib, Project: Vibrant Journeys, Create: Diesel Generators,
seventh round); the whole YUNG's family (12 pins: `yungs-api` and all
11 `yungs-better-*`/`yungs-cave-biomes`/`yungs-extras` pins) uses
`"<game_version>-<loader>-<mod_version>"` (e.g. `"1.21.1-NeoForge-5.1.9"`);
Cristel Lib uses the reverse order, `"<loader>-<game_version>-<mod_version>"`
(e.g. `"neoforge-1.21.1-3.1.7"`) (both eighth round). semver's own
grammar parses the leading numeric-looking component as the CORE and
everything after as a single prerelease identifier, so the mod's own
version lives entirely in a field `semver_delta_category` otherwise
ignores -- the core never changes between releases on these schemes, so
it returned `None` for every update, including a major one. Each round
fixing only the pins it found live reduced, but did not close, this gap
-- 100% of pins, then 4, now 0 confirmed after this round's fix, though
a yet-undiscovered scheme is not ruled out. `semver_delta_category` now
takes optional `game_version`/`loader` and strips every leading
`"<component>-"` that case-insensitively matches either, in any order,
repeating until neither matches (`strip_known_version_prefixes`, the
old private `_strip_game_version_prefix`'s replacement); `run_engine`
gained a `loader` parameter alongside `game_version`, both threaded
through from every caller's own `mod_eligibility.read_pack_targets`
result, so the classification operates on the mod's own version, not
the game version/loader components sharing its string. The generated
notes' "no category could be determined" note also now distinguishes
"changed no semver component" from "could not be parsed as semver after
stripping" -- the two cases a prior round's note conflated.

**The previous-pin's metadata is now identity-checked too (seventh
review round).** `run_engine` verifies the CANDIDATE's metadata hard
(`expected_project_id=pin.mod_id`, among others) before trusting it, but
the PREVIOUS version's metadata -- fetched from `pin.pinned_version_id`
for the no-downgrade check, and (since last round) for the category
delta too -- had no such check; only its `version_number` was read off
it. A pin whose recorded version id happened to resolve to a different
project's metadata would silently trust that unrelated project's
version number for both the no-downgrade guard (a low number lets a
real downgrade through; a high one freezes the pin forever) and the
category determination (a wrong number can misclassify a breaking
release as patch and skip the `allow_breaking` gate). `run_engine` now
checks `previous_metadata`'s `project_id` against `pin.mod_id` and fails
closed (`EXIT_INTEGRITY_FAILURE`) on a mismatch, symmetric with the
candidate's own gate. Confirmed latent, not active, by checking all 78
pins live at review time -- the same "unchecked field, no live
mismatch today" shape as the fifth round's `--mod-id` finding. Eighth
round: `previous_metadata`'s own `id` is checked too, against
`pin.pinned_version_id` -- the `project_id` check alone confirmed the
right PROJECT but not the right VERSION within it, asymmetric with the
candidate gate's own `expected_version_id` check.

**Only `version_type == "release"` may ever be pinned, enforced inside
`run_engine` itself, not only the poll's resolver (eighth review
round).** `modrinth_compat.resolve_latest_compatible_version` filters to
`version_type == "release"` before a candidate ever reaches
`run_engine` -- but that filter lives in the POLL path's own resolver,
which `scripts/bump_mod.py`'s CLI never calls (it is handed a version id
directly). A human could pin a beta/alpha by id with no check refusing
it; that pin would then be permanently `downgrade_refused` by every
future poll run once a real release surpasses it -- and since the sixth
round, `downgrade_refused` is an expected per-pin gap, so the mis-pin
would read as a quiet `::warning::` forever, not a failure anyone
notices. `run_engine` now re-checks the candidate's OWN freshly-fetched
metadata (the same one the identity/hash gate already fetches) for
`version_type == "release"` and fails closed otherwise -- "never trust a
filter this call did not itself verify," the same principle
`modrinth_compat`'s own docstring states, closing the gap for both
callers at once rather than only the one that happened to call the
resolver.

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
(`scripts/poll-mod-updates.py`'s `_is_expected_gap()`). `"resolution_failed"`
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
Terrain, Dynamic Vehicles). `_is_expected_gap()` checks each gap-shaped
outcome's `mod_id` against the specific static list for THAT outcome
kind (`KNOWN_ANONYMOUS_404_MOD_IDS` for `"resolution_failed"`,
`KNOWN_NON_SEMVER_PIN_IDS` for `"unsupported_version_scheme"`,
`KNOWN_NO_PARSEABLE_CANDIDATE_PIN_IDS` for `"no_parseable_candidate"`,
`KNOWN_ONLY_PRERELEASE_PIN_IDS` for `"only_prerelease_available"`); a
gap-shaped outcome for a pin NOT on its outcome's list fails the run
(eleventh review round -- an unlisted pin landing in one of these
classes is a coverage regression, not a known gap, and the allowlist
must say so explicitly rather than only ever growing softer). On top of
that, an aggregate guard in `main()` fails the run regardless of any
per-pin allowlist unless at least one outcome this run was
`"already_pinned"` or `"bumped"` (eleventh review round -- narrower than
it sounds: a run where every pin lands on `"no_candidate"` is exactly
the silent-failure shape this guard exists to catch, so `"no_candidate"`
deliberately does NOT count as healthy here even though it is not
itself a failure; the prior version of this guard only fired when
LITERALLY EVERY pin failed to resolve, which missed that shape). The
non-semver pins
(see `KNOWN_NON_SEMVER_PIN_NAMES` -- 3 as of this writing, see "Status"
below) don't need an allowlist the same way, since
`"unsupported_version_scheme"` is already established per-pin by a
single deterministic comparison, not a catch-all. The same is true of
the two outcomes `resolve_latest_compatible_version` itself raises
(`"no_parseable_candidate"`, `"only_prerelease_available"` -- see
`KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES`/`KNOWN_ONLY_PRERELEASE_PIN_NAMES`
below): each is a deterministic per-pin fact the resolver itself
establishes, never a wrapper around `modrinth_compat.py`'s bare-`Exception`
catch-all the way `"resolution_failed"` is, so both get their own
per-outcome allowlist in `_is_expected_gap()` too, same as the
non-semver pins (tenth and eleventh review rounds -- a prior round
raised the first of these as the plain `CompatResolutionError` base
class, which this script then mapped to `"resolution_failed"`, landing
it in that outcome's allowlist instead of its own and hard-failing the
run for every pin on this specific gap; the eleventh round then gave
each of these two outcomes its own list, `KNOWN_NO_PARSEABLE_CANDIDATE_PIN_IDS`
and `KNOWN_ONLY_PRERELEASE_PIN_IDS`, rather than exempting the outcome
class unconditionally).

Because `poll_and_bump` stops at the first bump, most runs never reach
most pins, so the per-outcome `::warning::` lines above are intermittent
precisely on the runs that work. `main()` therefore also prints a
standing `::notice::` reminder every run, independent of what that run's
loop actually reached, naming all 20 known-uncovered pins from the five
static lists above (`KNOWN_ANONYMOUS_404_MOD_IDS`,
`KNOWN_NON_SEMVER_PIN_NAMES`, `KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES`,
`KNOWN_ONLY_PRERELEASE_PIN_NAMES`, `KNOWN_UNTRACKED_PIN_NAMES` --
twelfth review round added the fifth) -- these 20 pins are, as of this
writing, simply not covered by this mechanism: the 4 anonymous-
inaccessible ones need `MODRINTH_TOKEN` to be set (still held, see
"Status"); the 3 non-semver ones need either a human bump or a
different comparator this document does not attempt; the 5
no-parseable-candidate ones have NO parseable version published on
Modrinth at all, so a human bump cannot fix them either -- only a
different comparator this document does not attempt would; the 4
only-prerelease ones have never published a release build for this
target and need the mod author to publish one, not a human bump; and
the 4 untracked ones have no `[update.modrinth]` table at all and need
a GitHub-release-based identity check this mechanism does not
implement, so only a human bump covers them today. Deliberately NOT
named here: a pin matching `EXCLUDED_MOD_NAMES` (Herobrine API,
Baritone) reports its own `"excluded_local_only"` outcome but is not a
coverage gap at all -- it is a permanent, intentional non-enrollment,
so the standing notice (which exists to list GAPS) omits it (thirteenth
review round).

**`downgrade_refused` and `dependency_conflict` are expected gaps too
(sixth review round).** Both were still landing in `failed`, the
red-by-construction defect's third occurrence: each recurs IDENTICALLY
every run for a given pin until something upstream changes, the same
shape as the two outcomes above. `downgrade_refused` fires every run for
a pin whose currently-pinned version is ahead of the latest version
`resolve_latest_compatible_version`'s `version_type == "release"` filter
will ever return (e.g. pinned to a prerelease) -- confirmed live:
`mods/ice-and-fire-community-edition.pw.toml` is pinned ahead of its
newest actual release today, and lands on `unsupported_version_scheme`
only incidentally, because its pinned version string happens not to be
valid semver; a pin in the same situation with a valid-semver pinned
version would hit `downgrade_refused` every run instead, unclassified.
`dependency_conflict` is a pure function of the SAME re-resolved "latest
compatible" candidate's own declared dependencies against the pack's
current pins, so it recurs for the same structural reason. Both are now
unconditionally expected via `_UNLISTABLE_EXPECTED_GAP_OUTCOMES` (no
per-mod-id allowlist needed, unlike the four outcomes above -- each is
already established per-pin by one deterministic check, not a
catch-all, and which specific pin hits it next isn't knowable in
advance the way the other four outcomes' pins are).

Push-gating and credential-checking are driven by whether the run's
summary recorded a real bump, not by the script's overall exit code, so
an unexpected failure elsewhere in the same run never blocks pushing a
bump this run already made -- the job still fails afterward (so the
failure is visible), but only after that push.

35 tests in `tests/test_poll_mod_updates.py` cover: the same
game-version+loader threading reaching the category gate end-to-end
through `poll_and_bump` (not only `run_engine` directly), Dynamic Atmosphere's
pin considered like any other (no-op when nothing is newer for it, bumped
when something is), no-op when nothing is newer for a mod, a single bump
actually committing with the right trailers, a changelog category unable
to downgrade the semver-delta classification and a real bullet-list
changelog still classifying correctly from the semver delta alone, only
the FIRST of two simultaneously-eligible mods bumping in one run (the
second is left for the next run), an incompatible candidate correctly
read as no-candidate, a downgrade reported (not crashed), one mod's
failure not blocking another mod's success in the same run, a breaking
candidate refused rather than auto-published, and `main()`'s exit-code
classification itself: expected gaps alone (via the per-outcome
allowlists, now including `downgrade_refused`/`dependency_conflict`)
keep the run green, a gap-shaped outcome for a mod_id NOT on that
outcome's own allowlist still fails it (a coverage regression, not a
known gap), a genuine unexpected failure still fails it, and the
aggregate guard -- no run may pass with zero `already_pinned`/`bumped`
outcomes, regardless of any per-pin allowlist -- fails the run both
when every pin failed to resolve and when every pin instead landed on
`no_candidate` (eleventh review round: the narrower, pre-existing guard
only caught the first shape).
`commit_bump` committing an explicit pathspec (not a bare `git commit`)
is covered separately (`TestCommitBump`), for symmetry with
`scripts/bump_mod.py`'s own `cli_commit_bump` fix.

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
- `scripts/mod_eligibility.py` -- enrollment/exclusion resolution, plus
  `read_pack_targets` (moved here from `scripts/poll-mod-updates.py` so
  `scripts/bump_mod.py`'s CLI can share it rather than duplicate it)
  (10 tests).
- `scripts/modrinth_compat.py` -- compatibility filter (including
  `version_type == "release"` prerelease exclusion), candidate
  resolution, no-downgrade/dedup decision, token-attaching fetcher
  (`make_fetcher`), changelog-based category derivation
  (`derive_category_from_changelog`, heading-only -- a bare free-text
  "breaking change(s)" match was removed after it matched "No breaking
  changes."), and `version_candidate_from_json` (the old private
  `_version_to_candidate`, made public so a version-id-only caller like
  the CLI below can build a fully-populated candidate too; and (ninth
  round) `resolve_latest_compatible_version`/`decide_update` now both
  normalize through `semver_compare.strip_known_version_prefixes`
  themselves, closing the gap where the strip existed but only
  `semver_delta_category` used it -- Xaero's Minimap/World Map and
  Cristel Lib were, until this round, permanently invisible to the
  resolver or vulnerable to a downgrade slipping past the un-normalized
  comparison; and (tenth round) `NoParseableCandidateError`/
  `OnlyPrereleaseAvailableError`, each raised instead of a silent `None`
  so "nothing newer upstream" cannot be confused with "every candidate
  was unparseable" or "only prereleases exist") (42 tests).
- `scripts/modrinth_metadata.py` -- identity/hash verification, extracted
  from and still used by the DA engine (12 tests).
- `scripts/semver_compare.py` -- no-downgrade comparator, extracted from
  and still used by the DA engine, plus `semver_delta_category` (the
  real AC4 category source, see below -- including stripping every
  leading game-version/loader component for the pins that bake one or
  both into their version string) and `more_severe_category` (28 tests).
- `bump-dynamic-atmosphere.py`'s own dispatch path gained the SAME
  semver-delta category derivation (eighth round): it previously took
  category only from the trigger payload, so AC4's breaking gate could
  never fire there regardless of what the generalized poll path did --
  the same DA release got two different answers depending on which
  mechanism processed it. `delta_category`/`more_severe_category` now
  sit between the no-downgrade comparison and `bump_pack_version`, the
  same shape as `scripts/bump_mod.py`'s own fix (76 tests, up from 74 --
  2 new, several adjusted to use a genuinely-matching-category version
  delta rather than relying on the old payload-only default).
- The direct-pin-vs-packwiz decision above.
- `Release-Only: true` on DA's generated commits (see `da-auto-bump.md`).
- `scripts/bump_mod.py` -- the generalized write path, with the
  no-downgrade lookup now failing closed, its own "cannot compare
  non-semver versions" outcome now distinct (`unsupported_version_scheme`,
  not generic `config_error`), a fail-closed `allow_breaking` gate on
  breaking changes requiring real `migration_text`
  (`EXIT_MIGRATION_MISSING` / `breaking_change_requires_human_review`,
  `EXIT_CONFIG_ERROR` / `migration_text_required`), a real `argparse` CLI
  entry point for that human-in-the-loop path (`cli_main`, `--help`) that
  fetches its own candidate's real compatibility/dependency data from
  Modrinth and re-checks it, defaults every pin-identity field from the
  pin on disk (refusing a disagreeing explicit flag rather than
  overriding), and commits with the same trailers the automated path
  uses, the candidate's own changelog now included in generated notes
  (fenced and length-capped via `_fenced`), `assert_only_pin_changed_under_mods`
  checking its own `git diff` call's exit code instead of passing
  vacuously on a git failure, `--mod-id` now resolved through the same
  on-disk-defaulting/disagreement-refusal as the other pin-identity
  fields, `cli_commit_bump` committing an explicit pathspec rather than
  a bare `git commit`, a real AC4 category determination from the
  semver delta between the currently-pinned and candidate version
  numbers (escalatable, never downgradable, by a changelog/explicit
  category), a transport failure (DNS/timeout/connection refused)
  now exiting cleanly instead of a bare traceback, and (fourteenth
  round) `stage_intended_files` moved between the `refresh` and `check`
  gates (matching `bump-dynamic-atmosphere.py`'s own ordering) so
  `make check`'s `git diff --quiet` against the INDEX actually passes --
  one test exercises this with the real `git diff --quiet` predicate in
  place of a stub, and a second (skipped where `packwiz` is not
  installed) runs the real `packwiz refresh` binary too (42 tests).
- `scripts/poll-mod-updates.py` + `.github/workflows/mod-auto-bump-poll.yml`
  -- the generalized trigger, now covering Dynamic Atmosphere's own pin
  too, reading `pack.toml`'s `[versions]` shape strictly (raises rather
  than silently defaulting to `None, None`), passing a changelog-derived
  category HINT (not the deciding factor -- see above) to each
  candidate's bump, making AT MOST ONE bump commit per run (so it never
  asks `release.yml` to run twice before the first run finishes),
  classifying four gap-shaped outcome kinds (`resolution_failed`,
  `unsupported_version_scheme`, `no_parseable_candidate`,
  `only_prerelease_available`, plus `untracked` -- see "Enrollment"
  above) each against its OWN explicit `KNOWN_*_PIN_IDS`/
  `KNOWN_UNTRACKED_PIN_PATHS` allowlist (never the whole outcome class),
  three genuinely unlistable-in-advance outcomes
  (`breaking_change_requires_human_review`, `downgrade_refused`,
  `dependency_conflict`) staying unconditionally expected, and an
  aggregate guard stated as a POSITIVE invariant -- at least one
  `already_pinned`/`bumped` outcome this run, deliberately not
  satisfied by `no_candidate` -- that fails the run both on zero
  outcomes examined at all (mods/ missing or renamed) and on every pin
  landing on the same non-healthy outcome, so the scheduled job is
  neither red by construction nor silently green through an outage, a
  response-shape change, or a pin quietly dropping out of enrollment
  (eleventh and twelfth review rounds), and `commit_bump` committing an
  explicit pathspec rather than a bare `git commit`, and the
  previously-computed-but-unchecked `before_head`/`after_head` assertion
  restored in the breaking-change test (ninth round -- it had been
  silently dropped, leaving that test unable to catch a regression that
  refuses the bump outcome yet commits anyway, and (thirteenth round)
  `EXCLUDED_MOD_NAMES` pins reported as their own unconditionally-
  expected `"excluded_local_only"` outcome rather than reconciled by
  `pin_path` the way genuinely-untracked pins are, and (fifteenth round)
  `EXCLUDED_PIN_PATHS` gaining the same own-outcome treatment --
  `"excluded_pin_path"`, also unconditionally expected -- so a path
  added there in the future gets reported rather than silently dropped
  by the bare `continue` this replaced) (35 tests).
  `run_engine`'s category determination and no-downgrade
  check both now take/use a `game_version` hint and an identity-checked
  previous-pin metadata, threaded through from this script's own
  `read_pack_targets` result (see the Compatibility and Write Path
  sections above).
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
- The 4 GitHub-release-only mods (`dynamic-despawn`, `magicavoxel-loader`,
  `dynamic-battle-axes`, `dynamic-whips`) have no verified-identity
  mechanism at all yet -- a different source (GitHub's release-asset API,
  not Modrinth's version-file metadata) would be needed, and nothing here
  builds it. `scripts/poll-mod-updates.py` reports each as a standing
  `"untracked"` gap every run (see "Enrollment" above) so this remains a
  known, reconciled gap rather than a silent one; a human bump is the
  only remedy today.
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
- **16 of the 78 eligible pins are not covered by this mechanism today**
  (established by a live, authorized, read-only resolution against
  Modrinth, done under the review process for this ticket -- no
  dispatch, credential, write, or publication probe; recount as of the
  tenth review round). Four classes, each with its OWN outcome string
  and its own remedy -- conflating them, or conflating any of them with
  the TRUE catch-all `"resolution_failed"`, was itself the tenth round's
  bug (see "Not every non-success outcome..." above):
  - **4 pins** (`KNOWN_ANONYMOUS_404_MOD_IDS`: Dynamic Atmosphere,
    Dynamic Skateboards, Dynamic Terrain, Dynamic Vehicles) return HTTP
    404 to an anonymous caller -- outcome `"resolution_failed"`,
    allowlisted by `mod_id` specifically (not the whole outcome class).
    Need `MODRINTH_TOKEN` set to become resolvable -- still held, per
    the no-live-run item above.
  - **3 pins** (`KNOWN_NON_SEMVER_PIN_NAMES`: Create: Easy Structures,
    Geophilic, IceAndFire Community Edition) resolve a real candidate,
    but their own PINNED `version_number` is not valid semver even
    after normalization, so the no-downgrade comparator can never
    evaluate them -- outcome `"unsupported_version_scheme"`. Need a
    human bump to a parseable pinned version.
  - **5 pins** (`KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES`: Quark, Zeta,
    Create: Dynamic Village, Create: Pillager Arise, Dungeons and
    Taverns) have compatible RELEASE entries on Modrinth, but NONE of
    those entries' `version_number`s are valid semver even after
    normalization -- `resolve_latest_compatible_version` itself raises
    `NoParseableCandidateError`, outcome `"no_parseable_candidate"`.
    These 5 pins' UNPARSEABLE strings are the Modrinth CANDIDATES
    themselves, not the pin, so a human bump cannot fix any of them --
    only a different comparator this document does not attempt would.
  - **4 pins** (`KNOWN_ONLY_PRERELEASE_PIN_NAMES`: Biomes O' Plenty,
    GlitchCore, Serene Seasons, TerraBlender) have compatible entries,
    but every one is a beta/alpha -- `resolve_latest_compatible_version`
    raises `OnlyPrereleaseAvailableError`, outcome
    `"only_prerelease_available"`. A human bump cannot fix this; only
    the mod author publishing a release build does.

  All four classes report as `::warning::` lines (when a run's loop
  reaches them) and a standing `::notice::` reminder every run
  regardless of what that run's loop reached, so this list stays
  visible without paging on it every 20 minutes -- NOT as a failed run
  for a pin ON its matching list. This is true of all four classes
  equally (eleventh review round generalized it past just the first):
  each is checked against its OWN `KNOWN_*_PIN_IDS` dict by `mod_id`,
  so EVERY one of them fails the run for a pin hitting that outcome
  while NOT on its matching list -- the signal a genuinely unexpected
  failure (an outage, a revoked token, a new 404, a mod quietly
  changing version scheme) still needs, not only for
  `"resolution_failed"`. This count is itself a snapshot, not a
  guarantee -- the same undercounting mistake a prior round corrected
  could recur if a pin's scheme is discovered later without updating
  this document and the four `KNOWN_*` lists together.

  A fifth, related-but-distinct gap: 4 pins (`dynamic-battle-axes`,
  `dynamic-despawn`, `dynamic-whips`, `magicavoxel-loader`) have no
  `[update.modrinth]` table at all -- see "Enrollment" above for how
  these are reported and reconciled (twelfth review round). A sixth,
  deliberately NOT a gap: a pin named in `EXCLUDED_MOD_NAMES`
  (Herobrine API, Baritone) reports its own `"excluded_local_only"`
  outcome, unconditionally expected rather than reconciled against a
  per-pin list -- its `pin_path` can never be known in advance, so a
  list-based guard would otherwise fail every run forever the moment
  one of these intentionally-excluded pins exists (thirteenth review
  round).
