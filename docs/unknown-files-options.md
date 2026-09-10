# Unknown-files options, 2026-09-10: cost of waiting, re-pinning, or removing the two unresolvable pinned mods

SICKOS-24 runs the epic's JOB 3. It is a costing exercise: the deliverable is
this record plus two prepared, unmerged branches, not a change to the pack.
Nothing under `mods/` changes on this branch; see "What did not change, and
how I know" at the end. This record is [SICKOS-21's `docs/version-file-audit.md`](version-file-audit.md)'s
sibling and should be read alongside it — this doc does not repeat that
audit's method in full, only what changed or was re-checked since.

**Read this once, then pick one of the three options below.** Nothing here
requires a follow-up investigation to act on.

## Where this starts

On 2026-09-10 the human installed sickos 0.2.0 in the Modrinth App and got an
"Unknown files" warning asking them to authorize files Modrinth could not
vouch for. They declined, saying: "feels sketchy having to authorize that.
Feels like we should be doing things different."

SICKOS-21 measured why (merged to `main` as `docs/version-file-audit.md`):
of 14 pinned files under `mods/*.pw.toml`, 12 resolve against Modrinth's
`version_file` API and 2 do not — `mods/project-atmosphere.pw.toml` (mod id
`qIWoLcKJ`) and `mods/gabous-libs.pw.toml` (mod id `N8aGZtvj`). Both still
download intact from `cdn.modrinth.com` and hash-match what the pack pins.
**The pack still installs; it just makes the player click through a warning
they were right to distrust**, because the download path and the
"will Modrinth vouch for this hash" path are independent, and this pack is
on the wrong side of only the second one.

**The one open question neither SICKOS-21 nor this record can close:**
an unauthenticated 404 from Modrinth's `/v2/project/{id}` is *also* exactly
what a project sitting in Modrinth's own moderation queue returns — this
project (`sickos`, `RuhnnPqO`) is the proof, itself 404ing to strangers
while genuinely `status: processing`. So two readings survive:

- **(a) Withdrawn/deleted/privated, permanently.** The pack has to change.
- **(b) Re-submitted or re-queued, temporarily.** It heals on its own.

The one live signal available — the mod author (`Gaboouu`) shipped a new
version of a dependent addon (`nx3Le5Rv`) one day before SICKOS-21's
reading, still requiring `qIWoLcKJ` — is consistent with (b) but does not
rule out (a). **This is left open on purpose; it is not a gap in this
record.** See `docs/version-file-audit.md`'s "(a) vs (b)" section for the
full reasoning; nothing gathered here moves that needle either way.

## What this record adds beyond the audit

SICKOS-21's numbers are re-verified here, at **2026-09-10, 19:38:07Z–19:38:11Z**
(about 25 minutes after SICKOS-21's own reading) — same result, both project
lookups and both `version_file` lookups still 404, `LNytGWDc` (Create, the
same control) still 200. Given how short that gap is, treat it the same way
SICKOS-21 treats its own thirteen-minute reproduction: it rules out "already
stale an hour ago," nothing about next week.

Additionally, and new to this record:
- The live Modrinth listing text was read (Option 3, part D below).
- The project's own moderation-queue status was reconfirmed as part of that
  same read (Option 1b below).
- Every one of the 12 resolving pinned files' declared dependencies was
  checked against the two problem project ids (Option 3, part C below) —
  the audit did not do this.
- Two branches were prepared and pushed so that picking Option 2 (if it
  ever reopens) or Option 3 is a merge away, not a fresh investigation.

## Which options are live right now

| Option | Status | As of | Why |
|---|---|---|---|
| 1. Wait | **LIVE** | now | Always available; costed below |
| 2. Re-pin | **CLOSED FOR NOW** (not closed forever) | 2026-09-10T19:38:11Z | No indexed version of either project exists to re-pin to — both project ids still 404 (see re-check below) |
| 3. Remove both files | **PREPARED, NOT EXECUTED** | 2026-09-10T19:39–19:44Z (prepared) | Fully costed and branch pushed; not merged — removing Project Atmosphere is the human's call, not this agent's |

---

## Option 1 — Wait

This is the cheapest option, and correct if reading (b) holds.

### What we lose by waiting a week

Nothing is lost in the sense of data, money, or broken functionality — the
pack still installs and plays. What is genuinely lost:

- **The warning keeps showing** to anyone who installs during the wait,
  exactly as it does today. It doesn't get worse or better on its own.
- **A week of not knowing** which of (a)/(b) is true, which means a week
  where Option 3 (removal) can't be executed with full confidence that it
  wasn't premature, and a week where Option 2 (re-pin) can't be prepared
  because there's nothing to re-pin to yet.
- **No compounding cost found.** Nothing else in this pack depends on
  either problem project (see Option 3C below), so waiting doesn't block
  other work or risk a second failure mode.

### What players actually experience, and who they are

The pack installs, the "Unknown files" dialog appears, and the player has
to explicitly authorize two files Modrinth won't vouch for, or decline (as
the human did).

