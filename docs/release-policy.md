# Accepted-change release policy and shared workflow contract

Implements MINECRAFT-39 (implements epic MINECRAFT-12). This document is the
release/publication contract for `brooswit-minecraft/sickos`: which merged
changes actually reach Modrinth, how, and how the pipeline proves it. It is
written to stand on its own in this repository, without depending on any
Jira key remaining readable — a ticket this policy would otherwise have
cited (`SICKOS-1`) was deleted three weeks after being cited as durable
evidence; see "Durable evidence" below for what replaces it.

If this document and the code ever disagree, that is a bug in one of them —
file it, don't guess which is right. Every path, workflow name, exit code,
and command below was read from the live repositories on 2026-09-29 at the
commits this branch was cut from; re-verify before relying on any of it,
per this repo's own convention (see `docs/da-auto-bump.md`).

## What already exists (do not rebuild this)

As of this writing, `sickos` has shipped through `v0.23.2`. `release.yml`
triggers on a pack-path-filtered push to `main` (plus the legacy
`release: published` event and a `workflow_dispatch` dry run) and delegates
to `brooswit-minecraft/schematic`'s `reusable-release.yml@v1`, which:

- reads and semver-validates the version from `pack.toml`,
- refuses to overwrite an existing GitHub Release for that version (P2 gate),
- builds `build/<name>-<version>.mrpack` via this repo's own `make build`,
- creates the GitHub Release (P1 gate) with `target_commitish` identifying
  the source commit,
- publishes to Modrinth when `MODRINTH_TOKEN`/`MODRINTH_PROJECT_ID` are both
  configured (`has_modrinth`), via `rinth publish` (pinned `#v0.8.0` in
  `reusable-release.yml` today),
- exits successfully whether or not Modrinth was configured — a green run
  does not by itself mean anything was published.

`CONTRIBUTING.md` already states the semver policy, the pre-1.0 `0.x`
convention, and that CI (not a human) creates the release. This document
extends that policy; it does not replace it.

**Durable evidence baseline.** `SICKOS-1` investigated and proved one real
Modrinth publication; that ticket no longer exists (404, not migrated). What
it proved survives independently in GitHub and is the baseline this policy's
read-back requirement (Gap 2) builds on: workflow run `33328816753`
("sickos v0.2.0", conclusion `success`, head sha
`7b2681eeee7310110eca1978c8821369177e7f2b`) whose "Publish to Modrinth" step
itself concluded `success`, paired with GitHub release `v0.2.0`
(`target_commitish` = the same sha). Verify both yourself; do not cite a
Jira key for them going forward — that is the entire point of this
paragraph.

## Ownership boundary

Sickos (this document) owns: eligibility, hold representation, the version
source, consumer-side orchestration (concurrency, conflict/retry handling,
read-back verification), and end-to-end evidence. `schematic` owns shared
reusable workflow steps (`reusable-release.yml`, `reusable-server-update.yml`).
`rinth` owns the Modrinth CLI surface (`publish`, `versions latest`,
`servers upstream`). Nothing below asks this repo to fork any of that shared
logic locally; where a requirement needs a change to shared code, it is
named as an assumption on `MINECRAFT-44` (SCHEM) or `MINECRAFT-45` (RINTH)
instead, per "Dependencies" below.

Everything else specified below is achievable entirely within `sickos`'s own
`release.yml` and a small new consumer-side script, wrapping the existing
shared primitives (`ref`, `notes-file`, `rinth publish`, `rinth versions
latest`) rather than waiting on either dependency — called out explicitly
per gap.

---

## 1. Eligibility: release-eligible, build-only, and held changes (Gap 1)

**Release-eligible**: a change merged to `main` through normal review that
touches any path `release.yml` already filters on
(`pack.toml`, `index.toml`, `mods/**`, `config/**`, `defaultconfigs/**`,
`resourcepacks/**`, `shaderpacks/**`) and is not held (below). This is
unchanged from today's trigger filter.

**Build-only**: any push or pull request that does not match the above —
handled entirely by `ci.yml` today (build/validate only, no release
attempted). No change.

