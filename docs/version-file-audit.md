# version_file audit, 2026-09-10: every pinned mod vs Modrinth's `version_file`, and the state of qIWoLcKJ / N8aGZtvj

SICKOS-23 runs the epic's JOB 1 and JOB 2. It is an investigation: the deliverable
is a measurement and a written record of it, not a change to the modpack.
Nothing under `mods/` changed for this record; see "What did not change,
and how I know" at the end.

On 2026-09-10 the human got an "Unknown files" warning installing sickos
0.2.0 in the Modrinth App and declined to install, saying: "feels sketchy
having to authorize that. Feels like we should be doing things different."
Two files were named as the cause in the report that opened this work:
`NeoForge-projectatmosphere-0.9.1.2.jar` (Modrinth project `qIWoLcKJ`) and
`gaboulibs-neoforge-1.8.7.jar` (project `N8aGZtvj`). That report is treated
here as somebody else's measurement, not fact — everything below is
re-taken from this branch at the commit this task actually worked from.

## Failure conditions (stated before the results)

These say what would mean **my method is wrong**, as distinct from **the
upstream file is genuinely unknown to Modrinth**:

1. The named control (`Create`, project id `LNytGWDc` — see "Control"
   below) returns anything other than HTTP 200 on `version_file`. Create is
   a foundational, actively-maintained mod; a 404 on it means the query
   itself is broken, not that Create was pulled.
2. Every one of the 14 lookups returns 404. A simultaneous removal of the
   entire pinned set is far less likely than a bug in the request (wrong
   host, wrong query param name, wrong `algorithm` value).
3. Any HTTP 429 or 5xx gets recorded as, or collapsed into, a 404. The
   script below records the raw status code and prints a distinct note for
   429 specifically; a 404 is only ever a literal 404 response.
4. A hash sent to the endpoint is empty or is not 128 hex characters (the
   length of a sha512 digest). All 14 index files declare `hash-format =
   "sha512"`; anything else being sent, or a truncated hash, is a parsing
   bug in the script, not an upstream signal. The script prints `hash_len`
   for every row so this is checkable directly in the output.
5. The `algorithm` query parameter does not match the file's own declared
   `hash-format`. This pack only ever declares `sha512` (confirmed by
   reading all 14 `*.pw.toml` files directly), so any mismatch here would
   be the script's bug, not the pack's.
6. A 404 on a hash that can be shown to resolve by another route (e.g. the
   CDN download succeeds and its own sha512 matches the pinned hash, yet
   `version_file` still 404s for that same hash) is not "my method is
   wrong" — it is exactly the JOB 1 finding this ticket is measuring for
   `qIWoLcKJ`/`N8aGZtvj`. See the CDN section below, which is why the two
   questions ("does it download" vs "will Modrinth vouch for it") are kept
   separate throughout this doc.

If none of 1–5 trip, and specific files 404 while the control and the rest
of the pack return 200, that supports "the upstream file is really
unknown" and not "my method is wrong."

## Exact command run

A one-off Python 3 (stdlib only, tested on 3.12) script, not committed to
the repo and not wired into any workflow — run manually from the repo root
with `mods/` present:

```
python3 sickos23_version_file_audit.py
```

The script, verbatim:

