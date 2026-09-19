# Dynamic Atmosphere auto-bump engine

`scripts/bump-dynamic-atmosphere.py` turns a Dynamic Atmosphere (DA)
release-trigger payload into a correct, built, ready-to-commit working
tree: a rewritten DA pin, a new `pack.toml` version, a mechanically
updated README sentence, and a committed release-notes file -- plus a
machine-readable JSON summary. It never pushes, commits, tags, creates a
GitHub release, or touches Modrinth (including Modrinth moderation state).
A human (or a later CI workflow, out of scope here) reviews and pushes the
result.

This document is the contract a fresh agent or workflow needs: how the
engine is invoked, every exit code, and every summary field. If the code
and this document ever disagree, that is a bug in one of them -- file it,
don't guess which is right.

## Invocation

```
python3 scripts/bump-dynamic-atmosphere.py \
  --payload <client_payload.json> \
  [--repo-root <path>] \
  [--summary-out <path>] \
  [--release-body <path>]
```

- `--payload` (required): a JSON file containing the DA release workflow's
  `client_payload` object exactly as documented in SICKOS-72/SICKOS-75:
  `version`, `modrinth_version_id`, `sha1`, `sha512`, `download_url`,
  `file_name`, `category`, `github_release_url`. The exact shape (and
  nothing but those 8 keys) is pinned in
  `tests/fixtures/dynamic-atmosphere-released.example.json`, a byte-stable
  example payload both repos can build against -- DA's own trigger-payload
  work (ATMO-15) is expected to copy these same bytes, so this file is the
  shared source of truth for the contract's shape, not just a test fixture.
  `tests/test_bump_dynamic_atmosphere.py`'s `TestSharedContractFixture`
  asserts it has exactly the 8 agreed keys and validates successfully.
- `--repo-root` (default `.`): the packwiz repo root the engine operates
  on. Must contain `pack.toml`, `mods/dynamic-atmosphere.pw.toml`,
  `mods/create.pw.toml` (used as the vouch-check control), and `README.md`.
- `--summary-out`: if given, the summary JSON (see below) is also written
  to this path. The summary is **always** printed to stdout as well,
  regardless of this flag; a human-readable one-line status message is
  printed to stderr. Consumers that want only the JSON should read stdout
  or `--summary-out`, never stderr.
- `--release-body`: a local file to use as the DA GitHub release body
  instead of fetching `github_release_url` from the public GitHub API.
  Only read when the resolved category is `breaking` (see Migration
  extraction below). Lets tests and offline runs work without network
  access.

### Command overrides for the pack gates

The engine shells out to the same `make` targets a human runs locally
(`make refresh`, `make check`, `make build`), so local, CI, and the engine
cannot drift. Each is overridable independently via environment variables,
for tests or alternate environments that cannot run `make`/Go/packwiz:

- `DA_BUMP_REFRESH_CMD` (default `make refresh`)
- `DA_BUMP_CHECK_CMD` (default `make check`)
- `DA_BUMP_BUILD_CMD` (default `make build`)

Each is parsed with `shlex.split`. The engine's own test suite does not
set these; it instead calls the internal `run_engine()` function directly
with a `gate_commands` dict, which is the same mechanism `main()` uses
these environment variables to build.

## Exit codes