**Held** (new — this repo has no durable hold mechanism today; Gap 1 asked
for one): a change that touches a release-eligible path but must not
auto-release, expressed as a **commit-message trailer** on the commit that
lands on `main`, following the exact convention this repo already uses for
`DA-Auto-Bump:` (see `docs/da-auto-bump.md`'s "Trailer format" section — a
`Release-Hold: true` line alone in the commit message's final paragraph,
recognized via git's own trailer parser, not string-matched):

```
Release-Hold: true
```

An optional second trailer line, `Release-Hold-Reason: <text>`, is
recorded verbatim in the outcome (see Section 4) but never parsed or
validated — free text for a human.

**Where this is checked**: a new first job in `release.yml`,
`check-hold`, runs only on the `push` trigger (the `release`-event and
`workflow_dispatch` paths are never held — a human publishing a release
directly, or an explicit dry run, has already made the eligibility decision
that HOLD exists to gate). It reads `${{ github.event.head_commit.message
}}`, parses it with the same trailer-detection function `scripts/
apply-da-release-notes.py`'s `has_da_auto_bump_trailer()` already
implements generically (extend it, or extract a shared `has_trailer(name,
message)` helper — MINECRAFT-42's implementation choice), and outputs
`held: true|false`.

**Unhold**: there is no separate "unhold" action — the hold applies to one
specific commit's message on `main`, which is immutable once pushed.
Releasing a held change later means pushing a **new** commit (a version
bump, a revert-and-reapply, or a fast-forward continuation) without the
trailer. This is deliberate: it means "unhold" can never silently release a
commit whose message a human already reviewed and approved as held — it
always requires a new, reviewable act. Document this explicitly in
`CONTRIBUTING.md` (done below) so a contributor does not go looking for an
`unhold` command that does not exist.

**Downstream gating**: `check-hold`'s `held` output gates the job that
invokes `reusable-release.yml` (`if: needs.check-hold.outputs.held !=
'true'`) entirely — when held, that job, and everything after it, shows as
GitHub Actions **skipped** (distinct from success or failure), which is
also this policy's outcome-state signal for `held` (Section 4). `make
build`/`ci.yml` still run on the same push via the unrelated `ci.yml`
workflow, so a held change's buildability is still continuously verified;
only the release/publish path is withheld.

**Live-state note**: the two PRs the epic named as historically held
(`#28`, `#24`) are both `CLOSED` (not merged, not open) as of 2026-09-29 —
verified via `gh pr view`. Neither is a currently-held change under this new
mechanism; this section defines the mechanism itself, which had no durable
form before this policy regardless of those two PRs' state.

---

## 2. Version source, validation, and concurrent/retried merge resolution (Gaps 4, 5)

Unchanged from `CONTRIBUTING.md` and today's code: `pack.toml`'s `version`
field is the single source of truth, validated as semver-like
(`^[0-9]+\.[0-9]+\.[0-9]+([-.+][A-Za-z0-9.-]+)?$`) by `reusable-release.yml`,
tagged `v<version>` on GitHub, advanced by a contributor as part of the
reviewed PR per `CONTRIBUTING.md`'s bump table. This document adds the two
pieces `CONTRIBUTING.md` does not cover: what happens when two accepted
merges reach `main` close together (overlap), and what happens when the
same accepted merge's release event is replayed or manually retried
(retry).

### Overlap (Gap 5): a `release.yml`-level concurrency group

Add, at the top of `release.yml` (achievable entirely in this repo, no
`schematic` change):

```yaml
concurrency:
  group: sickos-release
  cancel-in-progress: false
```

This serializes every run of this workflow — push-triggered releases, the
legacy `release: published` path, and `workflow_dispatch` dry runs all
share one queue, `cancel-in-progress: false` so a queued run is never
silently dropped (mirroring `da-auto-bump.yml`'s own precedent for the same
tradeoff). Two pack-changing merges landing seconds apart can therefore
never have two `reusable-release.yml` jobs racing the GitHub-Release-create
step or the Modrinth-publish step against each other. The cost is that a
`workflow_dispatch` dry run queues behind an in-flight real release for up
to a few minutes; that is an acceptable latency cost for closing a real
correctness gap, and is worth stating explicitly since it is a deliberate
tradeoff, not an oversight.

Concurrency alone does not stop two merges that were reviewed and approved
with the **same** `pack.toml` version number (a review-process error, not a
workflow bug) from reaching the guard one after another — the second run's
own conflict check (Section 3) is what turns that case into an explicit,
actionable failure instead of a race.

### Retry (Gap 4): a replay must resolve to the SAME outcome, not fail

Today, a retried or replayed release event goes red: `reusable-release.yml`'s
overwrite guard fails the run if the GitHub Release already exists, and
`rinth publish` (confirmed against `rinth`'s current `publish.ts`) throws an
`ApiError` (exit 5) if the Modrinth `version_number` already exists. Both
correctly prevent a **duplicate**, but neither distinguishes that from a
**benign replay of the same already-completed publication**, which is the
distinction Gap 4 asked this policy to make explicit:

**Policy: a retry or replay of a logical version that has already been
correctly published must resolve to the same successful outcome as the
original run — never to `failed`.** A retry that finds a genuine identity
mismatch (same version number, different source) must fail loudly, since
that is a real conflict, not a replay.

This is implemented as consumer-side orchestration in `sickos`'s own
`release.yml`, wrapping existing primitives — it does not require a new
`schematic`/`rinth` capability, though `MINECRAFT-45`'s filed
publish-or-verify-existing primitive (see "Dependencies") would let this
logic move upstream for every consumer at once, and is the better long-term
home for it:

1. **Before** invoking `reusable-release.yml`, a `check-existing` job (see
   Section 3's `git rev-parse HEAD` note on why this must read the
   triggering commit's own resolved sha, not `pack.toml`'s version alone)
   runs `gh release view "v$VERSION" --json targetCommitish,tagName` against
   this repository (verify the exact `gh` JSON field names in your own
   checkout — this document specifies the check, not `gh`'s CLI surface).
   - **No release exists**: proceed normally (this is a first attempt).
   - **Release exists, `targetCommitish` == this run's resolved sha**: this
     is a replay of the exact same source revision. Skip invoking
     `reusable-release.yml` again (it would only fail the overwrite guard);
     proceed straight to the read-back-or-verify step (Section 3) to
     determine whether Modrinth publication also already completed.
   - **Release exists, `targetCommitish` != this run's resolved sha**: a
     real conflict — the version number was reused for different content.
     Fail this job with an actionable message naming both shas and
     instructing the operator to bump `pack.toml`'s version; never call
     `reusable-release.yml`.
2. **If** `reusable-release.yml` (or the skip-and-verify path above) reaches
   the Modrinth publish attempt and `rinth publish` exits `5` (ApiError,
   duplicate `version_number`): this is exactly the "already published"
   shape, not necessarily a conflict. Fall back to `rinth versions latest
   <project> --version-number <version> --json` (the same command
   `reusable-server-update.yml` already uses — pin a version, per that
   file's own precedent for why a tag rather than a branch) and compare the
   returned version's identity (Section 3) against what this run intended
   to publish.
   - **Identity matches**: benign replay — resolve as `published`.
   - **Identity does not match**: a genuine conflict — resolve as `failed`,
     with the exact mismatched fields logged.

Every one of these branches is a pure decision function of
(`existing_target_commitish`, `this_run_sha`, `existing identity`,
`intended identity`) and should be written as testable Python (see the Test
Matrix, Section 6) rather than inline shell, mirroring
`scripts/bump-dynamic-atmosphere.py`'s existing precedent in this repo.

---

## 3. Event flow, concurrency/idempotency keys, and identity (Gaps 2, 3, 5)

```
accepted merge (not held)
        |
        v
 check-hold  --(held)-->  [outcome: held]  (release/publish skipped;
        |                  ci.yml still builds separately)
        v (not held)
 check-existing  --(conflict: same version, different sha)-->  [outcome: failed]
        |
        v (no release yet, or replay of the same sha)
 reusable-release.yml (schematic@v1)
   - build .mrpack, create GitHub Release (target_commitish = source sha)
   - has_modrinth? --(no)--> [outcome: skipped]  (release still created)
        | (yes)
        v
   rinth publish  --(exit 5, duplicate)-->  verify-existing (Section 2.2)
        |                                        |
        v (success)                              v
   [outcome: publishing, transient]      match -> [published] / mismatch -> [failed]
        |
        v
 verify-publication (this repo's new job; Section "Read-back")
   rinth versions latest --version-number <v> --json
   check: version_number, Source-Revision, filename  -->  match: [published]
                                                      -->  no match / timeout: [failed]
```

**Concurrency key**: the `sickos-release` workflow-level group (Section 2)
— coarse (serializes the whole workflow), deliberately not a per-version
key, because a per-version key cannot be computed until after checkout, and
GitHub Actions `concurrency:` must be evaluable before the job body runs.

**Idempotency key**: the pair (`pack.toml` version number, source commit
sha). A GitHub Release is keyed by version (its tag) and independently
verified against sha via `target_commitish` (Section 2.2's conflict check).
A Modrinth version is keyed by `version_number` (rinth's own duplicate
guard) and independently verified against source sha via the
`Source-Revision` changelog trailer below.

### Source-revision and artifact identity (Gap 3)

The GitHub Release already identifies its source revision via
`target_commitish` — no change needed there. The published Modrinth
version does not today: nothing makes the `.mrpack` or the Modrinth version
metadata identify which commit built it.

**Required assumption of `schematic`'s `reusable-release.yml`** (named here
per Gap 3, owned by `MINECRAFT-44` — see Dependencies): the "Write Modrinth
changelog to file" step must unconditionally append a trailer paragraph

```
Source-Revision: <full 40-hex source commit sha>
```

using the value that step's own job already resolves as
`steps.checkout_sha.outputs.sha` (visible in `reusable-release.yml` today,
computed unconditionally, independent of the `ref` input) — regardless of
whether `RELEASE_BODY` or `notes-file` supplied other changelog content, so
the trailer survives on every publish, not only ones that already set a
changelog. This is a minimal, self-contained addition inside a step
`reusable-release.yml` already has; it requires no new `workflow_call`
input or output. Until it lands, `sickos`'s own read-back step (below)
cannot verify source-revision identity against the Modrinth changelog and
must report that check as `not yet available (MINECRAFT-44 pending)` rather
than silently skip it.

The `.mrpack` artifact itself identifying its own source revision (e.g. via
a packwiz override file) was considered and is **not** specified as a hard
requirement: `make build` (this repo's own Makefile) is the only place that
could embed it, and `MINECRAFT-42` should verify packwiz's exact override
semantics in its own checkout before committing to that mechanism — the
GitHub Release `target_commitish` plus the Modrinth changelog trailer
above jointly satisfy "the release and the published version identify
their source revision" without it.

### Read-back (Gap 2): publication success is not "the publish command exited 0"

A new job, `verify-publication`, runs after the publish attempt (whichever
path reached it: the normal publish, or the duplicate-then-verify fallback
in Section 2.2) and is the **only** thing allowed to resolve the run's
outcome to `published`. Modrinth's anonymous project read for this
project (`RuhnnPqO`) returns `HTTP 404` (measured 2026-09-29 by the story
author; re-verify — a project can 404 anonymously either because it is
private or because it is still awaiting Modrinth moderation, so do not read
a 404 alone as proof of misconfiguration), so this lookup **must** use
`MODRINTH_TOKEN`, exactly as `reusable-server-update.yml`'s existing
`rinth versions latest` call already does.

```sh
bunx --bun github:brooswit-minecraft/rinth#v0.9.1 versions latest \
  "$PROJECT_ID" --version-number "$VERSION" --wait 60 --wait-interval 10 --json
```

(pin the same tag `reusable-server-update.yml` already pins at the time
`MINECRAFT-42` implements this, verified live rather than assumed current).
Asserts, from the returned JSON:

- `.version_number == $VERSION` (exact match, not a prefix),
- `.changelog` contains a `Source-Revision: <sha>` trailer equal to this
  run's own resolved sha (once `MINECRAFT-44` lands; otherwise recorded as
  unverifiable, never silently treated as a pass),
- exactly one file is attached whose name matches the expected
  `<pack name>-<version>.mrpack` pattern (this repo's own `Resolve artifact
  name` step already computes the pack name).

All three matching is what "published" means under this policy. Any
mismatch, timeout (`rinth` exit `8`), or unreadable project (exit `4`)
resolves the run to `failed` with the specific mismatched field or `rinth`
exit code named in the failure message — never a silent partial success.

---

## 4. Outcome states (Gap 6)

Six distinct outcomes, matching the epic's own naming exactly:

| Outcome | Meaning | Where it's decided |
| --- | --- | --- |
| `build-only` | Not a release-eligible path (or not a push to `main`); `ci.yml` built/validated it. | Unchanged — `ci.yml`'s existing scope. |
| `held` | Release-eligible, but the landing commit carries `Release-Hold: true`. | `check-hold` job; downstream release job shows `skipped` in the Actions UI. |
| `skipped` | Release created, but Modrinth is not configured (`MODRINTH_TOKEN`/`MODRINTH_PROJECT_ID` missing). | `has_modrinth` check, already present in `reusable-release.yml`, echoed into this repo's own outcome job. |
| `publishing` | Transient: the publish attempt has started but `verify-publication` has not yet resolved. Never a resting state a human should observe after the run completes. | Set at the start of the publish attempt; superseded by `published` or `failed`. |
| `published` | `verify-publication` (Section 3) positively confirmed project, version, filename (and, once `MINECRAFT-44` lands, source revision) on Modrinth. | `verify-publication` job, success path only. |
| `failed` | Build failure, a real version/sha conflict (Section 2.2), a publish error that verify-existing could not reconcile, or a `verify-publication` mismatch/timeout. | Any job's failure path. |

**Machine-readable surface**: a final job, `record-outcome`
(`if: always()`, `needs:` every job above), sets a **GitHub commit status**
on the triggering sha —

```sh
gh api "repos/${GITHUB_REPOSITORY}/statuses/${SHA}" -f state=<success|failure> \
  -f context=sickos/release-outcome -f description="<one of the six outcomes>: <detail>"
```

— rather than relying on the workflow run's own pass/fail badge. This is
the concrete fix for "a green result must never imply publication if it
skipped": a run that hit `skipped` or `held` reports GitHub Actions
`success` (nothing errored) but sets `state=failure`-or-a-distinct
`description` on `sickos/release-outcome` that a human or a consuming
workflow can check independently of the run's own conclusion. Use
`state=success` only for `published`; every other outcome (including
`held`, `skipped`, and `build-only`, which are not errors) sets a state
other than a bare `success` paired with an unqualified description, so
nothing reads as "release complete" by accident. Exact state/description
mapping is `MINECRAFT-42`'s implementation detail; the requirement this
document fixes is that the six outcomes are queryable independent of the
workflow run's own conclusion, in one place, with no ambiguity.

**Actionable missing-configuration text**: reuse `reusable-release.yml`'s
existing `Check Modrinth configuration` step, which already names exactly
which of `MODRINTH_TOKEN`/`MODRINTH_PROJECT_ID` is missing — carry that
exact string into the `skipped` outcome's commit-status description rather
than re-deriving it, and additionally point at this repo's own
`README.md#secrets--variables` table (already documents both names, their
kind, and what consumes them) so the message tells an operator exactly what
to set and where.

---

## 5. Recovery after partial failure and safe operator retry

Recovery is a direct consequence of Sections 2.2 and 3, not new machinery:

- **Build failed**: nothing was created (GitHub Release or Modrinth
  version). Fix the cause, push a new commit (or amend and force-push a PR
  branch pre-merge). No special recovery — this is the ordinary CI-failure
  path `ci.yml` already exercises.
- **GitHub Release created, Modrinth publish never attempted or failed
  before any Modrinth-side write** (e.g. `has_modrinth` was false, or the
  job crashed before the `Publish to Modrinth` step): re-run the **same**
  workflow run (`gh run rerun <run-id> --failed`) or push a no-op
  workflow_dispatch is not appropriate here (dispatch never creates a
  release) — a rerun re-enters `check-existing`, finds the release already
  matches this sha, and (Section 2.2) proceeds straight to the publish
  attempt without trying to recreate the GitHub Release. No duplicate
  GitHub Release is possible: the conflict check in Section 2.2 is what
  prevents it.
- **GitHub Release created, Modrinth publish partially attempted (network
  failure, transient 5xx, or the run was killed mid-upload)**: re-run the
  same run. `rinth publish` either succeeds cleanly (Modrinth never
  actually received the earlier attempt) or fails with the duplicate-version
  `ApiError`, which Section 2.2's verify-existing fallback resolves to
  `published` (confirmed match) or `failed` (confirmed conflict) — never a
  second live upload attempt against an already-correct version. No
  duplicate Modrinth version is possible for the same reason.
- **`verify-publication` itself times out or fails** (Modrinth accepted the
  upload but read-back could not confirm it within the wait budget): this
  is the one case where a human should check Modrinth directly
  (`bunx github:brooswit-minecraft/rinth#v0.9.1 versions latest
  <project> --version-number <version> --json`, manually) before deciding
  whether to re-run — if Modrinth already shows the correct version,
  re-running `verify-publication` alone (not the whole workflow) is
  sufficient and does not touch Modrinth again.

**Authorization wall.** None of the above authorizes an agent to push to
`main`, merge a PR, re-run a workflow, or dispatch a release against this
repository on its own initiative. Every mutating recovery action above
requires the same fresh, explicit human/operator authorization any other
live release does — this document specifies the mechanism, not a standing
permission to use it.

---

## 6. Test matrix (Gap 7)

Split by what is testable today versus what is bounded by the absence of a
sanctioned Modrinth test target (`MINECRAFT-35`, unresolved as of this
writing — read it before promising coverage beyond what's listed here as
"testable today").

**Testable today, no live Modrinth mutation required:**

| Case | How |
| --- | --- |
| Hold-trailer detection (present, absent, malformed, glued to prose with no blank line) | Pure unit test against the trailer-parsing function, mirroring `tests/test_da_auto_bump_dispatch.py`'s style for `DA-Auto-Bump:`. |
| Conflict resolution: same version + same sha vs. same version + different sha vs. no existing release | Pure unit test of the Section 2.2 decision function, fed fabricated `(targetCommitish, sha)` pairs — no network call needed. |
| Identity match/mismatch for the duplicate-then-verify fallback | Pure unit test of the Section 2.2 identity-compare function against fabricated Modrinth JSON responses. |
| Missing-configuration message content | Unit test that the `skipped`-outcome description names the exact missing variable(s), against fabricated env. |
| `release.yml`'s new `concurrency:` block and job wiring | `actionlint`, mirroring `da-auto-bump-tests.yml`'s existing `actionlint` job for its own workflow files. |
| Build failure produces `failed`, never a partial GitHub Release | `workflow_dispatch` dry run against a deliberately broken pack state on a disposable branch — a dry run never creates a Release, so this is safe today. |
| Existing-release conflict check against **real, already-published** history | Run the Section 2.2 check function against `sickos`'s own real `v0.23.2` release (`target_commitish` already known and public) with a fabricated *different* sha — confirms the conflict path fires correctly, without publishing anything. |

**Bounded by `MINECRAFT-35` (no sanctioned Modrinth test target) — evidence
only as far as it goes, named explicitly rather than claimed:**

| Case | What's missing |
| --- | --- |
| A live Modrinth publish actually reaching `verify-publication` and resolving `published` | Requires a real, authorized publish against a real project — either this repo's own production project (requires the Section "Authorization wall" sign-off, and is properly `MINECRAFT-43`'s job, not this story's) or a throwaway project per `MINECRAFT-35`, whichever the operator authorizes first. |
| A genuine overlapping-merge race exercising the `sickos-release` concurrency group under real GitHub Actions scheduling | Requires two real near-simultaneous pushes to a real `main`; the YAML-level guarantee is `actionlint`-checked and manually reasoned about above, but has not been exercised live. |
| The `Source-Revision` changelog trailer round-tripping through a real Modrinth publish | Depends on `MINECRAFT-44` landing in `schematic` first, in addition to a live publish target. |

---

## Dependencies (named, not adopted)

Per the epic's ownership boundary, neither of these is implemented by this
story or by `sickos`'s implementation story (`MINECRAFT-42`):

- **`MINECRAFT-44`** (SCHEM, unowned orphan): this policy's one required
  assumption of `reusable-release.yml` is the `Source-Revision:` changelog
  trailer (Section 3). Everything else in this document (hold, concurrency,
  conflict/retry resolution, read-back, outcome recording) is specified as
  consumer-side orchestration in `sickos`'s own `release.yml` and does
  **not** require `MINECRAFT-44` to land first — `MINECRAFT-42` can
  implement all of it against `reusable-release.yml@v1` as it exists today,
  with the one named exception recorded as "not yet available" until
  `MINECRAFT-44` ships. Folding this consumer-side logic into
  `reusable-release.yml` itself later (so every SCHEM consumer gets it, not
  just `sickos`) is a reasonable evolution of `MINECRAFT-44`'s own filed
  scope ("reusable accepted-merge release orchestration with
  machine-readable outcome states"), but is not required for this policy
  to be implementable now.
- **`MINECRAFT-45`** (RINTH, unowned orphan): a native
  publish-or-verify-existing primitive in `rinth` itself would let Section
  2.2's duplicate-then-verify fallback move out of `sickos`'s own shell
  script and into a single `rinth` command, and is the better long-term
  home for it. Until it exists, `sickos` implements the equivalent logic by
  composing `rinth publish` and `rinth versions latest`, both of which
  already exist and are already used elsewhere in this pipeline
  (`reusable-server-update.yml`).
- **`MINECRAFT-35`** (SCHEM, unowned orphan): bounds the test matrix
  (Section 6) exactly as listed there. Read it before promising coverage
  this document does not claim.

## Repository documentation updated by this change

- `CONTRIBUTING.md`: release process step 4 now points here for hold,
  idempotency, and outcome-state detail, matching the existing pointer
  style used for `docs/da-auto-bump.md`.
- `README.md`: the "Releasing" section links here for the same reason.
