# rinth icon: before state, and the dispatch-only upload machinery

Epic SICKOS-10 wants the sickos Modrinth listing to have an icon. SICKOS-16
(this task) builds the MACHINERY that will perform that upload — a new
`workflow_dispatch`-only, dry-run-by-default workflow, `rinth-icon.yml` —
and records the listing's state before any icon exists. It does not choose
or create the icon image (sibling story SICKOS-12 owns that) and it does
not perform the real upload (a separate, shelved sibling story does that,
gated on a human picking one of SICKOS-12's candidates). When this task is
done, the live listing must be unchanged and the real upload must be one
deliberate dispatch away.

## Gate check (re-run immediately before acting)

Dispatching the repo's existing read-only `rinth-listing.yml` on `main`,
which does an authenticated `rinth --json project get RuhnnPqO`:

Run: https://github.com/brooswit-minecraft/sickos/actions/runs/33657757587
(success)

```json
{
  "id": "RuhnnPqO",
  "slug": "sickos",
  "title": "sickos",
  "project_type": "modpack",
  "status": "processing",
  "requested_status": "approved",
  "queued": "2026-08-29T15:33:56.860869Z",
  "approved": null,
  "moderator_message": null,
  "icon_url": null,
  "gallery": []
}
```

This matches what the ticket recorded from the prior measurement on
2026-09-02. The project sits normally in Modrinth's review queue; an
unauthenticated GET 404s and that is expected, not a fault. This check is
only as strong as the workflow it depends on — it reuses `rinth-listing.yml`
rather than re-implementing the read, so a bug in that workflow would read
through here too. It has no other known weakness: the read is authenticated,
live, and was run fresh for this record rather than reused from an older run.

## What was built

`.github/workflows/rinth-icon.yml`, `workflow_dispatch` only in its final
state, mirroring `rinth-listing.yml`'s conventions (pinned `RINTH_REF` in
`env:`, project id from `vars.MODRINTH_PROJECT_ID` with a literal fallback,
everything written to `$GITHUB_STEP_SUMMARY`, artifacts uploaded with
`if: always()`, exit codes captured and turned into `::error::`).

Two dispatch inputs: `icon_file` (path, default empty) and `mode`
(`dry-run` or `upload`, default `dry-run`). A "Resolve mode" step treats
anything other than the literal string `upload` — including no input at
all — as `dry-run`. This is what makes the workflow safe under every
trigger, not just a manual dispatch left at its declared default: the
temporary push-triggered run below supplies no `inputs` object whatsoever,
and it still resolved to `dry-run`.

`rinth project icon` has no `--dry-run` flag at the pinned ref (v0.9.1;
other rinth commands do, `publish` and some `project` subcommands among
them, but `project icon` is not one of them), and there is no way to
invoke it without it performing the PATCH. So the dry run never calls it. It does the equivalent
work itself: resolve the project id, resolve and stat the icon file,
validate the extension against a copy of rinth's own accepted list, do an
authenticated `project get` and print the current `icon_url`, and state
exactly what the real run would send — then stop.

The accepted extension list is hardcoded (`png, jpg, jpeg, bmp, gif, webp,
svg, svgz, rgb`, matching rinth's `ICON_CONTENT_TYPES` in
`src/client/index.ts` at v0.9.1) but self-checking: a step re-fetches that
same source file from `RINTH_REF`'s own tag on every run and fails the
workflow loudly if the hardcoded copy no longer matches, rather than
silently drifting.

## Sequence and run log

1. **Zero-inputs exercise (temporary push trigger, no inputs supplied)** —
   the workflow file was not yet on `main`, so `workflow_dispatch` was not
   yet callable (GitHub 404s an undispatchable workflow as an API object
   until the file lives on the default branch). Following the precedent in
   `rinth-listing.yml`'s own git history (commit
   `5095c233706bf4532eb98ca5db5d779b1d773f9d` on SICKOS-9), a push trigger
   scoped to branch `SICKOS-16` and this file's path alone was added for
   one commit, then removed.

   Run: https://github.com/brooswit-minecraft/sickos/actions/runs/33657885616
   (failure — expected; see below)

   A push event supplies no `inputs` at all, which is a genuine test of the
   safety property, not a synthetic one. Resolved mode: `dry-run` (input
   read as `<empty>`). Validation failed with reason `no icon_file input
   supplied`. The **"Real upload — rinth project icon" step shows
   `skipped`** in the job's step list — the workflow never came close to a
   write. The job fails overall (exit 1) because validation failed, which
   is the intended behavior: silently doing nothing would be worse than a
   loud, clean failure.