| Code | Constant | Meaning |
| --- | --- | --- |
| 0 | `EXIT_OK` | Success: either a clean bump was prepared and staged, or the payload's version was already pinned (a clean no-op). Check the summary's `changed`/`outcome` fields to tell which. |
| 1 | *(none)* | An unhandled exception. Always a bug in this script; never a designed outcome. |
| 2 | `EXIT_INVALID_PAYLOAD` | The payload failed validation (see "Payload validation" below). No file was touched. |
| 3 | `EXIT_INTEGRITY_FAILURE` | The downloaded jar's sha1 or sha512 did not match the payload, or the download itself failed. No repo file was touched; the temp jar is always deleted. |
| 4 | `EXIT_DOWNGRADE_REFUSED` | The payload's DA version is older than the currently pinned version. No file was touched. |
| 5 | `EXIT_ANOMALY` | Either (a) the payload's `modrinth_version_id` matches the pinned one but the sha512 differs, or (b) the payload's version string equals the pinned version string but the `modrinth_version_id` differs. Both are refused rather than silently resolved. No file was touched. |
| 6 | `EXIT_GATE_FAILURE` | `make refresh`, `make check`, or `make build` failed. The repo is restored to its pre-run state (see "Fail-closed restore" below). |
| 7 | `EXIT_MIGRATION_MISSING` | The category is `breaking` and either the release body could not be fetched, or no `## Migration` section was found in this release's own section of it. No file was touched. |
| 8 | `EXIT_NOTES_EXISTS` | `docs/releases/<new-version>.md` already exists. The engine never overwrites a published release's notes. No file was touched. |
| 9 | `EXIT_CONFIG_ERROR` | The repo's own state doesn't match what the engine expects (an unparsable DA pin filename, a `pack.toml` version that isn't a plain `x.y.z`, zero or more than one mechanical README sentence, an unexpected change under `mods/`, or `git status --porcelain` itself exiting non-zero while the dirty-tree guard below was checking whether the repo is clean -- not a git repo, `safe.directory` "dubious ownership", a corrupt index, etc.). This is a repo-state problem, not a payload problem, and needs a human. If any writes had already happened, the repo is restored (see below); the `git status` failure case fires before anything is touched. |
| 10 | `EXIT_UNEXPECTED_ERROR` | Any exception the write/gate phase raises that is *not* a designed `EngineError` -- a missing `make`/`packwiz` binary, an `OSError` writing a file, a `CalledProcessError`, anything unforeseen. The repo is still restored to its pre-run state exactly as for a designed failure (see below); this code exists only so `main()` never falls through to a bare Python traceback and exit 1 -- the summary and a non-zero, documented exit code are always produced. |
| 11 | `EXIT_DIRTY_TREE` | `pack.toml`, `index.toml`, `mods/dynamic-atmosphere.pw.toml`, or `README.md` already had an uncommitted change (staged, unstaged, or untracked) before this run started anything, per a successful `git status --porcelain` read. Checked first, before payload validation. No file was touched. (If `git status` itself fails rather than reporting cleanly, that is `EXIT_CONFIG_ERROR` (9) instead, not this code -- a failed check is a config problem, not evidence of a dirty tree.) |

## Fail-closed restore

On any of exit codes 3, 4, 5, 6, 7, 8, 9, 10, the repo's git status is
restored to exactly what it was before the run: no modified, staged, or
untracked files. Concretely, `pack.toml`, `index.toml`,
`mods/dynamic-atmosphere.pw.toml`, and `README.md` are restored (both index
and working tree) via `git restore --staged --worktree`, and any
`docs/releases/<version>.md` the run had already created is unstaged and
deleted.

That restore is a blind `git restore` of those four fixed paths -- on its
own it cannot tell this run's own edits apart from whatever was already
there when the run started. If any of the four already had an uncommitted
change before this run began, a restore-triggering failure would have
discarded that pre-existing change, and a successful run would have staged
it into the bump right alongside the DA update, silently. **This is why
`EXIT_DIRTY_TREE` (11) exists**: the engine checks `git status --porcelain`
against exactly these four paths as its very first act, before payload
validation or anything else, and refuses to start at all if any of them is
already dirty. Given that guard, and that nothing outside this process
modifies the repo while it runs, the restore only ever discards or stages
changes this run itself made. It does not defend against something else
writing to one of these paths *after* the guard passes but *while* this run
is still in flight -- that race is not addressed here.

This restore runs on **any exception** raised once the write/gate phase has
started, not only a designed `EngineError` -- a missing `make`/`packwiz`
binary, an `OSError` writing a file, a `CalledProcessError` from `git add`,
or anything else unforeseen is caught, the repo is restored the same way,
and the original exception is re-raised (`main()` then reports it as
`EXIT_UNEXPECTED_ERROR`, see above, instead of a bare traceback). Before
that phase starts (payload validation, the idempotency/version-order
checks, the Migration fetch, the integrity download, the vouch check), no
repo file has been touched yet, so there is nothing to restore.

Build output under the gitignored `build/` directory is explicitly **not**
cleaned up on failure; it is harmless there and cleaning it is not required
by the ticket that specified this contract.

## Order of operations

Every fallible check happens before any file is written, so a fail-closed
exit never has to undo more than the fixed set above:

1. Check that `pack.toml`, `index.toml`, `mods/dynamic-atmosphere.pw.toml`,
   and `README.md` are all clean (`git status --porcelain`) -- read-only,
   and first, before even the payload is looked at. Fail closed
   (`EXIT_DIRTY_TREE`) if any is dirty; fail closed (`EXIT_CONFIG_ERROR`) if
   the `git status` call itself fails rather than reporting cleanly.
2. Validate the payload.
3. Idempotency check (already-pinned / hash anomaly) -- read-only.
4. Version-order check (downgrade refusal / same-version anomaly) -- read-only.
5. Compute the new pack version from the category mapping rule, and check
   `docs/releases/<new-version>.md` does not already exist -- read-only.
6. If category is `breaking`: fetch (or read `--release-body`) and extract
   the Migration section; fail closed if missing -- read-only.
7. Download the jar to a temp directory **outside** the repo and verify
   both sha1 and sha512 -- no repo file touched; temp file always deleted.
8. Run the Modrinth vouch check (network, read-only, never fails the run).
9. Only now: rewrite the DA pin, rewrite `pack.toml`'s version, rewrite the
   README's mechanical sentence, compute the listing-review flags, write
   the notes file.
10. Run `make refresh` (this regenerates `index.toml`).
11. `git add` exactly the five intended paths: `pack.toml`, `index.toml`,
    `mods/dynamic-atmosphere.pw.toml`, `README.md`, and the new notes file.
    Staging must happen **after** refresh, because `make check`'s own gate
    compares the *unstaged* diff -- staging is what makes it pass, and if
    `index.toml` were staged before refresh regenerated it, the newly
    regenerated content would show up as an unstaged diff and fail `make check`.
12. Run `make check`, then `make build`.

## Payload validation

All of `client_payload` is treated as untrusted input from the DA release
workflow. Every field is validated before anything is read from or written
to the repo (aside from loading the currently pinned state, which is
read-only):

- `sha1`: exactly 40 hex characters.
- `sha512`: exactly 128 hex characters.
- `modrinth_version_id`: non-empty, `[A-Za-z0-9]+` only.
- `file_name`: a bare filename ending in `.jar`, matching
  `[A-Za-z0-9._-]+\.jar`; no `/`, no `\`, and neither `.` nor `..`.
- `download_url`: `https://cdn.modrinth.com/...` exactly (scheme `https`,
  host `cdn.modrinth.com`, nothing else).
