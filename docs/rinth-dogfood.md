# rinth dogfood: v0.2.0, the first sickos release published through rinth

sickos v0.1.0 and v0.1.1 were published to Modrinth by `Kir-Antipov/mc-publish`, a
third-party GitHub Action. SCHEM-6 replaced that with `rinth publish` inside
`schematic`'s `reusable-release.yml`. **v0.2.0 is the first sickos version published
by `rinth` instead of `mc-publish`**, cut through a real GitHub Release rather than a
synthetic `workflow_dispatch` harness — this proves the actual release path, which is
what epic SICKOS-1's acceptance criterion ("dogfood rinth end to end on a real modpack
release") requires, and what SCHEM-6 also needed to close.

## Gate check (re-run immediately before cutting the release)

```
$ gh api repos/brooswit-minecraft/schematic/contents/.github/workflows/reusable-release.yml?ref=v1 \
    -H "Accept: application/vnd.github.raw" | grep -n "mc-publish\|rinth publish"
102:            echo "MODRINTH_TOKEN/MODRINTH_PROJECT_ID not set — skipping Modrinth publish (missing: ${missing})"
182:      - name: Dry-run Modrinth publish
```

Note both matches are the substring `rinth publish` inside the word **Mod**rinth,
not the `mc-publish` Action or a literal `rinth publish` CLI invocation — this grep
pattern is a weak gate on its own. Reading the full file at tag `v1`
(`558d7b0b4fb55c27256d7110257b9edeef6a99a8`) confirms directly: `mc-publish` /
`Kir-Antipov` do not appear anywhere, and the actual publish invocations are at
line 198 (dry-run, `if: ... && github.event_name != 'release'`) and line 229 (real
publish, `if: ... && github.event_name == 'release'`), both
`bunx --bun github:brooswit-minecraft/rinth#v0.8.0 publish`. Loaders are read
one-per-line from `pack.toml` into a bash array and passed as repeated `--loader`
flags — not a comma-joined string.

## Sequence and run log

1. **Dry run** — `gh workflow run release.yml -R brooswit-minecraft/sickos -f version=0.2.0`
   Run: https://github.com/brooswit-minecraft/sickos/actions/runs/33328722012 (success)
   The "Dry-run Modrinth publish" step printed the exact payload `rinth --dry-run` would
   send, before any network call:
   ```
   {
     "data": {
       "project_id": "RuhnnPqO",
       "version_number": "0.2.0",
       "name": "0.2.0",
       "changelog": "",
       "game_versions": ["1.21.1"],
       "loaders": ["neoforge"],
       "version_type": "release",
       "featured": false,
       "dependencies": [],
       "file_parts": ["sickos-0.2.0.mrpack"],
       "primary_file": "sickos-0.2.0.mrpack"
     },
     "file": { "part": "sickos-0.2.0.mrpack", "size": 2655 }
   }
   ```
   Matches intent exactly: project RuhnnPqO, version 0.2.0, channel release (implied by
   `version_type`), game version 1.21.1, loader neoforge — as JSON arrays, not
   comma-joined. "Publish to Modrinth" correctly did not run (`event_name != 'release'`).

2. **Pre-check** (on `main`) — `gh workflow run rinth-dogfood.yml -R brooswit-minecraft/sickos --ref main`
   Run: https://github.com/brooswit-minecraft/sickos/actions/runs/33328775592 (success)
   `versions list` showed only `0.1.1` and `0.1.0` — `0.2.0` absent, duplicate guard not
   a concern.

3. **Real publish** — GitHub Release `v0.2.0`, tag `v0.2.0`, target `main`
   (`7b2681eeee7310110eca1978c8821369177e7f2b`):
   https://github.com/brooswit-minecraft/sickos/releases/tag/v0.2.0
   Release run: https://github.com/brooswit-minecraft/sickos/actions/runs/33328816753 (success)
   "Publish to Modrinth" step output:
   ```
   Qt4z9dHa  https://modrinth.com/project/sickos/version/Qt4z9dHa
   ```
   Created version id **`Qt4z9dHa`**, version_number **`0.2.0`**.