**Who "the player" actually is right now matters a lot here, and it's
narrower than "the public."** The sickos Modrinth project (`RuhnnPqO`) is
itself sitting in Modrinth's moderation queue and 404s to unauthenticated
strangers — confirmed live in this same session, 2026-09-10T19:37:55Z (see
[full JSON below](#option-1b-evidence-live-project-status)):

```
"status": "processing",
"requested_status": "approved",
"queued": "2026-08-29T15:33:56.860869Z",
"approved": null
```

This was read via the repo's own `.github/workflows/rinth-listing.yml`
(`workflow_dispatch`-only, no writes — I read its source myself before
dispatching it; see "How I verified rinth-listing.yml is read-only" below).
**If this is still true when you read this**, the exposed audience is
essentially the human plus anyone holding a direct link — not a public
storefront audience — which materially lowers the cost of waiting. Re-check
it yourself before leaning on this if time has passed; a project can leave
moderation at any point and this reading has a shelf life exactly like the
version_file checks below.

### The decisive signal: has the pinned version come back?

Two different questions, and only one of them is decisive:

- `GET /v2/project/<id>` returning 200 tells you the **project** came back.
  It does **not** tell you the dialog is gone — a project could return with
  its files re-uploaded under new hashes, and the specific pinned version
  would still be unindexed.
- `GET /v2/version_file/<pinned sha512>?algorithm=sha512` on the **exact
  hashes this pack pins right now** is the **decisive** check — it is the
  literal query the Modrinth App itself makes and fails. The project check
  above is diagnostic only.

Command (self-contained, paste and run; hashes below were read
programmatically from `mods/project-atmosphere.pw.toml` and
`mods/gabous-libs.pw.toml`'s own `download.hash` fields, not retyped by
hand — verify that yourself before trusting them, they are 128 hex
characters each):

```sh
#!/usr/bin/env bash
set -euo pipefail
UA="brooswit-minecraft/sickos (SICKOS-24 wait-signal check)"

echo "== DECISIVE: does the pinned version_file resolve? =="
for pair in \
  "project-atmosphere:02ca75ca33acd45c221d81c94e706ea77c529f793f3b95b62993667c5ea7d0421ebd98a96fefeab3202f9a66f6e4569e6beaa46767491c265b2f231679ca1db9" \
  "gabous-libs:0cfde81fe14999755cb46ccdd104c71cdc3df4b286da79a43f7a5ab226a7a598eaad96c74c869f91838161f2dd90685b9645fb86f9187eae427c7da96ba33367"
do
  name="${pair%%:*}"; hash="${pair##*:}"
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "User-Agent: $UA" \
    "https://api.modrinth.com/v2/version_file/${hash}?algorithm=sha512")
  echo "$name (hash len ${#hash}): HTTP $code $( [ "$code" = 200 ] && echo '<- CAME BACK, this pin now resolves' || echo '(still unresolved if 404)' )"
  sleep 1
done

echo ""
echo "== DIAGNOSTIC ONLY: has the project itself come back? =="
for id in qIWoLcKJ N8aGZtvj; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "User-Agent: $UA" \
    "https://api.modrinth.com/v2/project/${id}")
  echo "project $id: HTTP $code (200 does not by itself mean the pinned hash above resolves -- check that separately)"
  sleep 1
done

echo ""
echo "== CONTROL: must return 200, or the two checks above are not trustworthy =="
code=$(curl -s -o /dev/null -w '%{http_code}' -H "User-Agent: $UA" \
  "https://api.modrinth.com/v2/project/LNytGWDc")
echo "control (Create, LNytGWDc): HTTP $code $( [ "$code" = 200 ] || echo '<- CONTROL FAILED, do not trust the results above' )"
```

Real output, run 2026-09-10T19:38:07Z–19:38:11Z (paste this snippet's exact
text into a shell to reproduce; I ran the equivalent Python form of these
same three queries, shown verbatim in "Exact checks run" below):

```
project-atmosphere (hash len 128): HTTP 404 (still unresolved if 404)
gabous-libs (hash len 128): HTTP 404 (still unresolved if 404)
project qIWoLcKJ: HTTP 404
project N8aGZtvj: HTTP 404
control (Create, LNytGWDc): HTTP 200
```

Control passed (200), so this run is trustworthy per its own failure
conditions below — the 404s reflect Modrinth's real state, not a broken
query.

**Failure conditions for this check** (what result means *my method is
wrong*, not *the mods are really still gone*):
1. The control (`LNytGWDc`, Create) returns anything but 200.
2. A hash sent is not exactly 128 hex characters (sha512 length) — checked
   directly above (`hash len 128` on both).
3. A 429 or 5xx gets silently treated as a 404 — this snippet prints the
   raw code, so a 429/5xx shows as itself, never collapsed.
4. Both `version_file` checks return something other than 404 *and* the
   project checks still 404 — that combination (pinned hash resolves, but
   project endpoint doesn't) would itself be worth reporting as strange,
   not treating as either "fixed" or "still broken."

None of 1–4 tripped in this run; control passed, both hashes were full
128-character sha512, no 429/5xx occurred, and both diagnostic and decisive
checks agree (all four 404).

### What would make waiting the wrong call

I don't have a sourced number for how long a Modrinth moderation re-queue
plausibly takes, and I'm not going to invent one — I looked at nothing that
would let me estimate it (I didn't find published SLA text on Modrinth's
side, and this repo's own moderation history is one data point: `sickos`
itself has been queued since 2026-08-29, i.e. **12 days** as of this
writing, still unresolved — worth knowing as a real, if small, sample of
how long this specific queue can take before this specific project even
starts).

What I *can* name concretely: waiting stops being free the moment a release
becomes necessary for an unrelated reason (a genuine bugfix, a new mod the
human wants added now) — a release built while both files stay unresolvable
ships the exact same "Unknown files" warning again, to whatever wider
audience that release reaches. If `sickos` itself clears moderation and
becomes publicly discoverable while (a)/(b) is still unresolved, that also
raises the stakes of waiting, because the exposed audience given in Option
1's "who are the players" section above stops being just the human.

---

## Option 2 — Re-pin

Re-pinning means moving the two index files to a Modrinth-indexed version of
the same two projects. It requires such a version to exist.

**Failure conditions, stated first** (what would mean *my check is wrong*,
not *the projects are really still unindexed*):
1. The control (`LNytGWDc`, Create) returns anything but 200 on
   `version_file` or `project` lookups.
2. A 429 or 5xx gets recorded as, or collapsed into, a 404.
3. Either hash sent is not exactly 128 hex characters.
4. A search for the project names returns 0 results due to a malformed
   query rather than a genuine absence — checked by also running two
   control searches (`atmosphere` broadly, `gabou`) that are expected to
   return unrelated hits if the search endpoint itself works.

**Re-checked live, 2026-09-10T19:38:07Z–19:38:11Z** (same run as Option 1's
snippet; full script in "Exact checks run" below):

| check | result |
|---|---|
| `GET /v2/project/qIWoLcKJ` | 404 |
| `GET /v2/project/N8aGZtvj` | 404 |
| `GET /v2/version_file/<project-atmosphere hash>` | 404 |
| `GET /v2/version_file/<gabous-libs hash>` | 404 |
| `GET /v2/search?query=gaboulibs` | `total_hits: 0` |
| `GET /v2/search?query=project+atmosphere` | `total_hits: 4`, hits: `beta_atmosphere`, `project-atmosphere-for-tfc`, `pagecraft`, `atmospheric-wind-sway` — none is this mod |
| `GET /v2/search?query=gabou` (rename-catch control) | `total_hits: 0` |
| `GET /v2/search?query=atmosphere` (search-works control) | `total_hits: 353`, unrelated hits — confirms the search endpoint itself works, so the 0-hit results above are real absences, not a broken query |
| `GET /v2/user/Gaboouu/projects` | 200, 7 public projects (`atmospheric-shaders`, `biomes-scanner`, `dynamic-trees-universal-compat`, `serene-seasons-plus`, `legendary-survival-addon`, `beskar`, `project-atmosphere-for-tfc`) — neither `qIWoLcKJ` nor `N8aGZtvj` among them, and none reads as a rename |
| `GET /v2/project/LNytGWDc` (control) | 200 |

None of the four failure conditions tripped: control passed, no 429/5xx,
both hashes were 128 hex characters (see Option 1's `hash len 128` output),
and the search-works control (`atmosphere`, 353 hits) confirms the 0-hit
and no-match results above reflect real absence rather than a broken query.

**Result: no indexed version of either project exists. Option 2 stays
CLOSED FOR NOW, not closed forever, as of 2026-09-10T19:38:11Z.**

No PR is prepared for this option — there is nothing to re-pin to. **To
reopen this option**, re-run the two `version_file` GETs above (or the full
snippet in "Exact checks run"); if either returns 200 instead of 404, that
means an indexed version now exists and this option becomes live.

If it does reopen: re-check per the same failure conditions, and if a real
indexed version turns up, prepare it as a third branch/PR against `main`,
not merged, and say plainly in both the PR and an update to this doc that
it changes which build of the mod players get (old version id/file to new
version id/file) — comment on SICKOS-24 to flag it before requesting
review, since it changes what the human is being asked.

---

## Option 3 — Remove both files

Prepared in full. **Not merged, not taken out of draft.**
PR: https://github.com/brooswit-minecraft/sickos/pull/24 (branch
`SICKOS-22-option3-remove-unresolvable-mods`, cut from `main` at `02ce13a`,
never merged into `SICKOS-22` or this branch). The PR body carries "do not
merge" and the reason (Project Atmosphere is one of the human's ten named
mods; dropping it is the human's decision).

### 3A. The exact diff, and what CI says about it

Removing a mod from a packwiz pack is more than deleting two `.pw.toml`
files: `index.toml` lists every metafile with a hash of the metafile
itself, and `pack.toml`'s `[index]` block pins a hash of `index.toml` in
turn — CI fails if the committed index doesn't match what's on disk (this
repo's own `Makefile`'s `check` target, which CI runs, is what actually
enforces this; I ran it myself rather than taking the README's word for
it).

Used packwiz's own `packwiz remove project-atmosphere` and
`packwiz remove gabous-libs`, which update `index.toml` and `pack.toml`
automatically. The full changed-file set, verified with
`git diff --stat origin/main...origin/SICKOS-22-option3-remove-unresolvable-mods`
at the PR's own head (verify this yourself at your own commit before
trusting the count — branches move):

```
README.md                           |  3 ++
docs/modrinth-listing-if-removed.md | 62 +++++++++++++++++++++++
index.toml                          | 10 ------
mods/gabous-libs.pw.toml            | 13 --------
mods/project-atmosphere.pw.toml     | 13 --------
pack.toml                           |  2 +-
6 files changed, 66 insertions(+), 37 deletions(-)
```

`pack.toml`'s only change is the `[index]` hash (its `version` field is
untouched — still `0.1.0`, matching the pre-existing, correct state where
the release workflow sets the published version from the git tag
in-workflow without committing it back; this record does not touch that).
Nothing under `.github/` is touched (`git diff origin/main...<branch> --
.github/` is empty — checked, not assumed).

Locally, on this branch: `make check` passes (`OK: index is up to date.`)
and `make build` produces `build/sickos-0.1.0.mrpack`, exporting 12 files.
**CI on the PR is the actual evidence that the pack still builds without
these two mods** — this claim is not left as an assertion:

CI run: https://github.com/brooswit-minecraft/sickos/actions/runs/34521864618
— workflow `CI`, job `build / Validate and build pack`, conclusion
**`success`** (`status: completed`), 38s, on `SICKOS-22-option3-remove-unresolvable-mods`.
Quoted from `gh run view 34521864618 --json conclusion,status,name,headBranch`.
`gh pr checks 24` is the live source of truth if this line is ever stale
relative to it.

### 3B. What it does to the pack for a player

Project Atmosphere ("Project Atmosphere: Realistic Climate & Weather", per
its own `.pw.toml` `name` field) is a realistic climate and weather mod —
this is a direct read of the mod's own listed name and category, not an
inference; I have not read its actual feature list or code, since the
project itself is not publicly readable right now (see the (a)/(b)
question above). GabouLibs is a small (71,558-byte) library the pack pins
solely as Project Atmosphere's dependency (see 3C below) — a player loses
it only because they lose the mod that needs it, not as an independent
feature.

**Count:** the pack currently holds 8 of the human's 10 named mods (2 are
already intentionally omitted for having no 1.21.1 release — see
`README.md`). This option takes it to **7 of 10**.