- `version`: non-empty, `[A-Za-z0-9.+-]+` only (this is stricter than "no
  newlines or quotes" -- it is a positive allowlist, so nothing in it can
  ever be a TOML special character).
- `github_release_url`: must match
  `https://github.com/<owner>/<repo>/releases/tag/<tag>` with each of
  owner/repo/tag restricted to safe characters, **and** `<owner>/<repo>`
  must be exactly `brooswit-minecraft/dynamic-atmosphere` -- this is the
  only repository this engine will ever fetch a release body from.
- `category`: must be a JSON string or absent/`null`. A string that isn't
  `patch`/`minor`/`breaking` is accepted here (validation doesn't reject
  it) but is treated as "unrecognised" downstream -- see the mapping table.
  Any other JSON type (number, object, array, boolean) is rejected.

Any failure here exits `EXIT_INVALID_PAYLOAD` (2) before any repo file,
including the temp jar, is touched.

## Idempotency and version ordering

The currently pinned DA version string is extracted from the pin's own
`filename` field (`dynamicatmosphere-<version>.jar` -- `pack.toml`/the pin
carry no other field with the human DA version), which is why the pin's
filename must always follow that exact pattern; if it doesn't, that's
`EXIT_CONFIG_ERROR`, not a payload problem.

- **Same `modrinth_version_id`, same `sha512`:** already pinned. `changed`
  is `false`, `outcome` is `already_pinned`, exit 0, nothing touched.
