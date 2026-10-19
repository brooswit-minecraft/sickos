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
  At the time of writing this is 74 of the pack's pins.
  `dynamic-skateboards.pw.toml` is NOT one of them (MINECRAFT-421,
  superseding MINECRAFT-362 review comment 48456's "confirmed not a bug"
  on this exact pin): that round's theory -- Modrinth mirrors Skateboards'
  own GitHub-hosted jars as a real `release`-type version, so the
  `[download].url` pointing at `github.com` was unrelated to how the pin
  is tracked -- was never checked against a live, authenticated
  resolution. MINECRAFT-421's first real scheduled-poll run was that live
  check, with `MODRINTH_TOKEN` actually configured, and it found Modrinth
  project `n0hFQ5AT` has no `release`-type version for this pack's
  (game_version, loader) target at all, only prereleases
  (`only_prerelease_available`) -- a real, run-failing gap, since
  Skateboards is one of this pack's own managed mods, not a third-party
  one the `KNOWN_ONLY_PRERELEASE_PIN_IDS` allowlist tolerates. Its actual
  current, authoritative releases are the GitHub ones its `[download].url`
  already named (confirmed live: `gh release list` shows `v0.3.0` as
  latest, matching the pinned jar). Reconciled by switching its `[update]`
  table from `[update.modrinth]` to the same native-schema
  `[update.github]` (`slug`/`tag`/`branch`/`regex`) this pack's other
  10 `[update.github]`-tracked pins already use (see "GitHub-release-only
  managed mods" below) -- reusing the existing, reviewed GitHub-release
  engine (`scripts/github_release_identity.py`, `scripts/bump_mod_github.py`)
  rather than inventing a third channel. It is now `[update.github]`-tracked
  (11 such pins total as of this change, confirmed via
  `mod_eligibility.discover_github_pins`), not counted in this 74.
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
  - No `[update.modrinth]` table AND no `[update.github]` table of any
    shape: genuinely untracked by every mechanism this repo has. At the
    time of writing this is 0 pins -- the 4 that used to be here
    (`dynamic-despawn.pw.toml`, `magicavoxel-loader.pw.toml`,
    `dynamic-battle-axes.pw.toml`, `dynamic-whips.pw.toml`) all gained a
    real, native-schema `[update.github]` table in the twenty-third
    review round; see "GitHub-release-only managed mods" below.
    `scripts/poll-mod-updates.py` still reports a pin in this shape as
    its own `"untracked"` outcome every run (not silently dropped) and
    reconciles the set against `KNOWN_UNTRACKED_PIN_PATHS` (currently
    empty) by `pin_path`, the same way the four Modrinth-side gap classes
    below are reconciled by `mod_id` -- a pin newly losing ALL tracking
    is a coverage regression, not a known gap, and fails the run
    (twelfth review round).

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
`find_dependency_conflicts(candidate, pinned_versions_by_mod_id)`, wired
into BOTH `scripts/bump_mod.py`'s write path AND
`scripts/bump-dynamic-atmosphere.py`'s dispatch path (the latter added
in the eighteenth review round -- it had never called this at all
before, so a DA candidate declaring an existing pinned project
`incompatible` would still pin and publish successfully). It checks
exactly ONE thing: a candidate that declares another *currently-pinned*
project `incompatible` -- the one fact Modrinth's own metadata can
answer with no guessing, since NeoForge's own per-jar manifest has no
`incompatible` declaration at all. It briefly also compared a
`required` dependency's exact `version_id` (seventeenth round) and then
refused ANY unpinned `required` dependency as unverifiable (eighteenth
round) -- both REVERTED in the twenty-first round: Modrinth's
`dependencies` array is a project-WIDE author declaration, not
loader-scoped, so a NeoForge build inherits whatever a Fabric build of
the same version happens to declare, which produced a confirmed false
positive (Cristel Lib declaring Cloth Config API `required`, a
Fabric-only need the real NeoForge jar does not have) that permanently
excluded a healthy pin from auto-bumps. Required-dependency
satisfaction -- both "missing entirely" and "present but the wrong
version" -- is now checked exclusively by
`scripts/dependency_closure_check.py` (vendored verbatim from
brooswit-minecraft/schematic, the same checker `ci.yml`'s own
`dependency-check` job already trusts), run as a GATE against the
GENERATED tree's real jars, after `build`, on both write paths -- see
"Dependency closure -- the real-jar gate" below. Reading the actual
`META-INF/neoforge.mods.toml` manifest has no loader-mismatch
false-positive class and catches both failure shapes the Modrinth-
metadata comparison attempted, strictly superseding it. A full
dependency-GRAPH solve (transitive requirements beyond one level)
remains out of scope for both checks -- a real gap, not an oversight --
see "Status."

## Dependency closure -- the real-jar gate (twenty-first review round)

`scripts/dependency_closure_check.py` is vendored VERBATIM from
`brooswit-minecraft/schematic` at the exact commit
`.github/workflows/ci.yml`'s `dependency-check` job already trusts (via
`reusable-dependency-check.yml`'s own internal checkout of that same
commit) -- reused unmodified, not reimplemented, so there is exactly
ONE dependency-closure implementation this repo trusts, not two that
could silently drift apart. It downloads every pinned mod's REAL jar
(hash-verified against the pin), reads each one's actual
`META-INF/neoforge.mods.toml`, and confirms every `required` dependency
is satisfied -- in range -- by another pinned jar or a nested jarjar
jar. `minecraft`/`neoforge`/`forge`/`java`/`fml` are platform-provided
and only NOTED, never failed.

