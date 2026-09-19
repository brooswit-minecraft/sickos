# DA auto-bump release notes plumbing (route R1)

This is a TEMPORARY workflow. Delete it, and this document, in the same
change that adopts R3 (see "Why this is temporary" below).

## The gap this closes

`release.yml`, via `schematic`'s `reusable-release.yml`, creates the GitHub
release for a pack-changing push with `generate_release_notes: true` --
GitHub's own auto-generated "What's Changed" notes, nothing more. The
Modrinth changelog is filled only from a `release` event's body, so it
stays empty on an ordinary push.

Separately, `scripts/bump-dynamic-atmosphere.py` (contract:
`docs/da-auto-bump.md`) commits `docs/releases/<pack version>.md` on a DA
auto-bump: the new version, the DA version pinned, and -- for a `breaking`
category -- a verbatim `## Migration` section extracted from Dynamic
Atmosphere's own release notes.

Before this change, nothing consumed that notes file. The GitHub release
page for an auto-bumped version showed only the generic "What's Changed"
list; a player relying on the release page to see BREAKING/Migration
instructions saw nothing about them.

## What this ships

`.github/workflows/da-auto-bump-notes.yml`, triggered by `workflow_run` on
the "Release" workflow's completion. When all four conditions below hold,
it edits that release's GitHub body to be the notes file's own content,
followed by a marker, followed by GitHub's originally generated notes --
so nothing already on the release page is lost. Logic lives in
`scripts/apply-da-release-notes.py` (unit-tested in
`tests/test_apply_da_release_notes.py`); the workflow YAML only wires
triggers, permissions, and step outputs.

### Gate conditions (all four, checked independently)

1. The "Release" run concluded `success`.
2. The "Release" run's own trigger was `push` (never `release` or
   `workflow_dispatch` -- those paths are unaffected by this workflow).
3. The pushed head commit carries a `DA-Auto-Bump` trailer, detected with
   `git interpret-trailers --parse`, not a text search -- a mention of the
   words in the commit body does not count. **Whoever wrote the trailer**:
   this does not require it came from any particular workflow, and the
   value after the colon is never parsed or validated here. (The
   convention is `DA-Auto-Bump: <da_version> <modrinth_version_id>`, but
   that shape is informational only, for a human or another tool to read.)
4. `docs/releases/<pack version>.md` exists at that same head commit, where
   the pack version is read from `pack.toml` at that commit.

If any condition fails, the run ends quietly (this is the overwhelmingly
common case -- most pushes to `main` are not auto-bumps). If all four
hold but the release itself does not exist (`gh release view v<version>`
fails), the job fails loudly rather than silently skipping or creating one.

### Body composition and idempotency

`compose_release_body()` in `scripts/apply-da-release-notes.py` joins the
notes file's content, an HTML-comment marker
(`<!-- da-auto-bump-notes: GitHub's generated notes are preserved below -->`),
and whatever came before the marker in the *previous* body (or the whole
previous body, if this is the first edit). Because the generated-notes
portion is always read back out from behind the marker rather than
re-appended on top of it, re-running this workflow against an
already-edited release yields a byte-identical body -- it never duplicates
the generated notes. This holds for a release with an empty body too. See
`tests/test_apply_da_release_notes.py::TestComposeReleaseBody` for all
three shapes (fresh, already-edited, empty).

### Constraints honored

- Creates, tags, and publishes nothing -- release.yml already did that.
  This workflow only calls `gh release edit` on a release that must
  already exist.
- Never touches Modrinth.
- Never touches `release.yml`, `server-update.yml`, or anything in the
  `schematic` repo.
- `gh release edit --notes-file` only rewrites the release body; the
  attached `.mrpack` asset is untouched (this is `gh`'s own documented
  behavior for that flag -- not independently re-verified against a live
  release by this change, since nothing here can safely exercise a real
  release before merge; see "What could not be verified before merge").
- Permissions: `contents: read` at the workflow level, `contents: write`
  on the `edit-release-notes` job only -- the same shape `release.yml`
  itself uses for its own job.
- Everything from the triggering event (the commit message, the
  conclusion, the event name) reaches the shell only through `env:`, never
  interpolated into a `run:` body, and the job checks out
  `github.event.workflow_run.head_sha` explicitly rather than the default
  ref.

## What this does NOT fix

The Modrinth changelog stays empty for an auto-bump push. Players who see
the changelog through the launcher's update view, rather than the GitHub
release page, still see nothing about a BREAKING change until R3 lands (or
until R2, a direct Modrinth changelog PATCH, is separately approved -- it
is deferred, not rejected, and needs explicit sign-off before anyone
builds it). If R3 has not landed by the time the next real DA release
needs it, that is a decision for the epic to re-raise, not something this
workflow silently works around.

## Why this is temporary

`schematic`'s `reusable-release.yml` may eventually gain an optional notes
input that fills both the GitHub release body and the Modrinth changelog
from one release path (route R3). Once that lands, this workflow and this
document become redundant and should be deleted in that same change --
not left behind as a second, competing way to get notes onto a release.

## What could not be verified before merge

- A `workflow_run` trigger only arms once the workflow file is on the
  repository's default branch. There is no way to fire this workflow
  end-to-end against a real "Release" run before this PR merges into
  `main` (by way of `SICKOS-73`). The gate logic, the trailer parsing, and
  the body-composition rule are covered by stubbed unit tests instead (no
  network, no real `gh`/`git` state beyond the local, network-free
  `git interpret-trailers` call).
- Whether `gh release edit --notes-file` truly leaves the attached
  `.mrpack` asset alone was checked against `gh`'s documented behavior for
  that flag, not against a live release created by this repo's own
  `release.yml`.
- Whether `github.event.workflow_run.head_commit.message` reliably carries
  the full commit message (including a trailer several lines down) for
  every push shape GitHub can deliver was not exercised against a real
  webhook payload.