```python
#!/usr/bin/env python3
"""
ONE-OFF DIAGNOSTIC for SICKOS-23. Not wired into any workflow. Run manually,
from the repo root, with `mods/` present:

    python3 sickos23_version_file_audit.py

Requires only the Python 3 stdlib (tested on 3.12). Makes only GET requests
to api.modrinth.com and cdn.modrinth.com. Writes zero files outside of what
you redirect stdout to.
"""
import glob
import hashlib
import json
import sys
import time
import tomllib
import urllib.error
import urllib.request

UA = "brooswit-minecraft/sickos (SICKOS-23 version_file audit)"
SLEEP_BETWEEN_CALLS = 1.0
CONTROL_MOD_ID = "LNytGWDc"  # Create -- see docs for why this one was chosen
CDN_CHECK_FILES = {"qIWoLcKJ", "N8aGZtvj"}  # project-atmosphere, gabous-libs


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, body
    except urllib.error.URLError as e:
        return None, str(e).encode()


def load_mod(path):
    with open(path, "rb") as f:
        data = tomllib.load(f)
    dl = data.get("download", {})
    modrinth = data.get("update", {}).get("modrinth", {})
    return {
        "path": path,
        "name": data.get("name"),
        "filename": data.get("filename"),
        "url": dl.get("url"),
        "hash_format": dl.get("hash-format"),
        "hash": dl.get("hash"),
        "mod_id": modrinth.get("mod-id"),
        "version_id": modrinth.get("version"),
    }


def main():
    mod_files = sorted(glob.glob("mods/*.pw.toml"))
    print(f"# Found {len(mod_files)} pinned mod index files under mods/", file=sys.stderr)

    mods = [load_mod(p) for p in mod_files]

    print("\n## JOB 1: version_file lookups\n")
    print(f"{'file':45} {'mod_id':10} {'hash_format':6} {'hash_len':8} {'http':5} {'note'}")
    results = []
    for m in mods:
        h = m["hash"] or ""
        algo = m["hash_format"] or ""
        if not h or not algo:
            results.append({**m, "status": None, "note": "MISSING hash or hash-format in index -- cannot query"})
            print(f"{m['path']:45} {str(m['mod_id']):10} {str(algo):6} {len(h):8} {'N/A':5} MISSING hash/hash-format")
            continue
        url = f"https://api.modrinth.com/v2/version_file/{h}?algorithm={algo}"
        status, body = http_get(url)
        note = ""
        parsed = None
        if status == 200:
            try:
                parsed = json.loads(body)
                note = f"version_id={parsed.get('id')} project_id={parsed.get('project_id')}"
            except Exception as e:
                note = f"200 but body did not parse as JSON: {e}"
        elif status == 404:
            note = "404 (not found)"
        elif status == 429:
            note = "429 RATE LIMITED -- not a 404, do not count as one"
        elif status is None:
            note = f"transport error: {body!r}"
        else:
            note = f"non-200/404 status, body[:200]={body[:200]!r}"
        results.append({**m, "status": status, "note": note, "parsed": parsed})
        print(f"{m['path']:45} {str(m['mod_id']):10} {algo:6} {len(h):8} {str(status):5} {note}")
        time.sleep(SLEEP_BETWEEN_CALLS)

    print("\n## Control check\n")
    control = next((r for r in results if r["mod_id"] == CONTROL_MOD_ID), None)
    if control is None:
        print(f"CONTROL {CONTROL_MOD_ID} NOT FOUND IN mods/ -- test setup is broken")
    else:
        print(f"control = {control['path']} (mod_id={CONTROL_MOD_ID}), status={control['status']}, note={control['note']}")

    print("\n## CDN download check (named files only)\n")
    for m in mods:
        if m["mod_id"] not in CDN_CHECK_FILES:
            continue
        status, body = http_get(m["url"])
        if status == 200:
            got_hash = hashlib.sha512(body).hexdigest()
            match = (got_hash == m["hash"])
            print(f"{m['path']}: HTTP {status}, {len(body)} bytes, sha512 match = {match}")
            if not match:
                print(f"  expected={m['hash']}")
                print(f"  got     ={got_hash}")
        else:
            print(f"{m['path']}: HTTP {status}, download FAILED")
        time.sleep(SLEEP_BETWEEN_CALLS)

    print("\n## JOB 2: upstream project state\n")

    def check(label, url):
        status, body = http_get(url)
        print(f"{label}: HTTP {status}")
        if status == 200:
            try:
                print(json.dumps(json.loads(body), indent=2)[:2000])
            except Exception:
                print(body[:500])
        else:
            print(body[:300])
        time.sleep(SLEEP_BETWEEN_CALLS)
        return status, body

    check("GET /v2/project/qIWoLcKJ", "https://api.modrinth.com/v2/project/qIWoLcKJ")
    check("GET /v2/project/N8aGZtvj", "https://api.modrinth.com/v2/project/N8aGZtvj")
    check("GET /v2/search?query=gaboulibs", "https://api.modrinth.com/v2/search?query=gaboulibs")
    check("GET /v2/search?query=project+atmosphere", "https://api.modrinth.com/v2/search?query=project+atmosphere")
    check("GET /v2/user/Gaboouu/projects", "https://api.modrinth.com/v2/user/Gaboouu/projects")
    check("GET /v2/project/create (control)", "https://api.modrinth.com/v2/project/create")
    check("GET /v2/project/tectonic (control)", "https://api.modrinth.com/v2/project/tectonic")
    check("GET /v2/project/flowing-fluids (control)", "https://api.modrinth.com/v2/project/flowing-fluids")
    check("GET /v2/project/nx3Le5Rv (Project Atmosphere for TFC addon)", "https://api.modrinth.com/v2/project/nx3Le5Rv")
    check("GET /v2/project/nx3Le5Rv/version (addon versions)", "https://api.modrinth.com/v2/project/nx3Le5Rv/version")


if __name__ == "__main__":
    main()
```