Both `scripts/bump_mod.py`'s and `scripts/bump-dynamic-atmosphere.py`'s
`default_gate_commands` run it as a new `"dependency_closure"` gate,
positioned AFTER `build` and before anything is committed -- same
fail-closed/clean-rollback guarantee as `refresh`/`check`/`build`
(`EXIT_GATE_FAILURE` / `"gate_failed"`, caught by the same
`restore_repo` path). This closes the gap
`modrinth_compat.find_dependency_conflicts` could never close even at
its widest (the eighteenth round's version): Modrinth's own metadata
can declare a `required` dependency with NO exact `version_id` at all
(only a project reference, a semver RANGE requirement), which the
Modrinth-metadata comparison could never evaluate regardless of how it
was written -- confirmed live: a candidate requiring `othermod [2.0,)`
against a pinned `othermod 1.0.0` bumped successfully under the
Modrinth-only check, but the real jar checker correctly refuses it.
`tests/test_bump_mod.py`'s `TestRealDependencyClosureGate` and
`tests/test_bump_dynamic_atmosphere.py`'s
`TestDependencyClosureGateWiring` cover this (the DA path's own test
class stops at wiring/ordering/rollback with a stubbed command, not the
real checker, because DA's `validate_payload` requires `download_url`
to be a real `https://cdn.modrinth.com/...` URL -- anti-tampering
against an untrusted webhook payload -- so DA's own candidate pin
cannot point at a local `file://` jar the real checker could fetch
offline/deterministically the way the generalized path's candidate,
with no such restriction, can).

`default_gate_commands` now runs this gate with `--strict` (twenty-second
round): it runs unattended, so a warning (e.g. "no nested jars found
anywhere") has no human present to notice it the way a PR reviewer
watching `ci.yml`'s own non-strict `dependency-check` job does. Confirmed
live before adding this: today's real pack has 36 nested jars and 71
satisfied required dependencies, zero warnings -- `--strict` changes
nothing today, only if that regresses later. Override via
`DA_BUMP_DEPENDENCY_CLOSURE_CMD`.

A dependency_closure gate failure now gets its OWN outcome string,
`"dependency_closure_gate_failed"`, distinct from `refresh`/`check`/
`build`'s generic `"gate_failed"` (`run_gate` in
`scripts/bump-dynamic-atmosphere.py`, twenty-second round). The
distinction matters one layer up, in `scripts/poll-mod-updates.py`'s
`_is_expected_gap`: that trio failing means the build pipeline itself is
broken and must fail a poll run loudly, but a dependency_closure failure
means the real checker looked at a real candidate jar and correctly
refused it -- exactly as unpredictable per-candidate as
`dependency_conflict` (see below), so it joins that same unconditionally-
expected bucket instead of being red-by-construction.

Nothing previously re-checked that the vendored copy's header-claimed
commit still matches what `ci.yml`'s pinned
`reusable-dependency-check.yml` ref actually checks out --
`scripts/check_vendored_dependency_closure_sync.py` (twenty-second
round) does, by reading both pins and fetching the live workflow file
from GitHub (two unauthenticated reads). It is deliberately NOT wired
into the test suite or any CI gate -- a network hiccup must never fail a
build the way a flaky network-dependent unit test would -- run it by
hand (or from a separate, non-blocking job) whenever bumping either pin.

## Runtime/platform compatibility -- the lower-bound-only gap (twenty-second review round)

`dependency_closure_check.py` is vendored VERBATIM and must never be
hand-edited -- but it deliberately never fails on a
`minecraft`/`neoforge`/`forge`/`java`/`fml` ("platform") mismatch; those
are only NOTED. That leaves a real gap the vendored file can't close
without becoming a fork: a candidate's own jar can require a NEWER
NeoForge (or Minecraft) than what this pack actually runs, and nothing
previously caught it before commit.

`check_runtime_compatibility_or_die` (new, in
`scripts/bump-dynamic-atmosphere.py`, reused by `bump_mod.py` the same
way `download_and_verify` already is) closes this -- as a SEPARATE
check, not a vendored-file edit. It reuses only the vendored module's
own public-by-import helpers (`PLATFORM`, `_RANGE`, `compare`,
`read_mods`, `pack_versions`) to read the CANDIDATE's own jar manifest,
filter to platform-id dependencies, and check each one's lower bound
only -- an upper-bound-only mismatch (e.g. a candidate capped at
`[1.21,1.21.1)` against a pack already on `1.21.1`) is deliberately left
alone, same "only noted" semantics the vendored checker itself uses for
platform deps, just applied to the one direction (too-old) that is
actually safe to fail closed on; the other direction (too-new) still
just means the vendored checker's own existing "noted, not failed"
behaviour for upper bounds. Runs right after each engine's
`download_and_verify` call (which now returns the verified jar bytes
instead of discarding them, so this check needs no second download);
raises `EngineError(EXIT_CONFIG_ERROR, "runtime_incompatible", ...)` on
a violation, same exit code `dependency_conflict` already uses for "the
data says this candidate can't go in this pack," with the same clean
rollback every other pre-commit check gets.

Test coverage: `tests/test_bump_mod.py`'s `TestRuntimeCompatibilityCheck`
and `tests/test_bump_dynamic_atmosphere.py`'s own class of the same name
-- both cover a lower-bound violation (fails closed), a satisfied lower
bound (still bumps), and (generalized path only) an upper-bound-only
mismatch (still bumps, confirming the "noted, not failed" semantics are
preserved). Unlike the dependency_closure GATE, this check runs
IN-PROCESS against bytes the test's own stubbed `downloader` already
delivered -- no second network fetch happens, so DA's
`validate_payload` URL-format requirement does not block using real jar
bytes here the way it blocks a true end-to-end `file://` test of the
dependency_closure gate itself.

`runtime_incompatible` joins `scripts/poll-mod-updates.py`'s
`_UNLISTABLE_EXPECTED_GAP_OUTCOMES` (same bucket as
`dependency_conflict`/`dependency_closure_gate_failed` above): a real
candidate jar's real manifest correctly refusing a real pack is exactly
as unpredictable per-candidate as those two, so a poll run hitting it
should not hard-fail the whole run any more than they do.

## GitHub-release-only managed mods (twenty-third review round; `dynamic-skateboards` added MINECRAFT-421)

Four managed mods ship jars as GitHub Release assets rather than through
Modrinth -- `dynamic-despawn`, `magicavoxel-loader`, `dynamic-battle-axes`,
`dynamic-whips`. All four, `dynamic-skateboards` (MINECRAFT-421 -- see
"Enrollment" above for why its Modrinth tracking was reconciled to
GitHub), and the other managed mods that DO have GitHub-release pins for
reasons unrelated to this ticket (`dynamic-atmosphere`'s own pin,
`dynamic-backpacks`, `dynamic-population`, `dynamic-terrain`,
`dynamic-vehicles`, `isometric-camera` -- confirmed by reading
`origin/main` directly), are tracked via packwiz's OWN native
`[update.github]` pin schema: `slug` (`"owner/repo"`), `tag` (the
currently-pinned release tag), `branch` (see below), `regex` (an
asset-name filter). This is NOT an invented schema -- an earlier round of
this ticket built one (`owner`/`repo` fields) before checking it against
real pins, and comment 48415 on MINECRAFT-362 corrected it to match
packwiz's actual shape (`github/updater.go` in packwiz itself).

**Why bare `packwiz update github` cannot do this job.** Verified against
pinned packwiz revision `dfd8b68a4796c763e25bad50265ea1f1233e24f1`:
`ghUpdater.CheckUpdate` calls `getLatestRelease(slug, branch)`, which --
whenever `branch` is non-empty -- filters the repository's releases down
to the one whose own `target_commitish` equals that exact string, and
returns an error otherwise. Every real `[update.github]` pin in this pack
stores `branch` as a frozen, exact 40-hex commit SHA (the source commit
the CURRENTLY pinned jar was built from), and a brand-new release's own
`target_commitish` essentially never equals an old, frozen SHA again --
so bare `packwiz update github` can never discover a new release for any
of these pins as configured. Separately, `ghUpdater.DoUpdate` discards
`CheckUpdate`'s own regex-selected asset and picks "the last asset ending
in `.jar`" from the release's full, UNFILTERED asset list instead (see
that function's own `// yes, this is duplicated` comment) -- a real
correctness gap if a release ever has more than one matching asset
differently ordered than intended. `github/install.go`'s own hashing
(`Asset.getSha256`) hashes whatever bytes it downloads but never compares
them against GitHub's own `digest` field on the asset -- no independent
tamper check. None of this makes packwiz "broken" for its own intended
use (a human running `packwiz update` interactively, who can see and
correct a wrong pick); it is simply the wrong tool for an unattended,
fail-closed write path, for the same reason this whole ticket does not
reuse packwiz's own Modrinth updater either.

**What `scripts/github_release_identity.py` and `scripts/
bump_mod_github.py` do instead**, reusing the same shared fail-closed
gates (identity → digest verification of downloaded bytes → dependency
closure → runtime compatibility → refresh/check/build → commit; no
commit if any step fails) every other write path in this ticket gets:

- Discovery (`resolve_latest_matching_release`) lists ALL of a slug's
  releases, keeps only non-draft ones (and, unless the CALLER passes
  `include_prerelease=True`, non-prerelease ones too -- see "Preserved
  release channels" below), and picks the newest by `created_at` --
  explicitly re-sorted, not trusted to already be ordered that way by
  the API. It takes no `branch`/`pinned_source` argument at all: it is
  completely independent of the pin's own frozen `branch` value, which
  is exactly what avoids packwiz's own freeze bug above.
- Asset selection (`match_asset`) evaluates the pin's own `regex` against
  the release's asset names, but only understands the one shape every
  real pin in this pack actually uses today -- `^.+(?<!SUF1|SUF2|...)\.jar$`
  (packwiz's own install-command default) or the plain `^.+\.jar$` --
  because Python's stdlib `re` cannot evaluate a variable-width negative
  lookbehind alternation at all, and this repo is stdlib-only (see
  `bump-dynamic-atmosphere.py`'s own docstring). Zero or multiple matches
  fails closed (`no_parseable_candidate`), same "exactly one unambiguous
  file" shape the Modrinth path already requires. An exotic regex this
  module does not recognize also fails closed with a clear message,
  rather than being silently mis-evaluated.
- Identity (`sha256` from GitHub's own `assets[].digest`) is verified
  against the downloaded bytes the same way DA's `sha1`/`sha512` is.
  Before that, `run_engine` re-resolves the CURRENTLY-pinned tag's own
  asset identity fresh (never just compares tag/version strings) and
  compares it against what this pin actually has on disk
  (`[download].url`/`.hash`) -- a bare tag-string or version-number
  match is never alone enough to return `already_pinned`; a changed
  digest, a moved asset, an equal-version retag with different bytes,
  OR A MOVED SOURCE COMMIT all fail closed as `integrity_conflict`
  instead of being accepted as a harmless replay (twenty-fourth review
  round, comment 48456 item 2; twenty-fifth round, comment 48492's
  follow-up closes two gaps the twenty-fourth round's version left
  open):
  - **Source identity**: `_verify_identity_matches_pin` independently
    re-peels the tag's own current target (`resolve_tag_source_sha`)
    and compares it against `pin.pinned_source` (the on-disk `branch`
    field) -- a tag silently re-pointed to a different commit, with its
    URL and digest both unchanged, previously passed as a no-op because
    neither no-op return path ever called `resolve_tag_source_sha` at
    all. The equal-version-retag path compares the NEW tag's peeled
    source against the OLD pin's recorded source the same way, on top
    of its existing digest check.
  - **Non-sha256 on-disk hash formats**: the real `dynamic-despawn` pin
    predates this engine and still records its hash as `sha512`.
    Comparing only GitHub's sha256 digest against a `pin.pinned_hash`
    that is never itself sha256 is comparing nothing -- skip the
    content check entirely, and a byte-for-byte-replaced asset at an
    unchanged URL passes as a verified no-op. For any `pinned_hash_
    format` other than `"sha256"`, the asset's bytes are now actually
    downloaded (reusing `download_and_verify_sha256`'s own sha256
    check against GitHub's digest first) and re-hashed in the pin's own
    format for comparison.
- On a real bump, the NEW release's own TAG is resolved to the
  immutable commit it points at (`resolve_tag_source_sha`, via
  `refs/tags/{tag}`, peeling one more hop for an ANNOTATED tag) and
  written to the new pin's `branch` field -- NEVER via the release's
  `target_commitish`, which only names the branch/commit a release was
  CREATED against and is not proof of what the tag itself currently
  points at (twenty-fourth round, comment 48456 item 1; the real
  `magicavoxel-loader` v0.1.3 tag is annotated, confirmed live).
  `branch` is written-only from the write path's own perspective:
  nothing in this engine ever reads it back for discovery.
- **Preserved release channels** (twenty-fourth round, comment 48456
  item 4): `scripts/poll-mod-updates.py`'s own poll loop determines
  `include_prerelease` fresh, per pin, from whatever the CURRENTLY-
  pinned tag's own `prerelease` flag actually is, before calling
  discovery -- never a global default. This pack's real
  `dynamic-atmosphere` (`v0.36.0-alpha.1`) and `dynamic-population`
  (`v0.3.0-alpha.1`) pins are themselves prereleases, confirmed live;
  without this, discovery's default (stable-only) would silently drop
  them out of their own accepted channel the first time either polled.
- No Modrinth-style `find_dependency_conflicts` (GitHub releases carry no
  structured dependency metadata to check against); required-dependency
  satisfaction is still fully covered by the real-jar `dependency_closure`
  gate every write path shares. No changelog-heading category escalation
  (free-form prose, no measured heading convention); category comes from
  the semver delta alone. No `--allow-breaking`/migration-text CLI path
  yet -- none of these mods has shipped a major-version release to design
  against; the `breaking_change_requires_human_review` gate refuses one
  unattended, same as the Modrinth path's own gate, until a real case
  exists to design a CLI around.

`scripts/poll-mod-updates.py`'s scheduled poll resolves these pins too,
sharing ONE one-bump-per-run counter with its Modrinth loop: a Modrinth
bump this run means the GitHub loop runs no code at all this run, not
just that it stops early -- the same "at most one `release.yml` run per
poll" contract the Modrinth loop's own docstring already describes.

## Identity/hash verification -- shared with DA, not duplicated

`scripts/modrinth_metadata.py`'s `verify_version_file_metadata()` is the
exact check `bump-dynamic-atmosphere.py`'s AC3 gate (version id, project
id, version number, exactly-one-file-by-name, sha512/sha1/url) already has
reviewed and tested, extracted so both the DA engine and any future
generalized write-path call the same implementation. `scripts/bump_mod.py`
re-fetches `GET /v2/version/{id}` fresh and verifies it against exactly
the candidate `modrinth_compat.py` picked from the version-LIST response,
before any pin is ever written.

It also optionally checks `(game_version, loader)` compatibility
(`expected_game_version`/`expected_loader`, both-or-neither; seventeenth
review round) against the metadata's own declared `game_versions`/
`loaders` fields -- the same two fields `resolve_latest_compatible_version`
already filtered the version-LIST response on before this candidate was
ever selected. For `bump_mod.py`'s generalized path this is defense in
depth over that earlier filter ("never trust a filter you did not
verify"). For `bump-dynamic-atmosphere.py`'s dispatch path it is NOT
defense in depth -- it is the FIRST and ONLY (game_version, loader)
check anywhere on that path, since a dispatch payload originates outside
Modrinth and has no earlier compatibility filter at all. Before this
round, DA's own wrapper (`verify_against_modrinth_metadata`) never
passed these two parameters, so a dispatch declaring the wrong Minecraft
version or loader would pin and publish successfully (confirmed live
with fixture metadata declaring Minecraft 1.20.1/Fabric against the
1.21.1/NeoForge pack target).

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

## Normal rollout (twenty-fourth review round)

Every commit message builder in this ticket -- DA's `build_commit_
message`, the generalized `build_generic_commit_message`, and
`scripts/bump_mod_github.py`'s `build_github_commit_message` -- used to
attach `Release-Only: true` to every automated bump unconditionally
(added in an earlier round of this same ticket, on the premise that an
automatic pack commit should never deploy/restart the server by
itself). `scripts/server_update_policy.should_deploy` reads that
trailer's ABSENCE as permission to deploy (`value is None: return
True` -- it is an opt-OUT a release can set, not an opt-in an automated
commit needed to earn), so the unconditional trailer meant every
automated bump published WITHOUT ever updating the hosted server.

Reversed in the twenty-fourth round (MINECRAFT-362 review, comment
48456, item 5), per the ticket owner's own resumed directive (comment
48363: "whenever any mod is updated, update the modpack and cut a
release... make sure the server is updated (should be auto)"): none of
the three builders attaches `Release-Only` any more. An ordinary
automated bump -- DA's, the generalized Modrinth path's, or the GitHub-
release path's -- now deploys/restarts the server through the existing
chain (`release.yml` -> Modrinth publish -> `server-update.yml`) the
same way a human-authored release always has. This is not a new
deployment gate and does not touch `should_deploy`'s own logic at all --
it only stops these three builders from setting the one trailer that
opts a release OUT. A genuinely held, publication-only release remains
possible by setting `Release-Only: true` explicitly wherever that
decision is actually made; nothing in this ticket removes that
mechanism, only its blanket, automatic application to every bump.

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
index/hash consistency on the result. This is NOT the same claim as
"packwiz enforces dependency consistency" (MINECRAFT-362 review,
seventeenth round -- a prior version of this sentence overstated this):
`make refresh`/`make check`/`make build` regenerate and verify
`index.toml`'s own hashes against the files on disk; none of them read
or validate a mod's declared Modrinth dependencies against what else is
pinned. That check is `modrinth_compat.find_dependency_conflicts`'s own,
separate, narrower job (see "Compatibility and no-downgrade resolution"
above), run by `bump_mod.run_engine` before any gate executes, not by
packwiz at all. The write happens
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

**The CLI now commits, with the same trailer the automated path uses,
and an explicit pathspec.** A third round's CLI called `run_engine`
(which only stages) and stopped, leaving the operator to write their own
commit message with no `Mod-Auto-Bump` trailer -- not itself a deploy
problem (normal rollout is the contract either way, see "Normal rollout"
below), but it meant the operator had to hand-transcribe a trailer shape
git's parser is picky about, and the commit body would silently lose the
mod/version identity a real automated bump always records. `cli_main`
now builds the commit with the same `build_generic_commit_message`
`scripts/poll-mod-updates.py` uses (passing its own `source_text`, so
the commit body no longer claims "via scheduled Modrinth reconciliation"
on a bump a human made by hand) and checks `should_deploy(message)` is
`True` before committing (a plain `if`, not `assert` -- an `assert`
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
commit too, authored as `sickos-mod-auto-bump[bot]`, which `release.yml`
then publishes (and, per "Normal rollout" below, deploys). A pathspec'd
`git commit -- <paths>` records a snapshot of exactly those paths and
leaves anything else staged untouched (still staged, neither stolen nor
discarded).

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

48 tests in `tests/test_bump_mod.py` cover: a real engine-level
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
substitution, and the commit step carrying the Mod-Auto-Bump trailer
and normal-rollout contract (`TestCliMain`), an unrelated staged file surviving a
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
`KNOWN_ANONYMOUS_404_MOD_IDS` would name SPECIFIC projects individually
confirmed, by a live, authenticated check, to be unreadable even by the
CONFIGURED `MODRINTH_TOKEN` -- it is currently EMPTY (sixteenth review
round: a prior round populated it with 4 projects -- Dynamic Atmosphere,
Dynamic Skateboards, Dynamic Terrain, Dynamic Vehicles -- that returned
HTTP 404 to an ANONYMOUS caller, on the false premise that
`MODRINTH_TOKEN` was not yet configured; it IS configured and wired into
both `da-auto-bump.yml` and `mod-auto-bump-poll.yml`, so this module's
fetcher is never actually anonymous in a real run, and whether the
configured token can read those 4 projects specifically remains
unverified -- not the same claim as "needs a token to be set". Emptied
rather than kept on a false justification; re-populate only with
mod_ids a live, authenticated check has actually confirmed the
configured token cannot read). `_is_expected_gap()` checks each gap-shaped
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
loop actually reached, naming all known-uncovered pins from five static
lists (`KNOWN_ANONYMOUS_404_MOD_IDS`, `KNOWN_NON_SEMVER_PIN_NAMES`,
`KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES`, `KNOWN_ONLY_PRERELEASE_PIN_NAMES`,
`KNOWN_UNTRACKED_PIN_NAMES` -- twelfth review round added the fifth);
each clause skips itself when its list is empty (sixteenth review round
-- `KNOWN_ANONYMOUS_404_MOD_IDS` is currently empty, see below, and a
hardcoded "0 pins ... ()" clause printed on every run would itself have
been a standing false claim). As of this writing that is 12 pins across
the three non-empty lists (`KNOWN_UNTRACKED_PIN_PATHS` is now empty --
see "GitHub-release-only managed mods" below): the 3 non-semver ones
need either a human bump or a different comparator this document does
not attempt; the 5 no-parseable-candidate ones have NO parseable version
published on Modrinth at all, so a human bump cannot fix them either --
only a different comparator this document does not attempt would; and
the 4 only-prerelease ones have never published a release build for this
target and need the mod author to publish one, not a human bump.
Deliberately NOT
named here: a pin matching `EXCLUDED_MOD_NAMES` (Herobrine API,
Baritone) reports its own `"excluded_local_only"` outcome but is not a
coverage gap at all -- it is a permanent, intentional non-enrollment,
so the standing notice (which exists to list GAPS) omits it (thirteenth
review round).

**`downgrade_refused` is an expected gap too (sixth review round).** It
was still landing in `failed`, the red-by-construction defect's third
occurrence: it recurs IDENTICALLY every run for a given pin until
something upstream changes, the same shape as the two outcomes above.
It fires every run for a pin whose currently-pinned version is ahead of
the latest version `resolve_latest_compatible_version`'s
`version_type == "release"` filter will ever return (e.g. pinned to a
prerelease) -- confirmed live: `mods/ice-and-fire-community-edition.pw.toml`
is pinned ahead of its newest actual release today, and lands on
`unsupported_version_scheme` only incidentally, because its pinned
version string happens not to be valid semver; a pin in the same
situation with a valid-semver pinned version would hit
`downgrade_refused` every run instead, unclassified. It is unconditionally
expected via `_UNLISTABLE_EXPECTED_GAP_OUTCOMES` (no per-mod-id
allowlist needed, unlike the five outcomes elsewhere in this doc -- it
is already established per-pin by one deterministic check, not a
catch-all, and which specific pin hits it next isn't knowable in
advance the way the other outcomes' pins are).

**`dependency_conflict`'s history (sixth, nineteenth, and twenty-first
review rounds) -- now settled back to unconditionally expected, like
`downgrade_refused`.** Originally reasoned about the same way as
`downgrade_refused` above: a pure function of the re-resolved
candidate's own declared dependencies, so which pin hits it next isn't
knowable in advance -- true for the `incompatible`-and-currently-pinned
case, the only one `find_dependency_conflicts` checks today. For one
round (the eighteenth), a second check existed here too (`required`
dependency on any project not pinned at all), which broke that
reasoning: it found a real, confirmed false positive (Cristel Lib's
pinned version declares Cloth Config API `required`, but Modrinth's
version-level `dependencies` array is a project-WIDE author
declaration, not loader-scoped, and the actual NeoForge jar requires
only `neoforge`/`minecraft`), so the nineteenth round reconciled
`dependency_conflict` against a per-mod-id allowlist rather than
exempting the whole class. The twentieth round then found that fix
incomplete -- it reconciled the REPORTING but left the REFUSAL itself
untouched, so Cristel Lib stayed permanently excluded from auto-bumps
regardless. The twenty-first round fixed the actual root cause instead
of patching around it again: `find_dependency_conflicts` no longer
compares a `required` dependency AT ALL (see "Explicit scope limit"
above and "Dependency closure -- the real-jar gate" below for what
replaced it). With that removed, `dependency_conflict` can once again
only mean the one remaining, genuinely unpredictable-in-advance case,
so it is unconditionally expected again, with no per-mod-id allowlist
and no exclusion list to go stale.

Push-gating and credential-checking are driven by whether the run's
summary recorded a real bump, not by the script's overall exit code, so
an unexpected failure elsewhere in the same run never blocks pushing a
bump this run already made -- the job still fails afterward (so the
failure is visible), but only after that push.

36 tests in `tests/test_poll_mod_updates.py` cover: the same
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
classification itself: expected gaps alone keep the run green --
`downgrade_refused` unconditionally, `dependency_conflict` via its own
per-mod-id allowlist (nineteenth round) same as the other four
list-based outcomes -- a gap-shaped outcome for a mod_id NOT on that
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
confuse a generic bump for a DA one) and the same normal-rollout
contract (no `Release-Only` trailer -- see "Normal rollout" below).

## Concurrency / shared-lock / latest-main handling

**Wired, not only designed.** `.github/workflows/da-auto-bump.yml`'s
`concurrency.group` was renamed from `da-auto-bump` to `mod-auto-bump`,
and `mod-auto-bump-poll.yml` uses that same group name -- GitHub Actions
concurrency groups serialize across *different* workflow files that share
a group name, not only within one file, so a DA dispatch and a scheduled
poll run now queue against each other, not just against runs of their own
kind. Both now pin `actions/checkout`'s `ref: main` explicitly
(seventeenth review round), so a later run always recomputes against
whatever the earlier run already pushed. Before this round, that was
true for a run's FIRST attempt but false for a QUEUED run or a RE-RUN:
`actions/checkout` without an explicit `ref` defaults to the triggering
event's own ref/sha, which a queued run (held behind an in-progress one
in this shared concurrency group) or a re-run of a prior job keeps even
after a later run has already pushed -- so either would silently
recompute against a stale base rather than the real current tip of
`main` (confirmed: a stale-based push is safely rejected by git as
non-fast-forward, not silently accepted, but the documented
"recomputes from the current tip" claim was still false for those two
cases specifically). Pinning `ref: main` means the checkout step always
resolves that named ref at ITS current state when the step actually
runs, not a captured sha from whenever the job was first queued.

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
  was unparseable" or "only prereleases exist"); `find_dependency_conflicts`
  briefly extended, across the seventeenth and eighteenth rounds, to
  also compare a `required` dependency's exact version (whether pinned
  or not), both reverted in the twenty-first round once that comparison
  was confirmed to produce a false positive against Modrinth's own
  project-wide (not loader-scoped) dependency metadata -- it checks
  only `incompatible` again today (46 tests).
- `scripts/modrinth_metadata.py` -- identity/hash verification, extracted
  from and still used by the DA engine, plus (seventeenth round) an
  optional `(game_version, loader)` compatibility check against the
  metadata's own declared `game_versions`/`loaders` fields, both-or-
  neither (18 tests, up from 12).
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
  same shape as `scripts/bump_mod.py`'s own fix. Also gained (seventeenth
  round) the SAME `(game_version, loader)` compatibility check threaded
  into `verify_against_modrinth_metadata` -- unlike the generalized
  path's defense-in-depth re-check, this was the FIRST and ONLY such
  check anywhere on this dispatch path, since a dispatch payload has no
  earlier compatibility filter at all; (eighteenth round)
  `modrinth_compat.find_dependency_conflicts` wired in for the first
  time, reading `verify_against_modrinth_metadata`'s now-full metadata
  return value for its `dependencies` field; and (twenty-first round)
  the new real, vendored `dependency_closure` gate wired in too, after
  `build` -- tested at the wiring/ordering/rollback level with a
  stubbed command, not the real checker, since DA's own
  `validate_payload` requires a real `cdn.modrinth.com` `download_url`
  (anti-tampering), which rules out a local, deterministic `file://`
  jar the way the generalized path's own test (no such restriction)
  uses (86 tests, up from 74 across the eighth, seventeenth, eighteenth
  and twenty-first rounds; several others adjusted along the way to use
  a genuinely-matching-category version delta rather than relying on
  the old payload-only default).
- The direct-pin-vs-packwiz decision above.
- Normal rollout on both DA's and the generalized path's generated
  commits (no `Release-Only` trailer -- MINECRAFT-362 review, comment
  48456, item 5; see `da-auto-bump.md`).
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
  installed) runs the real `packwiz refresh` binary too; two tests
  exercising the new `(game_version, loader)` defense-in-depth re-check
  against a freshly-refetched candidate's own metadata (seventeenth
  round); and (twenty-first round) two tests exercising the real,
  vendored `dependency_closure` gate against real zip jar bytes served
  over `file://` -- a version-range mismatch the Modrinth-metadata
  check could never evaluate even at its widest, and a satisfying
  positive control (48 tests).
- `scripts/poll-mod-updates.py` + `.github/workflows/mod-auto-bump-poll.yml`
  -- the generalized trigger, now covering Dynamic Atmosphere's own pin
  too, reading `pack.toml`'s `[versions]` shape strictly (raises rather
  than silently defaulting to `None, None`), passing a changelog-derived
  category HINT (not the deciding factor -- see above) to each
  candidate's bump, making AT MOST ONE bump commit per run (so it never
  asks `release.yml` to run twice before the first run finishes),
  classifying five gap-shaped outcome kinds (`resolution_failed`,
  `unsupported_version_scheme`, `no_parseable_candidate`,
  `only_prerelease_available`, `untracked` -- see "Enrollment" above)
  each against its OWN explicit `KNOWN_*_PIN_IDS`/`KNOWN_UNTRACKED_PIN_PATHS`
  allowlist (never the whole outcome class), a growing set of genuinely
  unlistable-in-advance outcomes (`breaking_change_requires_human_review`,
  `downgrade_refused`, `dependency_conflict` -- the last briefly had its
  own allowlist in the nineteenth round, reverted in the twenty-first
  once its root cause was fixed at the source instead, see
  "Compatibility and no-downgrade resolution" above; joined in the
  twenty-second round by `dependency_closure_gate_failed` and
  `runtime_incompatible`, see "Runtime/platform compatibility" and
  "Dependency closure" above) staying unconditionally expected --
  `_UNLISTABLE_EXPECTED_GAP_OUTCOMES` is the live set, not a number
  worth repeating here -- plus `excluded_local_only`/`excluded_pin_path`
  unconditionally expected for a different reason (no meaningful mod_id
  to reconcile by), and an
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
  by the bare `continue` this replaced, and (sixteenth round)
  `KNOWN_ANONYMOUS_404_MOD_IDS` emptied with a dedicated test asserting
  `"resolution_failed"` is never an expected gap on its own any more.
  `dependency_conflict` passed briefly through its own per-mod-id
  allowlist (nineteenth round, after a confirmed false positive on
  Cristel Lib) before settling back to unconditionally expected
  (twenty-first round, once the root cause was fixed at its source --
  see "Compatibility and no-downgrade resolution" above) (36 tests).
  `run_engine`'s category determination and no-downgrade
  check both now take/use a `game_version` hint and an identity-checked
  previous-pin metadata, threaded through from this script's own
  `read_pack_targets` result (see the Compatibility and Write Path
  sections above).
- The shared `mod-auto-bump` concurrency group, covering both mechanisms.
- **Checkout pinned to `ref: main`** in both `mod-auto-bump-poll.yml` and
  `da-auto-bump.yml` (seventeenth review round) -- without it,
  `actions/checkout` defaults to the triggering event's own ref/sha.
  For `mod-auto-bump-poll.yml`'s `workflow_dispatch` trigger that is
  WHATEVER ref the dispatch call named, so a manual dispatch against an
  ahead-of-main feature branch would fast-forward that branch's own
  unrelated ancestors onto `main` along with the generated bump commit
  (confirmed live against an isolated local bare repository, no remote
  push performed). For both workflows, a queued or re-run job otherwise
  keeps the ORIGINAL event's sha even after a later run in the same
  concurrency group has already pushed, recomputing against a stale
  base rather than the real current tip of `main` (confirmed: local Git
  reproduction of a rejected push from a stale base). `ref: main` fixes
  both. `tests/test_auto_bump_workflow_checkout.py` guards this by
  asserting the YAML itself pins it, since no Python-level test
  otherwise exercises the real `actions/checkout` behavior (2 tests).
- `modrinth_compat.find_dependency_conflicts` -- refuses a candidate that
  declares a currently-pinned project `incompatible`, the one thing
  Modrinth's own metadata can answer with no guessing (11 tests, plus 5
  integration tests through `bump_mod.run_engine` and 4 through
  `bump.run_engine`). Briefly also compared a `required` dependency's
  exact version (seventeenth and eighteenth rounds), both reverted in
  the twenty-first round -- see "Compatibility and no-downgrade
  resolution" above for why, and the `dependency_closure` gate entry
  below for what replaced it. Wired into BOTH write paths since the
  eighteenth round: `scripts/bump_mod.py`'s and
  `scripts/bump-dynamic-atmosphere.py`'s.
- **`scripts/dependency_closure_check.py`** (twenty-first round) --
  vendored VERBATIM from `brooswit-minecraft/schematic` at the exact
  commit `ci.yml`'s own `dependency-check` job already trusts. Run as
  a new `"dependency_closure"` gate, after `build`, on BOTH write
  paths' `default_gate_commands`/`run_engine` (same fail-closed/clean-
  rollback guarantee as `refresh`/`check`/`build`). Reads every pinned
  mod's REAL jar (`META-INF/neoforge.mods.toml`) and confirms every
  `required` dependency is satisfied, in range, by another pinned jar
  or a nested jarjar jar -- strictly more accurate than comparing
  Modrinth's project-wide, not loader-scoped, `dependencies` metadata,
  with no loader-mismatch false-positive class. Covered by 2 real
  (not stubbed) tests on the generalized path, using real zip jar
  bytes served over `file://`, and 3 wiring/ordering/rollback tests on
  the DA path using a stubbed command (DA's own `validate_payload`
  requires a real `cdn.modrinth.com` `download_url`, which rules out a
  local `file://` jar for a true end-to-end test there).