- **Same `modrinth_version_id`, different `sha512`:** an anomaly (the same
  Modrinth version now hashes differently). Exit 5, nothing touched.
- **Different `modrinth_version_id`:** compare `payload.version` against
  the pinned version string using the semver-with-prerelease rule below.
  - Payload version **older**: refuse the downgrade. Exit 4.
  - Payload version **equal** (but a different `modrinth_version_id`): an
    anomaly -- DA re-published the same version string under a different
    artifact. Exit 5.
  - Payload version **newer**: proceed to bump.

### Version comparison rule

DA versions look like `0.19.0-alpha.1`. Comparison follows semver
precedence (semver.org rule 11) exactly:

1. Compare `(major, minor, patch)` numerically. If they differ, that
   decides it.
2. If the numeric triple is equal: a version **with** a prerelease suffix
   (the part after `-`) is older than the same triple **without** one.
3. If both have a prerelease suffix, split each on `.` into identifiers
   and compare left to right: a purely-numeric identifier compares
   numerically, otherwise lexically (ASCII); a numeric identifier is
   always older than an alphanumeric one at the same position; if every
   shared identifier is equal, the prerelease with **more** identifiers is
   newer.

Build metadata (a trailing `+...`) is accepted by the parser but never
affects ordering, per semver's own rule. A version string that doesn't
parse as `major.minor.patch[-prerelease][+build]` cannot be compared and
is `EXIT_CONFIG_ERROR`.

## Version selection (pack.toml bump)

Never a blind patch bump. The mapping (see `bump_pack_version()`):

| Category received | New pack version | `mapping_rule` in the summary |
| --- | --- | --- |
| `"patch"` | `x.y.z` -> `x.y.(z+1)` | `patch` |
| `"minor"` | `x.y.z` -> `x.(y+1).0` | `minor` |
| `"breaking"` | `x.y.z` -> `x.(y+1).0` (a **minor** bump; Sickos is pre-1.0, see CONTRIBUTING.md) | `breaking_as_minor` |
| absent / `null` | `x.y.z` -> `x.(y+1).0` | `minor_default_absent_category` |
| present but not `patch`/`minor`/`breaking` | `x.y.z` -> `x.(y+1).0` | `minor_default_unrecognised_category` |

For the two default-to-minor cases, the notes file explicitly states that
the category was missing or unrecognised and that minor was used as the
default -- never silently.

## Pin write

The DA pin (`mods/dynamic-atmosphere.pw.toml`) is rewritten directly from
the payload with a small hand-written TOML emitter (`render_da_pin()`) --
never via `packwiz update`, because DA's Modrinth project (`PZV7RorC`) is
not publicly listed and packwiz cannot resolve it through the API. Every
string value is TOML-escaped. `name` and `side` are preserved from the
existing pin; `filename`, `download.url`, `download.hash` (with
`hash-format` fixed at `sha512`), and `update.modrinth.version` come from
the payload; `update.modrinth.mod-id` is always `PZV7RorC`. After writing,
the engine asserts (via `git diff --name-only HEAD -- mods`) that the DA
pin is the *only* file under `mods/` that changed; any other change is
`EXIT_CONFIG_ERROR`.

## Notes file