Plus three follow-up `curl | jq` one-liners run to get untruncated detail the
script's own 2000-character print cap cut off (full author project list,
full per-version dependency list for the addon, and search `total_hits`) —
same host, same `User-Agent`, GET only:

```
curl -s -H "User-Agent: brooswit-minecraft/sickos (SICKOS-23 version_file audit)" \
  "https://api.modrinth.com/v2/user/Gaboouu/projects" | jq -r '.[] | "\(.id)  \(.slug)  \(.title)"'

curl -s -H "User-Agent: brooswit-minecraft/sickos (SICKOS-23 version_file audit)" \
  "https://api.modrinth.com/v2/project/nx3Le5Rv/version" \
  | jq -r '.[] | "\(.version_number) | status=\(.status) | game=\(.game_versions) | loaders=\(.loaders) | date=\(.date_published) | deps=" + ([.dependencies[] | select(.dependency_type=="required") | .project_id] | join(","))'

curl -s -H "User-Agent: brooswit-minecraft/sickos (SICKOS-23 version_file audit)" \
  "https://api.modrinth.com/v2/search?query=project+atmosphere" | jq -r '.total_hits, (.hits[] | "\(.project_id) \(.slug) \(.author) \(.title)")'
```

All measurements below were taken **2026-09-10, between 19:12:58Z and
19:14:25Z**. Every reading in this doc is time-sensitive; a re-run at a
later date may legitimately differ, especially for the (a)/(b) question in
JOB 2.

## File count

Counted directly at the commit this task worked from: **14** files under
`mods/*.pw.toml`. Not inherited from the report that opened this ticket —
confirmed by `ls mods/*.pw.toml | wc -l` and by the script's own `glob`
count, which agree.

## JOB 1: per-file `version_file` results

Query: `GET https://api.modrinth.com/v2/version_file/<hash>?algorithm=sha512`
for every file's recorded `download.hash`, `User-Agent` set to
`brooswit-minecraft/sickos (SICKOS-23 version_file audit)`, one request per
second.