**Not implemented -- real gaps, not oversights:**
- **Full dependency-GRAPH validation**: neither `find_dependency_conflicts`
  nor the new `dependency_closure` gate resolves a transitive
  requirement (a dependency of a dependency) or a semver RANGE against
  anything beyond one level of pinned/nested jars. That fuller graph
  solve remains out of scope.
- The GitHub-release-only mods (`dynamic-despawn`, `magicavoxel-loader`,
  `dynamic-battle-axes`, `dynamic-whips`) now have a verified-identity
  mechanism (`scripts/github_release_identity.py`, `scripts/
  bump_mod_github.py` -- see "GitHub-release-only managed mods" above),
  wired into `scripts/poll-mod-updates.py`'s scheduled poll. Not yet
  exercised against a REAL new release for any of these mods (every one
  is currently already at its own latest release) -- that first live run
  is the same kind of gap "No live run of either mechanism" below already
  names for the generalized Modrinth path.
- **Dynamic Population**: explicitly deferred per this ticket's own
  prerequisite -- it is not added through this or any path until
  MINECRAFT-29's Fillager integration lands and a compatible verified
  release exists.
- `render_github_pin` reconstructs a fixed field subset (name, filename,
  side, download url/hash, update.github slug/tag/branch/regex) rather
  than surgically merging into the pin's existing TOML (comment 48456,
  item 3's "preserve unrelated metadata" concern). Checked against
  every real `[update.github]` pin on `origin/main` directly: none
  carries any field beyond this fixed set today, so this is not a live
  bug -- but it is a real latent gap if a future pin ever gains one
  (e.g. a packwiz `[option]` table), left as a known limitation rather
  than built speculatively against a field shape nothing in this pack
  has yet.