Written to `docs/releases/<new-pack-version>.md` (a path already excluded
from the packwiz export by `.packwizignore`'s `docs/` entry) and staged
alongside the pin/pack.toml/README changes -- so it survives even if a
later CI story never plumbs it into the GitHub release or Modrinth
changelog. If that path already exists, the engine refuses to overwrite it
(`EXIT_NOTES_EXISTS`) -- a published release's notes are never touched.

Contents: the new Sickos version (and, for `breaking`, a `BREAKING`
heading), the DA version and `modrinth_version_id`, the DA GitHub release
URL, the category as received, and which mapping rule fired. For
`breaking`, a `## Migration` section containing the extracted text
verbatim. The vouch-check interpretation rubric (below) is stated in the
notes before the actual result, and the `listing_review_required` state is
stated along with the exact flagged lines when true.

## Migration extraction (`breaking` only)

DA's release body is a cumulative changelog: `# <version>` (heading level
1) sections, one per historical release. Only a `## Migration` heading
(level 2, case-insensitive, nothing else on the line) found **within this
release's own section** counts -- i.e. after the body's first `# ...`
heading and before the next `# ...` heading. The extracted text is
everything between that `## Migration` heading and the next heading of
level 1 or 2 (or the end of the body), copied byte-for-byte (only the
single blank line immediately adjacent to the heading markers on each side
is trimmed; nothing else is reformatted). If category is `breaking` and no
such section is found, the engine fails closed (`EXIT_MIGRATION_MISSING`)
rather than preparing a breaking release without migration instructions.

Heading detection is **fence-aware**: a line inside a fenced code block
(opened by a line that starts, after up to 3 leading spaces per CommonMark,
with 3+ backticks or 3+ tildes, and closed by a later such line using the
same character with a run at least as long) is never treated as a heading,
no matter what it starts with. Migration instructions routinely include
shell blocks with `#`-prefixed comment lines; without fence-awareness, a
line like `# step 1: back up` inside a ```` ```bash ```` block would be
misread as the next top-level heading and silently truncate the
"verbatim" text mid-fence. An unterminated fence runs to the end of the
body, and nothing inside it is ever treated as a heading boundary.

The release body is fetched from
`GET https://api.github.com/repos/<owner>/<repo>/releases/tags/<tag>`
(note: the API path is `/releases/tags/<tag>`, plural, not the web URL's
`/releases/tag/<tag>`) unless `--release-body <file>` is given.

## Modrinth vouch check

For every run that reaches the bump path (an already-pinned no-op does not
vouch-check, since nothing is being newly pinned), the engine calls
`GET https://api.modrinth.com/v2/version_file/{sha512}?algorithm=sha512`
for both the DA hash being pinned and a control hash: the currently pinned
`mods/create.pw.toml` hash (Modrinth project `LNytGWDc`, Create), read
live from the repo at run time. Create was chosen for the same reason
`docs/version-file-audit.md` (SICKOS-23) chose it: a foundational,
actively-maintained mod, about as unlikely to have been quietly pulled
from Modrinth as any file in this pack, and independently one of that
audit's own cross-checked controls. Re-verified live as part of this work:
`GET /v2/version_file/<create's pinned sha512>?algorithm=sha512` returns
200 today.

