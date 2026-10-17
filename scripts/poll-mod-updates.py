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
# retrying, as opposed to an unexpected failure:
# - "unsupported_version_scheme": the no-downgrade check cannot compare
#   the pinned version's own version_number because it is not valid
#   semver -- established per-pin, by this exact comparison, every time.
# - "breaking_change_requires_human_review": a real candidate exists but
#   this generic engine refuses to auto-publish a breaking change with no
#   migration notes (see bump_mod.run_engine's allow_breaking gate) --
#   also established per-pin, every time, by the same gate.
# "resolution_failed" is deliberately NOT unconditionally in this set
# (MINECRAFT-362 review, third round): modrinth_compat.py's version-list
# fetch catches bare `Exception`, so "resolution_failed" is a catch-all
# for an HTTP 5xx, a timeout, a DNS failure, a malformed response, an
# expired MODRINTH_TOKEN, or a project genuinely deleted/privated -- NONE
# of which are the documented "4 projects Modrinth won't serve
# anonymously" gap, and treating the whole class as expected would let a
# Modrinth outage (or any of the above) stop the mechanism entirely while
# the scheduled job stays green every 20 minutes. Only the 4 SPECIFIC,
# individually-confirmed project ids below get the "expected" treatment
# for that outcome; see `_is_expected_gap` and the aggregate guard in
# `main()` for the rest of the fix.
EXPECTED_GAP_OUTCOMES = frozenset({"unsupported_version_scheme", "breaking_change_requires_human_review"})

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

# The 6 pins individually confirmed (same live check) to have a
# currently-pinned version_number that is not valid semver, so
# bump_mod.run_engine's no-downgrade comparator can never evaluate them --
# they always land on "unsupported_version_scheme" regardless of what
# resolves. Named here (not only in docs) purely so main()'s standing
# reminder notice (see below) can name them even on a run whose loop
# never reaches them -- see this module's docstring for why one-bump-
# per-run means most runs never reach most pins.
KNOWN_NON_SEMVER_PIN_NAMES = (
    "Create: Easy Structures (z69Pxatv)", "Cristel Lib (cl223EMc)", "Geophilic (hl5OLM95)",
    "IceAndFire Community Edition (VpmCsizY)", "Quark (qnQsVE2z)", "Zeta (MVARlG2f)",
)


def _is_expected_gap(outcome):
    """True if `outcome` (one entry from poll_and_bump's return list) is
    a documented, permanent, per-pin gap rather than an unexpected
    failure. `resolution_failed` only counts when the pin's mod_id is on
    the individually-confirmed allowlist above -- NOT for the outcome
    class as a whole (MINECRAFT-362 review, third round)."""
    kind = outcome["outcome"]
    if kind in EXPECTED_GAP_OUTCOMES:
        return True
    if kind == "resolution_failed":
        return outcome.get("mod_id") in KNOWN_ANONYMOUS_404_MOD_IDS
    return False


def _load_build_generic_commit_message():
    module_path = Path(__file__).resolve().parent / "da-auto-bump-dispatch.py"
    spec = importlib.util.spec_from_file_location("da_auto_bump_dispatch_shared", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["da_auto_bump_dispatch_shared"] = module
    spec.loader.exec_module(module)
    return module.build_generic_commit_message


build_generic_commit_message = _load_build_generic_commit_message()


def read_pack_targets(repo_root):
    """The pack's own pinned (game_version, loader) -- read once, used as
    the compatibility filter for every mod considered this run.
    pack.toml's [versions] table keys the loader's own name ("neoforge")
    to its version, alongside "minecraft" -- the loader is just "whichever
    key isn't minecraft", not a convention this needs to hardcode.

    Fails loudly, not silently, if pack.toml's [versions] table does not
    have this expected shape: a silent (None, None) here would make every
    mod's compatibility check compare against `None`, which never equals
    anything, so the poll would quietly resolve nothing all run while
    reporting a clean "no_candidate" for every pin -- indistinguishable
    from a genuinely quiet run (MINECRAFT-362 review)."""
    pack_toml = Path(repo_root) / "pack.toml"
    import tomllib
    with pack_toml.open("rb") as handle:
        data = tomllib.load(handle)
    versions = data.get("versions", {})
    game_version = versions.get("minecraft")
    loader_names = [key for key in versions if key != "minecraft"]
    if not game_version or len(loader_names) != 1:
        raise ValueError(
            "pack.toml's [versions] table is not the expected shape (exactly 'minecraft' plus "
            f"one loader key); got {sorted(versions)!r}. Refusing to poll with an unverifiable "
            "compatibility target."
        )
    return game_version, loader_names[0]


def commit_bump(repo_root, message, run_command=subprocess.run):
    run_command(
        ["git", "-c", "user.name=sickos-mod-auto-bump[bot]",
         "-c", "user.email=sickos-mod-auto-bump[bot]@users.noreply.github.com",
         "commit", "-F", "-"],
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
        except modrinth_compat.CompatResolutionError as error:
            outcome.update(outcome="resolution_failed", changed=False, error=str(error))
            outcomes.append(outcome)
            continue

        dedup_decision = modrinth_compat.decide_update(pin, candidate)
        if dedup_decision in ("no_candidate", "already_pinned"):
            outcome.update(outcome=dedup_decision, changed=False, error=None)
            outcomes.append(outcome)
            continue

        # AC4: derive the pack-version category from the candidate's own
        # Modrinth changelog (fix -> patch, feature -> minor, breaking ->
        # minor-during-0.x) -- there is no trigger payload here to supply
        # one, unlike DA's dispatch path. None (unrecognised/absent) falls
        # through to bump_mod.run_engine's own documented default-minor
        # behaviour (MINECRAFT-362 review).
        category = modrinth_compat.derive_category_from_changelog(candidate.changelog)

        try:
            summary, exit_code, message = bump_mod.run_engine(
                repo_root, pin, candidate, category=category, run_command=run_command, token=token,
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
        commit_sha = commit_bump(repo_root, commit_message, run_command=run_command)
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
        f"{len(KNOWN_NON_SEMVER_PIN_NAMES)} pins have a non-semver pinned version and need a "
        f"human bump ({', '.join(KNOWN_NON_SEMVER_PIN_NAMES)})."
    )

    failed = [o for o in outcomes if o["outcome"] not in
              ("no_candidate", "already_pinned", "bumped") and not _is_expected_gap(o)]

    # Aggregate guard (MINECRAFT-362 review, third round): if literally
    # EVERY pin examined this run failed to resolve at all, that is not
    # the documented baseline (which always has some already_pinned/
    # no_candidate/bumped mix even accounting for the known-gap pins) --
    # it signals something systemic (a Modrinth outage, a response-shape
    # change, a revoked token) that the per-pin allowlist above cannot
    # and should not absorb. Fail regardless of which pins are on it.
    if outcomes and all(o["outcome"] == "resolution_failed" for o in outcomes):
        print("::error::every pin examined this run failed to resolve at all -- this looks like "
              "a systemic problem (Modrinth outage, response-shape change, or a bad/expired "
              "token), not the documented per-pin coverage gap. Failing regardless of the "
              "known-anonymous-404 allowlist.")
        failed = outcomes

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