4. **Read-back** (on `main`, same harness revision as the pre-check —
   `rinth-dogfood.yml` as it exists on `main` after SICKOS-8's job was merged, commit
   `7b2681e`) —
   Run: https://github.com/brooswit-minecraft/sickos/actions/runs/33328876810 (success)

   `rinth versions list RuhnnPqO` (rinth v0.8.0), three rows side by side:

   | id | version_number | channel | loaders | game versions | date | primary file |
   |---|---|---|---|---|---|---|
   | `Qt4z9dHa` | **0.2.0** | release | neoforge | 1.21.1 | 2026-08-30T18:42:42.316218Z | sickos-0.2.0.mrpack |
   | `aud76zIt` | 0.1.1 (control, mc-publish) | release | neoforge | 1.21.1 | 2026-08-30T16:23:54.454374Z | sickos-0.1.1.mrpack |
   | `N1BQpQTu` | 0.1.0 (control, mc-publish) | release | neoforge | 1.21.1 | 2026-08-29T01:23:57.659283Z | sickos-0.1.0.mrpack |

   **Verdict: identical shape across all three rows.** `loaders` is the bare value
   `neoforge` and `game versions` is the bare value `1.21.1` on the new row exactly as
   on the two mc-publish control rows — proper single/repeated values, never a
   comma-joined string like `neoforge,1.21.1` or `fabric,forge`. The predicted failure
   mode (SCHEM-6's stated worry) did not occur.

   As a secondary, non-required data point, the same run's `rinth-dogfood.yml` "servers"
   job (rinth **v0.9.0**, `versions latest --json`) resolved the correct version
   (`Qt4z9dHa`, `fell_back=0`) on the first call — consistent with RINTH-1's note that
   the `--version-number` exact-match fix landed in v0.9.0. This run did **not** rely on
   that path for its verdict; the v0.8.0 `versions list` table above is the actual
   evidence, per the known v0.8.0 `versions latest` trap.

## Server update (expected failure, not this story's)

The chained "Server update" workflow run
(https://github.com/brooswit-minecraft/sickos/actions/runs/33328854920) failed as
predicted, at the same wall recorded on SICKOS-4:

```
{"error":{"code":4,"status":404,"endpoint":"POST /modrinth/v0/servers/{id}/reinstall",
"message":"servers upstream failed: HTTP 404 from the v0 `POST /modrinth/v0/servers/{id}/reinstall`
route. This route is dead at the router, independent of credentials, server, or project ...",
"reason":"servers_upstream_route_dead"}}
```

`reusable-server-update.yml` is untouched at `@v1` and still calls rinth's dead v0
`servers upstream` route. This is SICKOS-4's known, already-recorded wall — not a
failure of this release or this publish.

## Findings

- **No divergence found in the release path.** rinth v0.8.0's `publish` (and
  `--dry-run`) behaved exactly as `reusable-release.yml`'s comments and README describe:
  repeated `--loader` flags arrived as a JSON array, never comma-joined; the duplicate
  guard was never tested here since 0.2.0 was unused, but the dry-run/read-back round
  trip confirms the payload construction is correct end to end, not just structurally.
- **Gate-check grep caveat.** The literal grep command given for the pre-flight gate
  check (`mc-publish\|rinth publish`) matches the substring `rinth publish` inside the
  word "Mod**rinth publish**" (a step name / log message), not necessarily an actual
  `rinth publish` CLI invocation or the absence of `mc-publish`. Anyone re-running this
  check should also confirm by reading the file directly, as done above, rather than
  trusting the grep alone.
- **v0.9.0 `versions latest` exact-match fix confirmed live.** Not a divergence, a
  confirmation: RINTH-1's flagged fix (the `--version-number` filter) resolved the
  newly-published version correctly on the first call in this run, with `fell_back=0`.