Interpretation (stated in code comments in `run_vouch_check()` and in every
run's notes file, before the result):

| Control status | DA status | Result |
| --- | --- | --- |
| 200 | 404 | `not_vouched` -- DA genuinely isn't vouched for by Modrinth. Expected today: `PZV7RorC` is private. |
| 200 | 200 | `vouched` |
| 404 | (any) | `inconclusive_control_failed` -- the query or endpoint itself is wrong; this run says nothing about DA. |
| anything else (429, 5xx, transport error) on either side | | `inconclusive`, with the raw status recorded (never collapsed into 404). |

This check **never** fails the run or changes the exit code. A descriptive
`User-Agent` (`brooswit-minecraft/sickos (SICKOS-75 DA auto-bump engine)`)
is sent on every Modrinth and GitHub API call.

The whole check is wrapped in a catch-all: **any** exception -- a transport
failure `urllib` does not wrap in `URLError` (e.g.
`http.client.RemoteDisconnected`), a missing or unreadable
`mods/create.pw.toml`, or anything else -- is caught and recorded as
`result = "inconclusive"` with `control_status`/`da_status` both `null` and
the raw error text in a new `error` field (see the summary table below),
rather than propagating and aborting an otherwise-successful bump. `error`
is `null` on every non-exceptional outcome, including the three status-code
based ones above.

## Listing and README (`listing_review_required`)

The engine rewrites exactly one thing mechanically: the README sentence
matching `Sickos <pack version> pins Dynamic Atmosphere <DA version> for
client and server.` (matched by regex, not a hardcoded line number; if
zero or more than one such sentence exists, that's `EXIT_CONFIG_ERROR`).

Everything else that *mentions* DA materials or behaviour -- the rest of
README.md's `## Dynamic Atmosphere` section, and any matching line in
`.modrinth/description.md` -- is never rewritten, because it cannot be
derived from the payload and a confidently wrong listing is worse than a
stale one. Instead, on every category **except** `patch`
(`listing_review_required = true`), the engine flags the exact
`{file, line, text}` triples for a human to check, using a keyword scan
(`find_readme_listing_lines()` / `find_description_listing_lines()`) over
README's `## Dynamic Atmosphere` section (excluding the one line it just
auto-rewrote) and over all of `description.md`, for lines containing any
of: `dynamic atmosphere`, `atmospher` (prefix, catches
"atmosphere"/"atmospheric"), `vapor`, `smoke`, `dust`, `exhaust`,
`ender gas`, `violence`, `slime` (case-insensitive substring match). These
two functions are the single, isolated place that decides what gets
rewritten (the mechanical sentence, elsewhere) versus flagged (everything
here) -- so a future machine-readable renames/listing section in DA's own
contract could be slotted in here without reworking the rest of the
engine. No such section exists today and the engine does not wait for one.

`listing_review_required` and its flagged lines are **reporting only**.
They never change the exit code, never hold anything back, and never alter
what gets staged -- the engine prepares the same ready-to-commit tree
either way. The notes file always states plainly, when true, that these
lines were **not** auto-updated and may be stale.

## Summary JSON

Always printed to stdout (and to `--summary-out` if given), on every exit
path including failures:

| Field | Type | Notes |
| --- | --- | --- |
| `changed` | bool | `true` only on a real bump. |
| `outcome` | string | `already_pinned`, `bumped`, or one of the `EngineError` outcome tags (`invalid_payload`, `integrity_failure`, `downgrade_refused`, `anomaly_hash_mismatch`, `anomaly_same_version_different_id`, `gate_failed`, `migration_missing`, `migration_fetch_failed`, `notes_exists`, `config_error`, `dirty_tree`). |
| `previous_pack_version` | string or null | Null on a failure that happened before the pinned state could be read. |
| `new_pack_version` | string or null | Null unless `outcome == "bumped"`. |
| `da_version` | string or null | The payload's `version`, when the payload was readable at all. |
| `modrinth_version_id` | string or null | From the payload. |
| `sha512` | string or null | The sha512 pinned (or attempted). |
| `category_received` | string, null, or other JSON scalar | Exactly as received, before the mapping rule was applied. |
| `mapping_rule` | string or null | See the version-selection table above. |
| `notes_path` | string or null | Repo-relative path of the committed notes file, only when one was written. |
| `vouch` | object or null | `{control_status, da_status, result, error}`, or null if the vouch check never ran (any failure before that step, or the already-pinned no-op path). `error` is a string (the raw exception, `repr()`'d) when the check itself failed and `result` is `inconclusive`; `null` otherwise. `control_status`/`da_status` are also `null` in that case, never guessed. |
| `listing_review_required` | bool | See above. |
| `listing_flagged_lines` | array of `{file, line, text}` | Empty when `listing_review_required` is false. |
| `error` | string or null | The human-readable failure reason, only set on a non-zero exit. |

## CONTRIBUTING.md pointer

See the one-line pointer added to CONTRIBUTING.md's "Release process"
section, next to step 4 (the push-to-main / CI step this engine's output
feeds).