| file | project id (mod-id) | hash (sha512, truncated) | HTTP | result |
|---|---|---|---|---|
| architectury-api.pw.toml | lhGA9TYQ | d9f7c3bb…027f0 | 200 | version_id=`1IiqEQGl`, project_id=`lhGA9TYQ` (matches) |
| countereds-terrain-slabs.pw.toml | SJu6sklj | 56d52bd9…7b593 | 200 | version_id=`1dYKrrP8`, project_id=`SJu6sklj` (matches) |
| create-aeronautics.pw.toml | oWaK0Q19 | 16ba7a2c…5bbb27 | 200 | version_id=`Vzp221Un`, project_id=`oWaK0Q19` (matches) |
| create.pw.toml **(control)** | LNytGWDc | 11cc8fc0…bafd4b | 200 | version_id=`UjX6dr61`, project_id=`LNytGWDc` (matches) |
| flowing-fluids.pw.toml | s1I3BT95 | c903f5a9…9409c29 | 200 | version_id=`k37oVEnG`, project_id=`s1I3BT95` (matches) |
| **gabous-libs.pw.toml** | **N8aGZtvj** | 0cfde81f…ba33367 | **404** | not found |
| glitchcore.pw.toml | s3dmwKy5 | 7a009ed1…283c32 | 200 | version_id=`S2TfWrZR`, project_id=`s3dmwKy5` (matches) |
| lithostitched.pw.toml | XaDC71GB | f2bdbdd6…72769b3 | 200 | version_id=`81DDKTGJ`, project_id=`XaDC71GB` (matches) |
| peaceful-nights.pw.toml | wusZLXmN | 4856c2c8…1a85b | 200 | version_id=`y93rTym7`, project_id=`wusZLXmN` (matches) |
| power-grid.pw.toml | eWiBLJ9R | d5d7075b…5c13c4 | 200 | version_id=`ip4gJrgx`, project_id=`eWiBLJ9R` (matches) |
| **project-atmosphere.pw.toml** | **qIWoLcKJ** | 02ca75ca…9ca1db9 | **404** | not found |
| sable.pw.toml | T9PomCSv | bf3d8c87…2b4c19a | 200 | version_id=`U678xqle`, project_id=`T9PomCSv` (matches) |
| serene-seasons.pw.toml | e0bNACJD | 8d6c2712…655b8c | 200 | version_id=`SPj5bJoM`, project_id=`e0bNACJD` (matches) |
| tectonic.pw.toml | lWDHr9jE | 7f91f69f…d770ef | 200 | version_id=`vNrkxC3z`, project_id=`lWDHr9jE` (matches) |

"Matches" means the `project_id`/`id` (version) Modrinth returned on the 200
response equals the `mod-id`/`version` this same file's own
`[update.modrinth]` block declares — i.e. the hash really did resolve to
*that* file's own project and version, not merely to some project.

No 429s or 5xx occurred anywhere in this run (checked against the raw
status codes the script printed, not inferred).

**12 of 14 resolve. Exactly 2 do not: `gabous-libs.pw.toml` (N8aGZtvj) and
`project-atmosphere.pw.toml` (qIWoLcKJ).** This matches the split the
opening report described, but it was independently counted here, not
copied from it.

### Control

Named control: **`create.pw.toml`, project id `LNytGWDc`**. Chosen because
(a) Create is the pack's foundational mod — an actively maintained,
extremely widely used project, about as unlikely to have been quietly
pulled from Modrinth as any file in this pack could be — and (b) it is
independently one of the three controls the epic's own JOB 2 baseline
names (`/v2/project/create` → `LNytGWDc`), so the same project id is
cross-checked by two different endpoints in this one doc. It returned
HTTP 200 with `version_id=UjX6dr61, project_id=LNytGWDc`, matching failure
condition 1's requirement for the run to be trusted at all.

## CDN download check (its own question, kept separate from `version_file`)

This checks a different thing from JOB 1: not "will Modrinth vouch for the
hash" but "does the file still download, and does it still hash to what
the pack pins." Per the ticket, this was run only for the two named files:

| file | URL | HTTP | bytes | sha512 matches pinned hash |
|---|---|---|---|---|
| gabous-libs.pw.toml | `cdn.modrinth.com/data/N8aGZtvj/versions/k5aZToKe/gaboulibs-neoforge-1.8.7.jar` | 200 | 71,558 | **yes** |
| project-atmosphere.pw.toml | `cdn.modrinth.com/data/qIWoLcKJ/versions/QBPZU1Dp/NeoForge-projectatmosphere-0.9.1.2.jar` | 200 | 14,254,631 | **yes** |

