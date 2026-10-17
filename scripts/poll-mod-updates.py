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

Deliberately excludes Dynamic Atmosphere's own pin (see
`EXCLUDED_PIN_PATHS`): DA already has its dedicated, reviewed,
dispatch-driven workflow, and running two different mechanisms against
the same pin would let them race each other. If DA's dispatch is ever
retired in favor of this poll covering it too, remove it from
`EXCLUDED_PIN_PATHS` deliberately, as its own change.

Each eligible mod with a real update is a SEPARATE commit, made (not just
staged) before the next mod is considered -- so a second mod's bump reads
the pack.toml version the first mod's commit just advanced, and "two
mods' updates in the same run" coalesce into two sequential releases
rather than colliding on the same pack.toml edit. This script never
pushes (same convention as scripts/bump-dynamic-atmosphere.py and
scripts/bump_mod.py); the calling workflow pushes once at the end.

One run's full outcome (one entry per considered mod) is always printed
as JSON to stdout and optionally written to --summary-out, so a failure
partway through a multi-mod run is still fully visible, not just the
mods that happened to succeed before it.
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

EXCLUDED_PIN_PATHS = frozenset({"mods/dynamic-atmosphere.pw.toml"})


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
    key isn't minecraft", not a convention this needs to hardcode."""
    pack_toml = Path(repo_root) / "pack.toml"
    import tomllib
    with pack_toml.open("rb") as handle:
        data = tomllib.load(handle)
    versions = data.get("versions", {})
    game_version = versions.get("minecraft")
    loader_names = [key for key in versions if key != "minecraft"]
    loader = loader_names[0] if loader_names else None
    return game_version, loader


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
    """Returns a list of one outcome dict per eligible, non-excluded mod,
    in the order mod_eligibility.discover_eligible_pins returns them
    (alphabetical by pin filename -- deterministic across runs)."""
    repo_root = Path(repo_root)
    game_version, loader = read_pack_targets(repo_root)
    fetcher = fetcher or modrinth_compat.default_fetcher

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

        try:
            summary, exit_code, message = bump_mod.run_engine(
                repo_root, pin, candidate, run_command=run_command, token=token,
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

    failed = [o for o in outcomes if o["outcome"] not in
              ("no_candidate", "already_pinned", "bumped")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