- No live run of either mechanism has happened, since PR #104 (this
  ticket's implementation) has not merged: `mod-auto-bump-poll.yml` has
  never actually fired against the real Modrinth API. Two DIFFERENT
  credentials are involved here, and their readiness is NOT the same
  claim (sixteenth review round: a prior version of this bullet
  conflated them under one false "no credential has been set"): the
  PUSH credential (`SICKOS_DISPATCH_APP_ID`/`SICKOS_DISPATCH_APP_PRIVATE_KEY`/
  `SICKOS_DISPATCH_TOKEN`) is genuinely not configured today, confirmed
  by `gh secret list`/`gh variable list`; but `MODRINTH_TOKEN` IS
  configured and already wired into both `da-auto-bump.yml` and
  `mod-auto-bump-poll.yml` -- what remains genuinely unverified is
  whether that CONFIGURED token has read access to every project it
  needs to, which only a live, authenticated run can confirm. Unit
  tests exercise every decision path against mocked Modrinth responses;
  they are not live-run evidence for either claim.
- **12 of the 78 eligible pins are documented, permanent, per-pin
  coverage gaps today** (established by a live, authorized, read-only
  resolution against Modrinth, done under the review process for this
  ticket -- no dispatch, credential, write, or publication probe;
  recount as of the sixteenth review round, which dropped a 4-pin class
  previously counted here -- see below). Three classes, each with its
  OWN outcome string and its own remedy -- conflating them, or
  conflating any of them with the TRUE catch-all `"resolution_failed"`,
  was itself the tenth round's bug (see "Not every non-success
  outcome..." above):
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

  A fourth list-based mechanism exists for `"resolution_failed"`
  (`KNOWN_ANONYMOUS_404_MOD_IDS`) but is currently EMPTY, not a fourth
  12-pin-contributing class (sixteenth review round): it previously
  named Dynamic Atmosphere, Dynamic Skateboards, Dynamic Terrain, and
  Dynamic Vehicles on the premise that `MODRINTH_TOKEN` was not yet
  configured. That premise is false -- the token IS configured and
  wired into both `da-auto-bump.yml` and `mod-auto-bump-poll.yml` -- so
  this module's fetcher is never actually anonymous in a real run, and
  these 4 pins' real resolvability with the configured token is
  UNVERIFIED, not a confirmed permanent gap. Emptying the list means a
  `"resolution_failed"` for any of these 4 (or any other pin) now fails
  the run loudly instead of warning on a premise that no longer holds;
  re-populate only with mod_ids a live, authenticated check has
  actually confirmed the configured token cannot read.

  All three of the populated classes report as `::warning::` lines
  (when a run's loop reaches them) and a standing `::notice::` reminder
  every run regardless of what that run's loop reached, so this list
  stays visible without paging on it every 20 minutes -- NOT as a
  failed run for a pin ON its matching list. This is true of all of
  them equally (eleventh review round generalized it past just the
  first, when there were four instead of three): each is checked
  against its OWN `KNOWN_*_PIN_IDS` dict by `mod_id`, so EVERY one of
  them fails the run for a pin hitting that outcome while NOT on its
  matching list -- the signal a genuinely unexpected failure (an
  outage, a revoked/insufficiently-scoped token, a new 404, a mod
  quietly changing version scheme) still needs, not only for
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
