#!/usr/bin/env python3
"""Scheduled reconciliation: bump every enrolled mod that has a newer,
compatible Modrinth release (MINECRAFT-362's generalized trigger).

This is the "scheduled poll backstop" docs/mod-auto-bump.md's design
section described but had not yet wired into a workflow. It is the
companion to `.github/workflows/da-auto-bump.yml`'s dispatch-driven path:
that path reacts to Dynamic Atmosphere's own release event; this script
reacts to nothing and instead asks Modrinth directly, on a schedule, for
every OTHER eligible pin -- so a missed/replaced dispatch for any mod is
eventually recovered, and mods with no dispatch integration at all are
still covered.

Covers Dynamic Atmosphere's own pin too, not only "every OTHER eligible
pin": DA already has its dedicated, reviewed, dispatch-driven workflow
(`.github/workflows/da-auto-bump.yml`), but that workflow and this one
share a GitHub Actions concurrency group (both push to main), and a
cron-triggered run arriving while a DA dispatch run is already queued
(not yet started) REPLACES the queued run in that group -- GitHub Actions
only keeps one run queued behind the currently in-progress one. If that
happens, the DA dispatch is gone with nothing left to recover it unless
this poll also resolves DA's own pin. `decide_update`'s dedup-by-version-id
already makes running both mechanisms against the same pin safe (whichever
runs second simply finds "already_pinned" and no-ops), so there is no
real race to protect against by excluding it -- only a real gap in
recovering a dropped dispatch, which excluding it would reopen
(MINECRAFT-362 review).

This run makes AT MOST ONE real bump commit, then stops -- it does NOT
keep considering the rest of the eligible pins after the first bump
(MINECRAFT-362 review, second round). A prior version of this script
committed every eligible mod's bump in the same run (each its own
commit) and relied on the calling workflow to push each commit
separately so each got its own release.yml run. That does give each
bump its own push event, but release.yml has its OWN concurrency group
(`sickos-release`, see .github/workflows/release.yml) with the same
"a run arriving while one is in-progress and one is pending replaces the
pending one" semantics this module's own docstring already describes for
`mod-auto-bump` -- and a release run takes minutes while these pushes
fire seconds apart, so a multi-bump run reliably outruns its own
releases and drops the middle ones (confirmed against live Modrinth
resolution of all eligible pins: 9 real bumps found on one run would
have published roughly 2 of them, leaving ~7 with a real committed pin
change and a docs/releases/<version>.md on main that release.yml's own
(non-fatal) skipped-version detector would then flag forever). Stopping
after one bump per poll run means at most one release.yml run is ever
triggered by this workflow at a time; the next scheduled run (20 minutes
later) picks up wherever this one left off, so multiple real updates
still converge, just one release per run instead of racing several.

Every pin examined before that (whether this run's own bump, or an
earlier pin that turned out to be a no-op/expected-gap/failure) is still
in the summary -- only pins past the first real bump are left for a
later run, and they are simply absent from this run's summary, not
reported as any kind of failure.

This script never pushes (same convention as
scripts/bump-dynamic-atmosphere.py and scripts/bump_mod.py); the calling
workflow pushes the one commit, if any, after this script returns.

One run's full outcome (one entry per pin examined) is always printed as
JSON to stdout and optionally written to --summary-out, so a failure
partway through is still fully visible, not just the pins that happened
to resolve before it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bump_mod  # noqa: E402 - needs sys.path set up first
import bump_mod_github  # noqa: E402
import github_release_identity  # noqa: E402
import mod_eligibility  # noqa: E402
import modrinth_compat  # noqa: E402

# No pin is excluded (see this module's docstring): dedup-by-version-id
# already makes it safe for this poll and da-auto-bump.yml's dispatch to
# both resolve Dynamic Atmosphere's pin. Empty today, but a path added
# here still gets its own "excluded_pin_path" outcome below (MINECRAFT-
#362 review, fifteenth round) rather than a bare `continue` that drops
# it with no outcome at all -- the latent version of the exact
# silently-dropped-pin shape rounds twelve and thirteen fixed for
# `untracked`/`excluded_local_only`, caught before any path was ever
# actually added here.
EXCLUDED_PIN_PATHS = frozenset()

# Outcomes that are a PERMANENT, per-pin property this poll cannot fix by
# retrying, as opposed to an unexpected failure -- see `_is_expected_gap`
# (below the three KNOWN_*_PIN_IDS dicts) for exactly how each is
# checked:
# - "resolution_failed": modrinth_compat.py's version-list fetch catches
#   bare `Exception`, so this is a catch-all for an HTTP 5xx, a timeout,
#   a DNS failure, a malformed response, an expired/insufficiently-
#   scoped MODRINTH_TOKEN, or a project genuinely deleted/privated.
#   KNOWN_ANONYMOUS_404_MOD_IDS below is currently EMPTY (sixteenth
#   review round: it previously allowlisted 4 mod_ids on the premise
#   that MODRINTH_TOKEN was not configured, which is false -- the
#   token is configured and wired into this workflow), so no mod_id is
#   presently expected for this outcome and a resolution_failed for ANY
#   pin fails the run. Re-populate only with mod_ids for which a live,
#   authenticated check has actually confirmed the configured token
#   cannot read that specific project -- never the whole class, or a
#   Modrinth outage would stop the mechanism while the scheduled job
#   stayed green (MINECRAFT-362 review, third round).
# - "unsupported_version_scheme" / "no_parseable_candidate" /
#   "only_prerelease_available": each established by one specific,
#   deterministic check (the no-downgrade comparator, or
#   `resolve_latest_compatible_version` itself -- see
#   `NoParseableCandidateError`/`OnlyPrereleaseAvailableError` in
#   modrinth_compat.py), so each is checked against its own
#   KNOWN_*_PIN_IDS dict below BY mod_id, not treated as expected for
#   every pin that happens to hit it (MINECRAFT-362 review, eleventh
#   round -- a pin newly landing in one of these classes without being
#   on its list is a coverage regression, not the documented baseline).
# - "breaking_change_requires_human_review" / "downgrade_refused" /
#   "dependency_conflict": each re-established fresh every run from
#   data that can change at any time (a candidate's own declared
#   category, the pack's current pins), so there is no fixed set of
#   mod_ids to check against -- these stay unconditionally expected by
#   outcome string alone (`_UNLISTABLE_EXPECTED_GAP_OUTCOMES`, defined
#   just before `_is_expected_gap`). "dependency_conflict" briefly had
#   its own per-mod-id KNOWN_UNMET_REQUIRED_DEPENDENCY_PIN_IDS list
#   (nineteenth review round, after a confirmed false positive on
#   Cristel Lib), removed again in the twenty-first round once the root
#   cause (modrinth_compat.find_dependency_conflicts comparing a
#   `required` dependency at all) was reverted rather than patched
#   around: `dependency_conflict` can now only mean the one remaining,
#   genuinely unpredictable-in-advance case (`incompatible`-and-pinned),
#   the same shape as `downgrade_refused`.

# Previously 4 projects (Dynamic Atmosphere, Dynamic Skateboards,
# Dynamic Terrain, Dynamic Vehicles) individually confirmed to return
# HTTP 404 to an ANONYMOUS/no-token caller, on the premise that
# MODRINTH_TOKEN was not yet configured. That premise is false
# (MINECRAFT-362 review, sixteenth round): `MODRINTH_TOKEN` IS
# configured as a repository secret and IS wired into both
# mod-auto-bump-poll.yml and da-auto-bump.yml -- this module's own
# fetcher is never actually anonymous in a real run, so "needs
# MODRINTH_TOKEN to resolve" was never an accurate remedy for these 4
# pins' behavior under this poll. What remains genuinely unverified is
# whether the CONFIGURED token has read access to these 4 specific
# projects -- a materially different, narrower claim this allowlist
# cannot make without live evidence it does not have. Emptied rather
# than kept on a false justification, per this comment's own
# long-standing rule: "once a token is set, re-verify this list rather
# than assuming it is still accurate" -- the token is set, so a
# resolution_failed for any of these 4 ids now fails the run loudly and
# honestly instead of warning on a premise that no longer holds. If a
# live, authenticated check later confirms the configured token
# genuinely cannot read one of these projects, re-add it here with that
# evidence (and the accurate remedy: grant/rotate the token's access,
# not "set a token" that already exists).
KNOWN_ANONYMOUS_404_MOD_IDS = frozenset()

# The following three lists are a SNAPSHOT from a live, read-only check
# of all 78 eligible pins (same caveat as KNOWN_ANONYMOUS_404_MOD_IDS
# above): if a human bumps a pin, or its upstream project publishes a
# release, the corresponding entry goes stale and should be updated, not
# trusted as still-accurate indefinitely. They exist (not only in docs)
# purely so main()'s standing reminder notice (see below) can name these
# pins even on a run whose loop never reaches them -- see this module's
# docstring for why one-bump-per-run means most runs never reach most
# pins. MINECRAFT-362 review, tenth round: a single list here previously
# conflated three outcomes with different code paths and different
# remedies -- a live check found the 12 pins it named actually split
# three ways (3 / 5 / 4), and 2 of those (Xaero's Minimap/World Map) had
# already been fixed by the ninth round's resolver normalization and
# should not have been listed at all.

# Keyed by mod_id (not just named in a display string) as of the
# eleventh review round: _is_expected_gap below now reconciles each
# run's OBSERVED gap outcomes against these lists by mod_id, not just
# the outcome string -- a pin that lands in one of these outcome classes
# WITHOUT being on the matching list is a coverage regression (a mod
# changed version scheme, or its author started shipping only
# prereleases), and must fail the run rather than silently blend into
# the documented baseline. Previously these were allowlisted
# unconditionally by outcome string alone, which is also what made the
# aggregate guard below unable to tell a genuine, newly-affected pin
# apart from the always-expected ones.

# 3 pins: a real candidate DOES resolve, but the no-downgrade check
# (bump_mod.run_engine, via decide_update) cannot compare it against
# this pin's own unparsable pinned version -- outcome
# "unsupported_version_scheme". Needs a human bump to a parseable
# version.
KNOWN_NON_SEMVER_PIN_IDS = {
    "z69Pxatv": "Create: Easy Structures", "hl5OLM95": "Geophilic",
    "VpmCsizY": "IceAndFire Community Edition",
}

# 5 pins: modrinth_compat.resolve_latest_compatible_version itself raises
# NoParseableCandidateError -- compatible RELEASE entries exist, but none
# parses as semver even after normalization (some other, undocumented
# version scheme). These 5 pins' UNPARSEABLE strings are the Modrinth
# CANDIDATES themselves, not the pin -- so "a human bump" cannot fix any
# of them (there is nothing parseable to bump to); only a different
# comparator this document does not attempt would (MINECRAFT-362 review,
# eleventh round, smaller item -- a prior round's remedy text was wrong).
KNOWN_NO_PARSEABLE_CANDIDATE_PIN_IDS = {
    "qnQsVE2z": "Quark", "MVARlG2f": "Zeta", "YCPmkFAm": "Create: Dynamic Village",
    "VT2rW2wr": "Create: Pillager Arise", "tpehi7ww": "Dungeons and Taverns",
}

# 4 pins: resolve_latest_compatible_version raises OnlyPrereleaseAvailableError
# -- compatible entries exist, but every one is a beta/alpha, never a
# release, for this (game_version, loader). A human bump cannot fix
# this; only the mod author publishing a release build does.
KNOWN_ONLY_PRERELEASE_PIN_IDS = {
    "HXF82T3G": "Biomes O' Plenty", "s3dmwKy5": "GlitchCore",
    "e0bNACJD": "Serene Seasons", "kkmrDlKT": "TerraBlender",
}

# Previously 4 pins (Dynamic Battle Axes, Dynamic Despawn, Dynamic
# Whips, MagicaVoxel Loader) with no [update.modrinth] table and no
# [update.github] table of ANY shape -- mod_eligibility.
# discover_eligible_pins reported these as `untracked`, with no mod_id
# to key the other four lists by, so this one was keyed by pin_path
# instead (MINECRAFT-362 review, twelfth round). Emptied in the
# twenty-third round (comment 48415): all 4 now carry a real,
# native-schema [update.github] table (see mod_eligibility.py's own
# docstring) and are covered by discover_github_pins/scripts/
# bump_mod_github.py instead -- re-add an entry here only if a pin
# genuinely loses ALL tracking again, with live evidence, same
# discipline as every other KNOWN_* list in this module.
KNOWN_UNTRACKED_PIN_PATHS = {}

# Display-only convenience views, built from the dicts above so there is
# one source of truth for membership AND display text.
KNOWN_NON_SEMVER_PIN_NAMES = tuple(f"{name} ({mod_id})" for mod_id, name in KNOWN_NON_SEMVER_PIN_IDS.items())
KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES = tuple(
    f"{name} ({mod_id})" for mod_id, name in KNOWN_NO_PARSEABLE_CANDIDATE_PIN_IDS.items()
)
KNOWN_ONLY_PRERELEASE_PIN_NAMES = tuple(f"{name} ({mod_id})" for mod_id, name in KNOWN_ONLY_PRERELEASE_PIN_IDS.items())
KNOWN_UNTRACKED_PIN_NAMES = tuple(
    f"{name} ({pin_path})" for pin_path, name in sorted(KNOWN_UNTRACKED_PIN_PATHS.items())
)

# Outcome kinds with no static per-pin list: `modrinth_compat`/`bump_mod`
# establish these fresh, per candidate, every run (a candidate's own
# declared category, or a competing pin's own update, can change at any
# time), so there is no fixed set to reconcile against the way the
# list-based outcomes above have. "excluded_local_only"/
# "excluded_pin_path" are different -- each IS fully determined in
# advance (a fixed, reviewed list of mod NAMES or pin paths), but not by
# anything a mod_id allowlist could express the way the list-based
# outcomes are checked, since neither carries a meaningful mod_id
# (thirteenth/fifteenth rounds). "dependency_conflict" briefly had its
# own per-mod-id list too (nineteenth round, after a confirmed false
# positive on Cristel Lib), removed again in the twenty-first round once
# the root cause was reverted at its source
# (modrinth_compat.find_dependency_conflicts no longer compares a
# `required` dependency at all -- see that module's own docstring) --
# `dependency_conflict` can now only mean the one remaining outcome
# (`incompatible`-and-pinned), which is exactly as unpredictable in
# advance as `downgrade_refused`. All four below stay unconditionally
# expected by outcome string alone.
#
# `dependency_closure_gate_failed` and `runtime_incompatible` both join
# this set in the twenty-second round: run_gate
# (scripts/bump-dynamic-atmosphere.py) now gives the dependency_closure
# gate its OWN outcome string, distinct from refresh/check/build's
# `gate_failed` (still NOT in this set -- those three failing means the
# build pipeline itself is broken, which must fail a poll run loudly,
# not blend in as expected); and check_runtime_compatibility_or_die
# raises `runtime_incompatible` when a candidate's own jar requires a
# platform version (minecraft/neoforge/etc.) newer than what this pack
# runs. Both mean the real data -- a real jar's real manifest -- looked
# at a real candidate and correctly refused it, exactly as unpredictable
# per-candidate as `dependency_conflict`, so both get the same
# unconditional treatment.
_UNLISTABLE_EXPECTED_GAP_OUTCOMES = frozenset({
    "breaking_change_requires_human_review", "downgrade_refused", "dependency_conflict",
    "dependency_closure_gate_failed", "excluded_local_only", "excluded_pin_path",
    "runtime_incompatible",
})


def _is_expected_gap(outcome):
    """True if `outcome` (one entry from poll_and_bump's return list) is
    a documented, permanent, per-pin gap rather than an unexpected
    failure (or a coverage regression). Five outcome kinds are checked
    against their own static KNOWN_*_PIN_IDS/KNOWN_UNTRACKED_PIN_PATHS
    list BY mod_id or pin_path -- a pin hitting one of these outcomes
    while NOT on the matching list is something this poll has never seen
    before for that pin, which must fail the run rather than silently
    blend into "the documented baseline" (MINECRAFT-362 review, eleventh
    and twelfth rounds). "excluded_local_only"/"excluded_pin_path" are
    deliberately NOT one of these -- each is a fixed, reviewed set
    (mod_eligibility.EXCLUDED_MOD_NAMES, or EXCLUDED_PIN_PATHS) that
    carries no meaningful mod_id to reconcile by, so both stay
    unconditionally expected (thirteenth/fifteenth rounds). The
    remaining kinds (including `dependency_conflict` again, as of the
    twenty-first round) have no fixed set to check against at all (see
    _UNLISTABLE_EXPECTED_GAP_OUTCOMES) and stay unconditionally expected
    too."""
    kind = outcome["outcome"]
    mod_id = outcome.get("mod_id")
    if kind == "resolution_failed":
        return mod_id in KNOWN_ANONYMOUS_404_MOD_IDS
    if kind == "unsupported_version_scheme":
        return mod_id in KNOWN_NON_SEMVER_PIN_IDS
    if kind == "no_parseable_candidate":
        return mod_id in KNOWN_NO_PARSEABLE_CANDIDATE_PIN_IDS
    if kind == "only_prerelease_available":
        return mod_id in KNOWN_ONLY_PRERELEASE_PIN_IDS
    if kind == "untracked":
        # No mod_id exists for an untracked pin (that is exactly what
        # "untracked" means) -- reconcile by pin_path instead.
        return outcome.get("pin_path") in KNOWN_UNTRACKED_PIN_PATHS
    return kind in _UNLISTABLE_EXPECTED_GAP_OUTCOMES


def _load_build_generic_commit_message():
    module_path = Path(__file__).resolve().parent / "da-auto-bump-dispatch.py"
    spec = importlib.util.spec_from_file_location("da_auto_bump_dispatch_shared", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["da_auto_bump_dispatch_shared"] = module
    spec.loader.exec_module(module)
    return module.build_generic_commit_message


build_generic_commit_message = _load_build_generic_commit_message()


# Moved to mod_eligibility.py (MINECRAFT-362 review, fourth round) so
# scripts/bump_mod.py's manual CLI can share the exact same compatibility
# target this script uses, rather than a second implementation that could
# drift.
read_pack_targets = mod_eligibility.read_pack_targets


def commit_bump(repo_root, message, paths, run_command=subprocess.run):
    """Commit ONLY `paths` (pack.toml, index.toml, the pin, the notes
    file) -- never a bare `git commit` with no pathspec. A pathspec'd
    `git commit -- <paths>` records a snapshot of exactly those paths'
    current content and leaves anything else already staged untouched
    (still staged, not stolen into this commit and not discarded either).
    This run's own checkout starts from a fresh `actions/checkout` with
    an empty index, so a bare commit happens to be equivalent here today
    -- the explicit pathspec is defense in depth for that invariant ever
    changing, and the exact fix scripts/bump_mod.py's CLI needed for the
    same helper, where a human's working checkout can have unrelated
    staged changes (MINECRAFT-362 review, fifth round)."""
    run_command(
        ["git", "-c", "user.name=sickos-mod-auto-bump[bot]",
         "-c", "user.email=sickos-mod-auto-bump[bot]@users.noreply.github.com",
         "commit", "-F", "-", "--", *paths],
        cwd=str(repo_root), input=message, text=True, check=True,
    )
    result = run_command(["git", "rev-parse", "HEAD"], cwd=str(repo_root),
                          capture_output=True, text=True, check=True)
    return result.stdout.strip()


def poll_and_bump(repo_root, *, token=None, fetcher=None, run_command=subprocess.run):
    """Returns a list of one outcome dict per pin examined, in the order
    mod_eligibility.discover_eligible_pins returns them (alphabetical by
    pin filename -- deterministic across runs), STOPPING as soon as one
    real bump commit is made (see this module's docstring for why: at
    most one bump per run avoids racing release.yml's own concurrency
    group). Pins after that point are simply not in this run's outcome
    list; they are picked up by the next scheduled run."""
    repo_root = Path(repo_root)
    game_version, loader = read_pack_targets(repo_root)
    # Built with `token` (never the bare, unauthenticated default_fetcher)
    # so the version-LIST query -- not only bump_mod.run_engine's later
    # single-version metadata lookup -- can resolve a project Modrinth
    # will not serve anonymously (MINECRAFT-362 review).
    fetcher = fetcher or modrinth_compat.make_fetcher(token=token)

    eligible, untracked = mod_eligibility.discover_eligible_pins(repo_root)
    outcomes = []

    # Reported as outcomes in their own right (MINECRAFT-362 review,
    # twelfth round), not discarded: an untracked pin is the one route
    # that drops coverage without producing ANY gap outcome at all, so a
    # mod silently losing its [update.modrinth] table was previously
    # invisible to every guard in this module. mod_eligibility.py's own
    # docstring already says callers must report these as a gap, never
    # silently skip them -- this was the one caller that still did.
    #
    # discover_eligible_pins files TWO different things into its one
    # `untracked` list (MINECRAFT-362 review, thirteenth round): a pin
    # with no [update.modrinth] table (a real, unintentional coverage
    # gap -- reconciled below by pin_path against
    # KNOWN_UNTRACKED_PIN_PATHS), and a pin named in
    # mod_eligibility.EXCLUDED_MOD_NAMES (Herobrine API, Baritone -- a
    # DELIBERATE, permanent non-enrollment this ticket itself requires,
    # not a gap at all). Round 12's pin_path-based reconciliation treated
    # both the same way, which meant the one case this ticket names by
    # name as a requirement -- excluding a local-only addon -- would fail
    # every scheduled run forever the moment such a pin existed, since no
    # EXCLUDED_MOD_NAMES pin's path can be on a static allowlist in
    # advance. Given its own distinct outcome kind instead, so
    # _is_expected_gap can treat it as unconditionally expected (like
    # downgrade_refused/dependency_conflict) rather than needing a list.
    for pin in untracked:
        is_deliberate_exclusion = pin.reason == "excluded local-only mod"
        outcomes.append({
            "mod_name": pin.name, "mod_id": None, "pin_path": pin.pin_path,
            "outcome": "excluded_local_only" if is_deliberate_exclusion else "untracked",
            "changed": False, "error": pin.reason,
        })

    bumped = False

    for pin in eligible:
        if pin.pin_path in EXCLUDED_PIN_PATHS:
            outcomes.append({
                "mod_name": pin.name, "mod_id": pin.mod_id, "pin_path": pin.pin_path,
                "outcome": "excluded_pin_path", "changed": False,
                "error": "pin_path explicitly excluded via EXCLUDED_PIN_PATHS",
            })
            continue

        outcome = {"mod_name": pin.name, "mod_id": pin.mod_id, "pin_path": pin.pin_path}
        try:
            candidate = modrinth_compat.resolve_latest_compatible_version(
                pin.mod_id, game_version=game_version, loader=loader, fetcher=fetcher,
            )
        # Order matters: both subclasses must be caught before the base
        # CompatResolutionError, which stays the catch-all for anything
        # else (MINECRAFT-362 review, tenth round -- a prior round raised
        # the first of these as the bare base class, which this script
        # then surfaced as "resolution_failed", landing it in the one
        # outcome _is_expected_gap does NOT unconditionally exempt).
        except modrinth_compat.NoParseableCandidateError as error:
            outcome.update(outcome="no_parseable_candidate", changed=False, error=str(error))
            outcomes.append(outcome)
            continue
        except modrinth_compat.OnlyPrereleaseAvailableError as error:
            outcome.update(outcome="only_prerelease_available", changed=False, error=str(error))
            outcomes.append(outcome)
            continue
        except modrinth_compat.CompatResolutionError as error:
            outcome.update(outcome="resolution_failed", changed=False, error=str(error))
            outcomes.append(outcome)
            continue

        dedup_decision = modrinth_compat.decide_update(pin, candidate)
        if dedup_decision in ("no_candidate", "already_pinned"):
            outcome.update(outcome=dedup_decision, changed=False, error=None)
            outcomes.append(outcome)
            continue

        # AC4: this is only a HINT now, not the deciding factor -- real
        # Modrinth changelogs are bullet lists, not the "## Breaking" /
        # "## Features" / "## Fixes" headings this regex looks for, so
        # it matched 0 of 15 real candidates when last measured live.
        # run_engine derives the real category from the semver delta
        # between the currently-pinned and candidate version numbers,
        # and only lets this changelog-derived hint ESCALATE that
        # (never downgrade it) -- see run_engine's own category
        # determination, right after its no-downgrade check
        # (MINECRAFT-362 review, sixth round).
        category = modrinth_compat.derive_category_from_changelog(candidate.changelog)

        try:
            summary, exit_code, message = bump_mod.run_engine(
                repo_root, pin, candidate, category=category, run_command=run_command, token=token,
                game_version=game_version, loader=loader,
            )
        except bump_mod.EngineError as error:
            outcome.update(outcome=error.outcome, changed=False, error=error.message)
            outcomes.append(outcome)
            continue

        if not summary["changed"]:
            outcome.update(outcome=summary["outcome"], changed=False, error=None)
            outcomes.append(outcome)
            continue

        commit_message = build_generic_commit_message(
            mapping_rule=summary["mapping_rule"], new_pack_version=summary["new_pack_version"],
            mod_name=pin.name, mod_id=pin.mod_id,
            modrinth_version_id=summary["candidate_version_id"],
            version_number=summary["candidate_version_number"],
        )
        commit_paths = ("pack.toml", "index.toml", pin.pin_path, summary["notes_path"])
        commit_sha = commit_bump(repo_root, commit_message, commit_paths, run_command=run_command)
        outcome.update(
            outcome="bumped", changed=True, error=None,
            new_pack_version=summary["new_pack_version"],
            mapping_rule=summary["mapping_rule"],
            commit_sha=commit_sha,
            listing_review_required=summary["listing_review_required"],
        )
        outcomes.append(outcome)
        # Stop here (see this module's and poll_and_bump's docstrings):
        # at most one bump commit per run, so at most one release.yml run
        # is ever triggered by this workflow at a time.
        bumped = True
        break

    # GitHub-release-tracked pins (MINECRAFT-362 review, comment 48415):
    # same one-bump-per-run contract as the Modrinth loop above, sharing
    # ONE counter across both loops -- a Modrinth bump this run means
    # this loop is skipped entirely, not just stopped early, so a run
    # never commits twice regardless of which mechanism found the
    # update. discover_github_pins is read AFTER the Modrinth loop
    # decides whether anything already bumped, not before, so a run that
    # bumps a Modrinth pin does the minimum GitHub-side work (none) --
    # mirrors the existing contract rather than adding a new one.
    if not bumped:
        for pin in mod_eligibility.discover_github_pins(repo_root):
            if pin.pin_path in EXCLUDED_PIN_PATHS:
                outcomes.append({
                    "mod_name": pin.name, "mod_id": None, "pin_path": pin.pin_path,
                    "outcome": "excluded_pin_path", "changed": False,
                    "error": "pin_path explicitly excluded via EXCLUDED_PIN_PATHS",
                })
                continue

            outcome = {"mod_name": pin.name, "mod_id": None, "pin_path": pin.pin_path}
            # Preserve this pin's own accepted release channel (MINECRAFT-362
            # review, comment 48456, item 4): determined fresh, per pin, from
            # whatever the CURRENTLY-pinned tag's own `prerelease` flag
            # actually is -- never a global default, so a stable-channel mod
            # is never silently opted into prereleases and an accepted-
            # prerelease mod (this pack's real dynamic-atmosphere and
            # dynamic-population pins) is never silently dropped out of its
            # own channel.
            try:
                include_prerelease = bool(
                    pin.pinned_tag
                    and github_release_identity.fetch_release_by_tag_json(
                        pin.slug, pin.pinned_tag,
                    ).get("prerelease")
                )
            except github_release_identity.GithubIdentityError as error:
                outcome.update(outcome=error.outcome, changed=False, error=error.message)
                outcomes.append(outcome)
                continue
            try:
                candidate = github_release_identity.resolve_latest_matching_release(
                    pin.slug, pin.regex, include_prerelease=include_prerelease,
                )
            except github_release_identity.GithubIdentityError as error:
                outcome.update(outcome=error.outcome, changed=False, error=error.message)
                outcomes.append(outcome)
                continue

            try:
                summary, exit_code, message = bump_mod_github.run_engine(
                    repo_root, pin, candidate, run_command=run_command,
                )
            except bump_mod_github.EngineError as error:
                outcome.update(outcome=error.outcome, changed=False, error=error.message)
                outcomes.append(outcome)
                continue

            if not summary["changed"]:
                outcome.update(outcome=summary["outcome"], changed=False, error=None)
                outcomes.append(outcome)
                continue

            commit_message = bump_mod_github.build_github_commit_message(
                mapping_rule=summary["mapping_rule"], new_pack_version=summary["new_pack_version"],
                mod_name=pin.name, slug=pin.slug, tag=summary["candidate_tag"],
                version_number=summary["candidate_version_number"],
            )
            commit_paths = ("pack.toml", "index.toml", pin.pin_path, summary["notes_path"])
            commit_sha = commit_bump(repo_root, commit_message, commit_paths, run_command=run_command)
            outcome.update(
                outcome="bumped", changed=True, error=None,
                new_pack_version=summary["new_pack_version"],
                mapping_rule=summary["mapping_rule"],
                commit_sha=commit_sha,
                listing_review_required=summary["listing_review_required"],
            )
            outcomes.append(outcome)
            bumped = True
            break

    return outcomes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--summary-out", default=None)
    args = parser.parse_args(argv)

    token = os.environ.pop("MODRINTH_TOKEN", None) or None

    outcomes = poll_and_bump(args.repo_root, token=token)

    summary_text = json.dumps(outcomes, indent=2, sort_keys=True)
    print(summary_text)
    if args.summary_out:
        Path(args.summary_out).write_text(summary_text + "\n", encoding="utf-8")

    for outcome in outcomes:
        if _is_expected_gap(outcome):
            if outcome["outcome"] == "excluded_local_only":
                # Deliberate, permanent non-enrollment (MINECRAFT-362
                # requires excluding Herobrine API/Baritone), not a
                # coverage gap -- "not covered this run" would describe
                # a REQUIRED exclusion as a problem (thirteenth review
                # round, smaller item: the one wording this round
                # existed to fix that hadn't been fixed here yet).
                print(
                    f"::warning::{outcome['mod_name']} ({outcome['pin_path']}) deliberately "
                    f"excluded from auto-update (see EXCLUDED_MOD_NAMES in mod_eligibility.py), "
                    f"not a coverage gap: error={outcome.get('error')}"
                )
            elif outcome["outcome"] == "excluded_pin_path":
                # Same reasoning as excluded_local_only above: a path on
                # EXCLUDED_PIN_PATHS is deliberately excluded by this
                # module itself, not a coverage gap (fifteenth review
                # round).
                print(
                    f"::warning::{outcome['mod_name']} ({outcome['pin_path']}) deliberately "
                    f"excluded from this poll (see EXCLUDED_PIN_PATHS in this module), not a "
                    f"coverage gap: error={outcome.get('error')}"
                )
            else:
                print(
                    f"::warning::{outcome['mod_name']} ({outcome['pin_path']}) not covered this run: "
                    f"outcome={outcome['outcome']!r}, error={outcome.get('error')}"
                )

    # Standing reminder, independent of what this run's loop actually
    # reached: one-bump-per-run (see poll_and_bump's docstring) means most
    # runs stop long before reaching most pins, so the per-outcome
    # warnings above are intermittent precisely on the runs that work --
    # the exact runs where staying silent about a known gap is most
    # likely to be mistaken for "nothing to report" (MINECRAFT-362
    # review, third round, smaller item).
    # Built from clauses that skip themselves when their list is empty
    # (MINECRAFT-362 review, sixteenth round) -- KNOWN_ANONYMOUS_404_MOD_IDS
    # emptied this round, and a hardcoded "0 pins need MODRINTH_TOKEN to
    # resolve anonymously ()" printed on literally every run would have
    # been a new, self-contradicting standing claim on the one line this
    # notice always prints.
    _notice_clauses = []
    if KNOWN_ANONYMOUS_404_MOD_IDS:
        _notice_clauses.append(
            f"{len(KNOWN_ANONYMOUS_404_MOD_IDS)} pins need MODRINTH_TOKEN read access to "
            f"resolve ({', '.join(sorted(KNOWN_ANONYMOUS_404_MOD_IDS))})"
        )
    if KNOWN_NON_SEMVER_PIN_NAMES:
        _notice_clauses.append(
            f"{len(KNOWN_NON_SEMVER_PIN_NAMES)} pins have a non-semver PINNED version and need "
            f"a human bump ({', '.join(KNOWN_NON_SEMVER_PIN_NAMES)})"
        )
    if KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES:
        _notice_clauses.append(
            f"{len(KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES)} pins have NO parseable version on "
            f"Modrinth at all (the unparseable strings are the project's own published "
            f"versions, not this pin -- a human bump cannot fix these; only a different "
            f"comparator this document does not attempt would) "
            f"({', '.join(KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES)})"
        )
    if KNOWN_ONLY_PRERELEASE_PIN_NAMES:
        _notice_clauses.append(
            f"{len(KNOWN_ONLY_PRERELEASE_PIN_NAMES)} pins have never published a release build "
            f"for this target and need the mod author to publish one, not a human bump "
            f"({', '.join(KNOWN_ONLY_PRERELEASE_PIN_NAMES)})"
        )
    if KNOWN_UNTRACKED_PIN_NAMES:
        _notice_clauses.append(
            f"{len(KNOWN_UNTRACKED_PIN_NAMES)} pins have no [update.modrinth] table at all and "
            f"need a GitHub-release-based identity check this mechanism does not implement, so "
            f"only a human bump covers them today ({', '.join(KNOWN_UNTRACKED_PIN_NAMES)})"
        )
    print(
        "::notice::Known, standing coverage gaps (not re-evaluated this run; see "
        "docs/mod-auto-bump.md's Status section): " + "; ".join(_notice_clauses) + "."
    )

    failed = [o for o in outcomes if o["outcome"] not in
              ("no_candidate", "already_pinned", "bumped") and not _is_expected_gap(o)]

    # Name the specific pin(s) that turned this run red, not only the
    # KNOWN gaps above (MINECRAFT-362 review, twelfth round, smaller
    # item): the per-outcome ::warning:: loop above only ever fires for
    # EXPECTED gaps, so a run with one known gap and one genuine,
    # unlisted regression previously annotated the known gap and said
    # nothing at all about the pin that actually failed it.
    for outcome in failed:
        print(
            f"::error::{outcome['mod_name']} ({outcome['pin_path']}) failed this run: "
            f"outcome={outcome['outcome']!r}, error={outcome.get('error')}"
        )

    # Aggregate guard (MINECRAFT-362 review, third round, broadened
    # eleventh round): the ORIGINAL guard only caught "every pin was
    # resolution_failed." Driving main() over all 78 real pins with an
    # injected fetcher found FOUR other ways to go silently, permanently
    # green: every entry's version_type reading something other than
    # "release" (-> 78x only_prerelease_available), every version
    # missing its `files` array or declaring a renamed game_versions
    # field (-> 78x no_candidate, the sharp case: zero warnings printed,
    # the standing notice unchanged, a summary that reads like an
    # ordinary quiet day), and every compatible release unparseable (->
    # 78x no_parseable_candidate). None of those are "every pin
    # resolution_failed," so the narrower guard missed all four.
    #
    # The real invariant a healthy run has -- stated positively, not as
    # a list of failure shapes to keep extending -- is that AT LEAST ONE
    # pin reaches "already_pinned" or "bumped": with 78 eligible pins
    # and the overwhelming majority already at their resolved latest at
    # any given time, zero of either happening at once is not a
    # legitimate "nothing to do" run, it is the signature of something
    # systemic breaking resolution for every pin uniformly. "no_candidate"
    # deliberately does NOT count as healthy here either -- it is
    # exactly the outcome the missing-`files`/renamed-field cases above
    # produce, so treating it as evidence of health would let the
    # sharpest failure shape back in by name.
    #
    # Deliberately NOT gated on `outcomes` being non-empty (MINECRAFT-362
    # review, twelfth round): a prior version of this guard read
    # `if outcomes and not any(...)`, so an empty outcomes list -- mods/
    # missing or renamed (discover_eligible_pins globs it and raises
    # nothing on a missing directory), or every pin's enrollment key
    # shape changing under a packwiz format change (confirmed live: a
    # tree with mods/mod-id renamed to mod_id enrolls 0 of what should be
    # 78 pins) -- short-circuited the guard entirely and exited 0 with no
    # ::error::, no ::warning::, and `[]` in the step summary: strictly
    # worse than the four shapes this guard already catches, since those
    # at least leave every pin's row in the summary. Examining nothing at
    # all is never a legitimate "nothing to do" run.
    aggregate_guard_failed = False
    if not outcomes:
        print("::error::zero pins were examined this run (mods/ missing or renamed, every pin "
              "excluded, or discover_eligible_pins returning nothing for some other reason) -- "
              "refusing to report this as a healthy, nothing-to-do run.")
        aggregate_guard_failed = True
    elif not any(o["outcome"] in ("already_pinned", "bumped") for o in outcomes):
        print("::error::no pin in this run reached already_pinned or bumped -- this looks like "
              "a systemic problem (Modrinth outage, response-shape change, a bad/expired token, "
              "or an API shape change breaking resolution for every pin uniformly), not the "
              "documented per-pin coverage gap. Failing regardless of any per-pin allowlist.")
        failed = outcomes
        aggregate_guard_failed = True

    return 1 if failed or aggregate_guard_failed else 0


if __name__ == "__main__":
    sys.exit(main())