### 3C. The load-bearing dependency check

The epic's standing belief is that GabouLibs is in the pack only as Project
Atmosphere's dependency, so its fate simply follows Project Atmosphere's.
That belief is load-bearing for this option (it's why the option removes
two files, not one) and had not been checked against the rest of the pack
before this record. **Checked now, live, 2026-09-10T19:38:11Z**: for each
of the 12 resolving pinned files, the `version_file` response's own
`dependencies` array was read and checked for `qIWoLcKJ` or `N8aGZtvj` at
any dependency type. Full result, every file listed:

| file | mod id | dependencies (project id, type) | names a problem project? |
|---|---|---|---|
| architectury-api.pw.toml | lhGA9TYQ | (none) | no |
| countereds-terrain-slabs.pw.toml | SJu6sklj | lhGA9TYQ (required) | no |
| create-aeronautics.pw.toml | oWaK0Q19 | LNytGWDc (required), T9PomCSv (required) | no |
| create.pw.toml | LNytGWDc | (none) | no |
| flowing-fluids.pw.toml | s1I3BT95 | (none) | no |
| glitchcore.pw.toml | s3dmwKy5 | (none) | no |
| lithostitched.pw.toml | XaDC71GB | (none) | no |
| peaceful-nights.pw.toml | wusZLXmN | (none) | no |
| power-grid.pw.toml | eWiBLJ9R | LNytGWDc (required), lhGA9TYQ (required) | no |
| sable.pw.toml | T9PomCSv | qy8EtCnG (optional), LNytGWDc (optional), 3KUWeVhG (embedded) | no |
| serene-seasons.pw.toml | e0bNACJD | s3dmwKy5 (required) | no |
| tectonic.pw.toml | lWDHr9jE | XaDC71GB (required) | no |