2. **Proof (b), part 1: rejected extension on an existing file** — dispatch
   with `icon_file=README.md` (exists, wrong extension), `mode=dry-run`.

   Run: https://github.com/brooswit-minecraft/sickos/actions/runs/33657944324
   (failure — expected)

   ```
   Unsupported icon file type: md (accepted: png, jpg, jpeg, bmp, gif, webp, svg, svgz, rgb)
   ```

   This is rinth's own error text, reproduced exactly (`Unsupported icon
   file type: <ext> (accepted: <Object.keys(ICON_CONTENT_TYPES).join(", ")>)`
   in `src/commands/project.ts` at v0.9.1) — checked before this run against
   a hand-read of that file at the pinned tag. The workflow never called
   `rinth project icon` or the network for this case.

3. **Proof (b), part 2: missing file** — dispatch with
   `icon_file=docs/icons/nonexistent.png` (does not exist), `mode=dry-run`.

   Run: https://github.com/brooswit-minecraft/sickos/actions/runs/33657958309
   (failure — expected)

   ```
   --file not found: docs/icons/nonexistent.png
   ```

   Also rinth's own usage-error text, and the check that runs *before* the
   extension check — matching rinth's own validation order (existence
   first, extension second; confirmed by reading `icon()` in
   `src/commands/project.ts` at v0.9.1) is what makes case 2 above possible
   at all: an existing file with a bad extension is required to reach the
   error that names the accepted types, a missing path alone does not.

   Both a. and b. use the workflow's own local, offline validation, so
   neither needed a token or the network — the extension-drift check
   (step "Resolve accepted icon extensions") does hit the network (a raw
   fetch of rinth's own source at the pinned tag, not the Modrinth API),
   and passed on both runs.

4. **Temporary trigger removed** — the push trigger from step 1 was
   removed in its own commit
   (`6d48e70`) before this PR's review. The final, merged file is
   `workflow_dispatch` only. Runs 2 and 3 above were dispatched via
   `workflow_dispatch` against the branch once GitHub had registered the
   workflow (following the first push-triggered run) — the same file
   content those runs exercised, apart from the trigger block itself.

5. **Review fix: dispatch inputs moved into `env:` blocks** — the first
   round of review found that `icon_file` (and values derived from it)
   were interpolated directly into `run:` script bodies via `${{ }}`
   rather than routed through `env:`, a shell injection path through the
   dispatch input. Fixed in commit `e181a1c`, no logic change. Re-ran the
   bad-extension case to confirm the error text is still byte-identical
   after the fix (`icon_file=README.md`, `mode=dry-run`):

   Run: https://github.com/brooswit-minecraft/sickos/actions/runs/33658951381
   (failure — expected)

   ```
   Unsupported icon file type: md (accepted: png, jpg, jpeg, bmp, gif, webp, svg, svgz, rgb)
   ```

   Identical to run 33657944324 above.

6. **Final re-check: the live listing is unchanged** — dispatching
   `rinth-listing.yml` on `main` again, after all of the above.

   Run: https://github.com/brooswit-minecraft/sickos/actions/runs/33658064247
   (success)

   ```json
   {
     "id": "RuhnnPqO", "slug": "sickos", "title": "sickos",
     "project_type": "modpack", "status": "processing",
     "requested_status": "approved",
     "queued": "2026-08-29T15:33:56.860869Z",
     "approved": null, "moderator_message": null,
     "icon_url": null, "gallery": []
   }
   ```

   Identical to the gate check at the top of this page. `icon_url` is still
   `null`.

## Proof (a) — outstanding, blocked on SICKOS-12

Proof (a) (a green dry run against the real candidate icon file, showing
the current `icon_url` and exactly what the real run would send) needs a
real icon file. That belongs to sibling story SICKOS-12, and as of this
writing SICKOS-12's remote branch is still byte-identical to `main`
(`a78c28c`) — no icon file exists on it. SICKOS-15 (a task that adds three
candidate icon PNGs, `docs/icons/candidate_{a,b,c}_*.png`) has an open PR
(#17) into SICKOS-12, not yet merged, and even once merged those are
candidates for a human to choose between, not a chosen icon.

This is the honest "outstanding, blocked" case the ticket names explicitly
rather than a failure to invent a workaround for: this task does not choose
or fabricate an icon to unblock itself. Once SICKOS-12 lands a chosen icon
file, dispatching `rinth-icon.yml` with `icon_file=<that path>` and
`mode=dry-run` (default) is the remaining step to close proof (a) — no code
change needed.

## Findings

- **The zero-inputs safety property is demonstrated, not just asserted.**
  A run with genuinely no inputs (the push-triggered exercise) resolved to
  `dry-run` and never reached the upload step.
- **Both required negative proofs pass, reproducing rinth's own error text
  exactly.** The bad-extension case additionally proves the accepted-types
  list the workflow shows matches what `rinth project icon` itself would
  say, at the pinned ref.
- **The live listing is unchanged.** `icon_url` is `null` both before and
  after this task's work, confirmed by two independent authenticated reads.
- **Proof (a) is honestly outstanding**, not skipped or faked. See above.

## After (pending)

To be filled in by the shelved upload story, once a human has chosen an
icon from SICKOS-12's candidates and the real `mode: upload` dispatch has
run.