Both files still download from the CDN, unmodified, exactly as pinned.
Per failure condition 6: these two facts (CDN succeeds, hash matches) next
to the same two files' `version_file` 404s above is not a sign the method
is broken — it is the actual shape of the problem this ticket measures.
The download path and the vouching path are independent at Modrinth, and
this pack is on the wrong side of only the second one.

**Does the pack still install, only behind a warning the player was right
to distrust? Yes — still true**, as of this measurement: both files
download intact from the CDN (so the pack installs), but neither hash
resolves via `version_file` (so Modrinth cannot vouch for them, and the
"Unknown files" dialog the human saw is expected to keep appearing).

## JOB 2: upstream state of qIWoLcKJ and N8aGZtvj

Re-taken 2026-09-10, 19:12:58Z–19:14:25Z, unauthenticated, against
`api.modrinth.com`, compared against the epic's baseline taken
2026-09-10T19:00Z:

| check | baseline (19:00Z) | this reading (19:12–19:14Z) | changed? |
|---|---|---|---|
| `GET /v2/project/qIWoLcKJ` | 404 | 404 | no |
| `GET /v2/project/N8aGZtvj` | 404 | 404 | no |
| `/v2/search?query=gaboulibs` | total_hits 0 | total_hits 0 | no |
| `/v2/search?query=project+atmosphere` | 4 hits, none the mod itself | 4 hits (`beta_atmosphere`, `project-atmosphere-for-tfc`, `pagecraft`, `atmospheric-wind-sway`), none the mod itself | no |
| `GET /v2/user/Gaboouu/projects` | 200, 7 public projects, neither id among them | 200, 7 public projects (`biomes-scanner`, `project-atmosphere-for-tfc`, `legendary-survival-addon`, `serene-seasons-plus`, `beskar`, `dynamic-trees-universal-compat`, `atmospheric-shaders`), neither `qIWoLcKJ` nor `N8aGZtvj` among them | no |
| `GET /v2/project/create` (control) | 200 | 200 | no |
| `GET /v2/project/tectonic` (control) | 200 | 200 | no |
| `GET /v2/project/flowing-fluids` (control) | 200 | 200 | no |

Every baseline reading reproduced exactly. That gap is about **thirteen
minutes** (19:00Z baseline to this 19:12:58Z–19:14:25Z run) — far too short
an interval to be evidence that the state is stable, or that a (b)-style
re-moderation isn't quietly healing underneath it. A reproduction over
thirteen minutes rules out "the report that opened this ticket was already
wrong an hour ago," nothing more; it says nothing about next week.

**The author is still active and still depends on the main project**,
re-checked: the addon `nx3Le5Rv` ("Project Atmosphere for TFC", by
`Gaboouu`) is public, `status: approved`, last `updated: 2026-09-09T21:31Z`
(one day before this reading), and **all 7 of its versions** — not just the
newest — declare `qIWoLcKJ` as a `required` dependency, including the
newest two (`Forge-1.2.0`, published 2026-09-09, and `NeoForge-1.2.0`,
published 2026-08-14, the latter targeting `1.21.1`/`neoforge` same as this
pack). This matches the baseline's finding and, since it was independently
re-checked rather than assumed, extends it slightly: the dependency
declaration is consistent across the addon's entire version history, not
just its latest release.

### (a) vs (b): does the evidence distinguish them?

Two readings survive everything measured above, and **the evidence found
here does not distinguish them**:

- **(a) Withdrawn/deleted/privated, permanently**, by the author's own
  choice.