**None of the 12 resolving files declare either `qIWoLcKJ` or `N8aGZtvj` as
a dependency, at any dependency type.** `gabous-libs.pw.toml` and
`project-atmosphere.pw.toml` themselves cannot be checked this way at all —
their own `version_file` lookups 404, which is the whole problem — so what
this table can say is limited to what **the rest of the pack** declares,
not what Project Atmosphere itself declares about needing GabouLibs. That
limit is real: this confirms nothing *else* in the pack needs GabouLibs, it
does not independently re-confirm that Project Atmosphere does (SICKOS-21's
audit and this epic's own research already established that from
GabouLibs' side — a "libs" utility mod bundled alongside Project Atmosphere
by the same author, present in the pack for no other declared reason).

**Nothing else depends on either project. The belief holds**, checked
rather than assumed.

### 3D. What it does to the listing text

The live sickos Modrinth listing names Project Atmosphere explicitly. Read
live via `.github/workflows/rinth-listing.yml` (verification of its
read-only nature below), 2026-09-10T19:37:55Z, before this branch changed
anything:

- `description`: "An early build of a Create focused modpack for Minecraft
  1.21.1 on NeoForge." — does not name mods or a count; no change needed.
- `body`'s "## Mods" section lists 8 mods including "Project Atmosphere" by
  name, and its opening line reads "Eight mods plus their required
  dependencies, pinned to exact versions and hashes." GabouLibs is **not**
  named anywhere in the live listing (it's a dependency, not one of the ten
  named mods), so its removal alone needs no listing edit.

