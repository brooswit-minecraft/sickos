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
import mod_eligibility  # noqa: E402
import modrinth_compat  # noqa: E402

# No pin is excluded (see this module's docstring): dedup-by-version-id
# already makes it safe for this poll and da-auto-bump.yml's dispatch to
# both resolve Dynamic Atmosphere's pin.
EXCLUDED_PIN_PATHS = frozenset()

# Outcomes that are a PERMANENT, per-pin property this poll cannot fix by
# retrying, as opposed to an unexpected failure -- see `_is_expected_gap`
# (below the three KNOWN_*_PIN_IDS dicts) for exactly how each is
# checked:
# - "resolution_failed": modrinth_compat.py's version-list fetch catches
#   bare `Exception`, so this is a catch-all for an HTTP 5xx, a timeout,
#   a DNS failure, a malformed response, an expired MODRINTH_TOKEN, or a
#   project genuinely deleted/privated. Only the mod_ids in
#   KNOWN_ANONYMOUS_404_MOD_IDS below are expected for this outcome --
#   never the whole class, or a Modrinth outage would stop the
#   mechanism while the scheduled job stayed green (MINECRAFT-362
#   review, third round).
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
#   category, the pack's current pins, another pin's own update), so
#   there is no fixed set of mod_ids to check against -- these stay
#   unconditionally expected by outcome string alone
#   (`_UNLISTABLE_EXPECTED_GAP_OUTCOMES`, defined just before
#   `_is_expected_gap`).

# The 4 projects individually confirmed (by a live, read-only, authorized
# resolution against all 78 eligible pins, done under this ticket's
# review process) to return HTTP 404 to an anonymous/no-token caller, as
# of this writing. MODRINTH_TOKEN is not set (see docs/mod-auto-bump.md's
# "Status" -- still held), so these are presently permanent, but this is
# an explicit allowlist, not a blanket exemption for the whole
# "resolution_failed" outcome class: once a token is set, re-verify this
# list rather than assuming it is still accurate, and remove any entry
# that resolves.
KNOWN_ANONYMOUS_404_MOD_IDS = frozenset({
    "PZV7RorC",  # Dynamic Atmosphere
    "n0hFQ5AT",  # Dynamic Skateboards
    "BCtj6RgM",  # Dynamic Terrain
    "srLLPzF8",  # Dynamic Vehicles
})

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

# Display-only convenience views, built from the dicts above so there is
# one source of truth for membership AND display text.
KNOWN_NON_SEMVER_PIN_NAMES = tuple(f"{name} ({mod_id})" for mod_id, name in KNOWN_NON_SEMVER_PIN_IDS.items())
KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES = tuple(
    f"{name} ({mod_id})" for mod_id, name in KNOWN_NO_PARSEABLE_CANDIDATE_PIN_IDS.items()
)
KNOWN_ONLY_PRERELEASE_PIN_NAMES = tuple(f"{name} ({mod_id})" for mod_id, name in KNOWN_ONLY_PRERELEASE_PIN_IDS.items())

# Outcome kinds with no static per-pin list: `modrinth_compat`/`bump_mod`
# establish these fresh, per candidate, every run (a dependency
# declaration or a competing pin can change at any time), so there is no
# fixed set to reconcile against the way the four list-based outcomes
# above have -- these stay unconditionally expected by outcome string
# alone.
_UNLISTABLE_EXPECTED_GAP_OUTCOMES = frozenset({
    "breaking_change_requires_human_review", "downgrade_refused", "dependency_conflict",
})


def _is_expected_gap(outcome):
    """True if `outcome` (one entry from poll_and_bump's return list) is
    a documented, permanent, per-pin gap rather than an unexpected
    failure (or a coverage regression). Four outcome kinds are checked
    against their own static KNOWN_*_PIN_IDS list BY mod_id -- a pin
    hitting one of these outcomes while NOT on the matching list is
    something this poll has never seen before for that pin, which must
    fail the run rather than silently blend into "the documented
    baseline" (MINECRAFT-362 review, eleventh round). The remaining
    kinds have no fixed set to check against (see
    _UNLISTABLE_EXPECTED_GAP_OUTCOMES) and stay unconditionally
    expected."""
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

    eligible, _untracked = mod_eligibility.discover_eligible_pins(repo_root)
    outcomes = []

    for pin in eligible:
        if pin.pin_path in EXCLUDED_PIN_PATHS:
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
    print(
        "::notice::Known, standing coverage gaps (not re-evaluated this run; see "
        "docs/mod-auto-bump.md's Status section): "
        f"{len(KNOWN_ANONYMOUS_404_MOD_IDS)} pins need MODRINTH_TOKEN to resolve anonymously "
        f"({', '.join(sorted(KNOWN_ANONYMOUS_404_MOD_IDS))}); "
        f"{len(KNOWN_NON_SEMVER_PIN_NAMES)} pins have a non-semver PINNED version and need a "
        f"human bump ({', '.join(KNOWN_NON_SEMVER_PIN_NAMES)}); "
        f"{len(KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES)} pins have NO parseable version on "
        f"Modrinth at all (the unparseable strings are the project's own published versions, "
        f"not this pin -- a human bump cannot fix these; only a different comparator this "
        f"document does not attempt would) "
        f"({', '.join(KNOWN_NO_PARSEABLE_CANDIDATE_PIN_NAMES)}); "
        f"{len(KNOWN_ONLY_PRERELEASE_PIN_NAMES)} pins have never published a release build for "
        f"this target and need the mod author to publish one, not a human bump "
        f"({', '.join(KNOWN_ONLY_PRERELEASE_PIN_NAMES)})."
    )

    failed = [o for o in outcomes if o["outcome"] not in
              ("no_candidate", "already_pinned", "bumped") and not _is_expected_gap(o)]

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
    if outcomes and not any(o["outcome"] in ("already_pinned", "bumped") for o in outcomes):
        print("::error::no pin in this run reached already_pinned or bumped -- this looks like "
              "a systemic problem (Modrinth outage, response-shape change, a bad/expired token, "
              "or an API shape change breaking resolution for every pin uniformly), not the "
              "documented per-pin coverage gap. Failing regardless of any per-pin allowlist.")
        failed = outcomes

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