- **(b) Re-submitted, renamed, or otherwise put back into Modrinth's
  moderation queue, temporarily**, and expected to heal on its own — the
  same state this very project (`sickos`, itself 404ing unauthenticated
  while genuinely `status: processing`) is independently known to be in
  right now.

The one fact that would make an outside reader lean toward (b) over
silence — a live author still actively shipping dependent content — is
present and re-confirmed: `Gaboouu` published a new addon version one day
before this reading, and that version still requires `qIWoLcKJ`. That is
consistent with (b) (why keep shipping an addon whose hard dependency you
just abandoned?) but it is also fully consistent with (a) mid-transition
(an author can privatize a project while an already-published dependent
addon simply keeps its now-stale requirement declaration; Modrinth does
not appear to retroactively edit a shipped version's dependency list).
Nothing gathered here — an unauthenticated 404 on the project itself, a
clean public author project list that omits it, a dependency declaration
elsewhere that cannot see into the private project's own state — can
separate "gone for good" from "gone for now." **This is left standing, on
purpose, as neither confirmed.**

### Successor or fork search (reported only, not adopted)

Checked, per the ticket's suggested (non-obligatory) places: the author's
public project list, the addon's own version/dependency data and body
text, and any source/Discord the addon page names.

- `Gaboouu`'s 7 public projects (listed above) include no project named or
  resembling "Project Atmosphere" or "GabouLibs," and none of them declare
  a 1.21.1 NeoForge build that reads as a rename or successor of either.
  One, `atmospheric-shaders` ("Atmospheric Shaders - NEW LOGO"), shares the
  same "- NEW LOGO"-style title suffix as this pack's two problem files'
  Modrinth titles ("Project Atmosphere: … - NEW LOGO", "Gabou's Libs - …
  LIZZARRDD") — noted only as a stylistic coincidence across this author's
  catalog, not evidence of anything about qIWoLcKJ/N8aGZtvj specifically.
- The addon's body text itself names no source repository and no handle or
  URL — only the instruction "If you need to report something, use
  Discord." But the addon project's own metadata (`GET
  /v2/project/nx3Le5Rv`) does carry a populated `discord_url`
  (`https://discord.gg/2jRhTJgYz4`; `source_url` is `null`). That invite
  was **deliberately not followed** — joining Discords on our behalf is
  outside this ticket's boundary — not "nothing to follow"; the boundary
  was live here, not merely theoretical.
- Modrinth search for "project atmosphere" (4 hits) and "gaboulibs" (0
  hits) surfaced no successor or fork; the only near-name hit,
  `beta_atmosphere` ("Beta Atmosphere - Horror Project" by `Gustavo9093`),
  is an unrelated horror modpack for 1.20.1, not a NeoForge 1.21.1 mod and
  not by this author.

**No public successor or fork was found.** Per the ticket, this is a
finding to report, and nothing was substituted, adopted, or pinned as a
result — see KAN-707 and the hard limits below.

## What did not change, and how I know

- **Nothing on the live Modrinth `sickos` project changed.** No call in
  the script or the follow-up one-liners above targets `sickos`/`RuhnnPqO`
  at all; the only Modrinth calls made are the `GET`s listed, all read-only
  by construction — `http_get()` calls `urllib.request.urlopen(req, ...)`
  with no `data=` argument, which only ever issues a GET, and the
  three follow-up `curl` calls carry no `-X`/`-d` either. No moderation
  state (`status`, `requested_status`) was read or touched for `sickos`,
  `qIWoLcKJ`, or `N8aGZtvj`.
- **No release was cut, no tag created.** No `gh release`/`git tag`
  command was run this session.
- **No mod was added, removed, or re-pinned.** `git diff --stat
  origin/SICKOS-21...HEAD` for this PR touches only this file under
  `docs/`; nothing under `mods/` changed.
- **No gate was added to any workflow.** Nothing under `.github/workflows/`
  was touched. The diagnostic script above lives only in this doc and was
  run from a local scratch location, never committed to the repo and
  referenced by no workflow.