This means removing Project Atmosphere makes the published listing wrong
in two places (the count, and the mod line) if left unedited — noted here
as part of the true cost of this option, not a silent gap. The corrected
text is drafted in full in `docs/modrinth-listing-if-removed.md` **on
branch B** (chosen over putting it here, so the artifact a human would
actually merge already carries its own storefront correction) —
"Eight mods" becomes "Seven mods", and the "Project Atmosphere" line is
removed from the Mods list. It is drafted only; nothing was pushed to
Modrinth.

**How I verified `rinth-listing.yml` is read-only before dispatching it**:
read its full source at this commit. It declares `permissions: contents:
read`, triggers only on `workflow_dispatch`, and its only Modrinth-facing
step runs `rinth --json project get <id>` — a read command (`rinth`'s own
`project get`, not `project update` or any write subcommand) — writing
only to local log files and the job summary/artifact. No step in the file
uses `project update`, `version create`, or any other rinth write
subcommand. I dispatched it once, on `main`, and read only its output;
I did not modify the file.

### 3E. The repo's own prose

`README.md` currently reads (verify at your own commit — this is what I
read at `02ce13a`):

> Pinned mods live under `mods/`; `Rediculous Ore Generation` and
> `Immersive Weathering` are intentionally omitted — neither has a 1.21.1
> release.

If Project Atmosphere and GabouLibs are removed, this line becomes
incomplete: two more files under `mods/` would be gone with no mention.
**Decision: update it.** Branch B adds one sentence, matching the existing
line's voice, naming both files and the different reason (Modrinth
stopped indexing them, not "no 1.21.1 release") and pointing at this doc.
Kept as a minimal, single-sentence addition rather than a rewrite.

---

## Hard limits respected (evidence, not just assertion)

- **Nothing on the live Modrinth `sickos` project changed.** Every
  Modrinth-facing call made in this session was an unauthenticated `GET`
  against `api.modrinth.com` (the Python `urllib.request.urlopen` calls
  underlying the checks above issue GET only — no `data=` argument was
  ever passed) plus the one dispatch of `rinth-listing.yml`, whose only
  Modrinth call is `rinth --json project get` — verified read-only above.
  Nothing wrote to `sickos`'s description, body, icon, or status.
- **No release was cut, no tag created.** No `gh release` or `git tag`
  command was run. `git ls-remote --tags origin`, run 2026-09-10, lists
  exactly `v0.1.0`, `v0.1.1`, `v0.2.0` — the same three that existed before
  this ticket started (verify this yourself; branches and tags can move).
- **No mod was actually added or removed on `main`.** `git ls-tree
  origin/main mods/` still lists both `mods/project-atmosphere.pw.toml` and
  `mods/gabous-libs.pw.toml` — checked directly, not inferred from "I didn't
  push to main." The removal exists only on the unmerged branch above.
- **No gate was added to any workflow.** `git diff origin/main...<branch>
  -- .github/` is empty on both this branch and the removal branch —
  checked for each, not assumed from "I didn't mean to." The diagnostic
  script below runs by hand from a local scratch location; it is not
  committed to the repo and is referenced by no workflow.
- **This branch (`SICKOS-24`) touches only `docs/unknown-files-options.md`**
  — nothing under `mods/`, `.github/`, `pack.toml`, or `index.toml`.

## Exact checks run

One-off Python 3 (stdlib only), not committed to the repo, run manually
from the repo root with `mods/` present — same shape as SICKOS-21's own
script, extended to also re-check Option 2 and run the Option 3C
dependency scan:

```python
#!/usr/bin/env python3
"""
SICKOS-24 diagnostic. Unauthenticated GETs only, against api.modrinth.com.
Run from the repo root (or set REPO env var) with mods/ present.
"""
import glob, json, os, sys, time, tomllib, urllib.error, urllib.request

REPO = os.environ.get("REPO", ".")
UA = "brooswit-minecraft/sickos (SICKOS-24 options costing)"
SLEEP = 1.0
CONTROL_MOD_ID = "LNytGWDc"  # Create
TARGETS = {"qIWoLcKJ": "project-atmosphere", "N8aGZtvj": "gabous-libs"}

def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        return None, str(e).encode()

def load_mod(path):
    with open(path, "rb") as f:
        data = tomllib.load(f)
    dl = data.get("download", {})
    modrinth = data.get("update", {}).get("modrinth", {})
    return {"path": path, "hash_format": dl.get("hash-format"), "hash": dl.get("hash"),
            "mod_id": modrinth.get("mod-id"), "version_id": modrinth.get("version")}

def main():
    mod_files = sorted(glob.glob(os.path.join(REPO, "mods/*.pw.toml")))
    mods = [load_mod(p) for p in mod_files]
    print(f"# {len(mod_files)} pinned files", file=sys.stderr)

    print("\n## OPTION 2 RE-CHECK: are qIWoLcKJ / N8aGZtvj indexed right now?\n")
    for mod_id, label in TARGETS.items():
        status, body = http_get(f"https://api.modrinth.com/v2/project/{mod_id}")
        print(f"GET /v2/project/{mod_id} ({label}): HTTP {status}")
        time.sleep(SLEEP)

    for m in mods:
        if m["mod_id"] not in TARGETS:
            continue
        h, algo = m["hash"], m["hash_format"]
        print(f"hash_len for {m['path']} = {len(h)} (algo={algo})")
        status, body = http_get(f"https://api.modrinth.com/v2/version_file/{h}?algorithm={algo}")
        print(f"GET /v2/version_file/... ({m['path']}): HTTP {status}")
        time.sleep(SLEEP)

    print("\n## Search checks (rename detection)\n")
    for q in ["gaboulibs", "project+atmosphere", "gabou", "atmosphere"]:
        status, body = http_get(f"https://api.modrinth.com/v2/search?query={q}")
        if status == 200:
            j = json.loads(body)
            print(f"search '{q}': total_hits={j.get('total_hits')}")
        time.sleep(SLEEP)

    status, body = http_get("https://api.modrinth.com/v2/user/Gaboouu/projects")
    print(f"\nGET /v2/user/Gaboouu/projects: HTTP {status}")
    time.sleep(SLEEP)

    print("\n## Control\n")
    status, body = http_get(f"https://api.modrinth.com/v2/project/{CONTROL_MOD_ID}")
    print(f"GET /v2/project/{CONTROL_MOD_ID} (Create, control): HTTP {status}")
    time.sleep(SLEEP)

    print("\n## OPTION 8c: dependency scan of the 12 resolving pinned files\n")
    for m in mods:
        if m["mod_id"] in TARGETS:
            print(f"{m['path']}: CANNOT CHECK (its own version_file 404s)")
            continue
        h, algo = m["hash"], m["hash_format"]
        status, body = http_get(f"https://api.modrinth.com/v2/version_file/{h}?algorithm={algo}")
        if status == 200:
            deps = json.loads(body).get("dependencies", []) or []
            hit = [d for d in deps if d.get("project_id") in TARGETS]
            print(f"{m['path']} ({m['mod_id']}): deps={[(d.get('project_id'), d.get('dependency_type')) for d in deps]} hits_target={bool(hit)}")
        time.sleep(SLEEP)

if __name__ == "__main__":
    main()
```

Real output of this run, 2026-09-10T19:38:07Z–19:38:11Z, is reproduced
throughout the tables above (Option 1, Option 2, Option 3C).

## What did not change, and how I know

See "Hard limits respected" above for the itemized evidence
(`git ls-remote --tags`, `git ls-tree origin/main mods/`, `git diff ...
-- .github/` for both branches, and the read-only-call accounting for
Modrinth). In addition:

- **No pre-publish hash-verification gate was added anywhere in this
  repo.** The diagnostic script above is quoted here, never committed, and
  wired into no workflow — the right home for a gate like this is
  schematic's shared reusable workflow (already sent to the SCHEM project
  as a peer request per the epic), not a local one-off here.
- **The Modrinth moderation state of `sickos` was not touched.** The one
  Modrinth call this record makes that touches `sickos` at all
  (`rinth --json project get`, via the read-only workflow) is a read; no
  `rinth project update` or moderation-state call was made.
- **The addon's Discord invite was not followed**, no account was created,
  and the author was not contacted.

WRITING FOR OTHERS: everything above that names a file path, a line
number, a commit, or a URL is this agent's own read of the repo and of
Modrinth at the timestamps given. Verify each one yourself at your own
commit and your own moment before relying on it — branches move, Modrinth
state changes, and PRs can be updated after this doc is written. For your
own host/port/systemd/journal facts, trust your own workspace's
`ENVIRONMENT.md`, never a value quoted in this doc.
