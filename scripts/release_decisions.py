#!/usr/bin/env python3
"""Consumer-side decision logic for the accepted-change release policy.

See docs/release-policy.md for the full contract this implements. This
module holds every branch as a pure, testable function (Section 2.2's own
instruction: "written as testable Python ... rather than inline shell,
mirroring scripts/bump-dynamic-atmosphere.py's existing precedent"). The
only non-pure pieces are the `main()` subcommands at the bottom, which
`.github/workflows/release.yml`'s jobs call directly; everything they do is
read already-fetched JSON/text from argv/files and print GITHUB_OUTPUT-style
lines or a commit-status pair -- no network call, no `gh`/`rinth` invocation,
lives in this file. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

README_POINTER = "README.md#secrets--variables"

MISSING_CONFIG_VARS = ("MODRINTH_TOKEN", "MODRINTH_PROJECT_ID")


# --------------------------------------------------------------------------
# Section 1: hold-trailer detection.
# --------------------------------------------------------------------------

def has_trailer(name: str, message: str) -> bool:
    """True iff `message` carries a trailer keyed `name` (case-insensitive),
    using git's own trailer parser rather than a loose grep -- a mention of
    the key in the message body (not in a trailer block) never counts. Any
    value shape after the colon is accepted. Mirrors
    scripts/apply-da-release-notes.py's has_da_auto_bump_trailer(), kept as
    its own copy here rather than imported, since
    docs/release-policy.md leaves "extend it, or extract a shared helper"
    as this story's implementation choice and the two scripts have no other
    coupling worth introducing for one four-line function.
    """
    result = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=message,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if key.lower() == name.lower():
            return True
    return False


def trailer_value(name: str, message: str) -> str | None:
    """The value of the FIRST trailer keyed `name` (case-insensitive), or
    None if absent. Free text, never parsed or validated further -- used
    only for Release-Hold-Reason, which docs/release-policy.md says is
    "recorded verbatim ... but never parsed or validated"."""
    result = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=message,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() == name.lower():
            return value.strip()
    return None


def parse_hold(head_commit_message: str) -> dict:
    """Section 1: held/reason from a push's head_commit message ALONE --
    this function is never given more than one commit's message, which is
    itself the test that proves the documented "Rebase and merge" gap
    (a trailer on an earlier commit of a multi-commit push is invisible)."""
    held = has_trailer("Release-Hold", head_commit_message)
    reason = trailer_value("Release-Hold-Reason", head_commit_message) if held else None
    return {"held": held, "reason": reason}


# --------------------------------------------------------------------------
# Section 2 (Overlap correction): skipped-intermediate-version detection.
# --------------------------------------------------------------------------

def find_skipped_intermediate_versions(version_history: list[str], released_versions: set[str]) -> list[str]:
    """`version_history`: every `pack.toml` version string that has appeared
    in git history (any order; duplicates allowed and ignored via a set).
    `released_versions`: version strings (no leading 'v') that already have
    a matching GitHub Release tag. Returns the versions that appear in
    history but have no matching release, in the order they first appear in
    `version_history`, de-duplicated -- a non-fatal finding, never used to
    fail the current run."""
    seen = set()
    skipped = []
    for version in version_history:
        if version in seen:
            continue
        seen.add(version)
        if version not in released_versions:
            skipped.append(version)
    return skipped


# --------------------------------------------------------------------------
# Section 2.2: existing-release conflict / replay / first-attempt.
# --------------------------------------------------------------------------

def resolve_existing_release(existing_target_commitish: str | None, this_run_sha: str) -> dict:
    """`existing_target_commitish`: None when `gh release view` found no
    release for this version; otherwise the release's targetCommitish.
    Returns {"branch": "no_release"|"replay"|"conflict", ...}."""
    if existing_target_commitish is None:
        return {"branch": "no_release"}
    if existing_target_commitish == this_run_sha:
        return {"branch": "replay", "sha": this_run_sha}
    return {
        "branch": "conflict",
        "existing_sha": existing_target_commitish,
        "this_sha": this_run_sha,
    }


def conflict_message(version: str, existing_sha: str, this_sha: str) -> str:
    return (
        f"GitHub Release v{version} already exists for a different source commit "
        f"(existing target_commitish={existing_sha}, this run's sha={this_sha}). "
        "The pack.toml version number was reused for different content -- bump "
        "pack.toml's version before merging another pack change."
    )


# --------------------------------------------------------------------------
# Section 2.2 / 4: required Modrinth configuration.
# --------------------------------------------------------------------------

def check_modrinth_config(token: str | None, project_id: str | None) -> dict:
    missing = []
    if not token:
        missing.append("MODRINTH_TOKEN")
    if not project_id:
        missing.append("MODRINTH_PROJECT_ID")
    return {"configured": len(missing) == 0, "missing": missing}


def missing_config_message(missing: list[str]) -> str:
    joined = "/".join(missing)
    return (
        f"Required Modrinth configuration is missing: {joined}. "
        f"Set it as described in {README_POINTER}."
    )


# --------------------------------------------------------------------------
# Section 3: identity checks -- shared by recover-publish's
# duplicate-then-verify fallback and verify-publication's read-back.
# --------------------------------------------------------------------------

_FILENAME_RE_CACHE: dict[str, re.Pattern] = {}


def _filename_pattern(pack_name: str, version: str) -> re.Pattern:
    key = f"{pack_name}\0{version}"
    pattern = _FILENAME_RE_CACHE.get(key)
    if pattern is None:
        pattern = re.compile(r"^" + re.escape(f"{pack_name}-{version}") + r"\.mrpack$")
        _FILENAME_RE_CACHE[key] = pattern
    return pattern


_SOURCE_REVISION_RE = re.compile(r"(?mi)^Source-Revision:\s*([0-9a-f]{40})\s*$")


def check_identity(
    version_json: dict,
    *,
    expected_version_number: str,
    expected_sha: str,
    pack_name: str,
    source_revision_available: bool,
) -> dict:
    """Section 3's "all three matching is what 'published' means": exact
    version_number, exactly one attached file matching
    `<pack name>-<version>.mrpack`, and (once MINECRAFT-44 lands) a
    Source-Revision trailer in the changelog equal to `expected_sha`.

    `source_revision_available` must be False until MINECRAFT-44 ships --
    callers pass that in explicitly rather than this function guessing from
    the changelog's absence, so a genuinely missing trailer post-MINECRAFT-44
    is still reported as a real mismatch, never silently reclassified as
    "not yet available"."""
    mismatches = []

    actual_version_number = version_json.get("version_number")
    if actual_version_number != expected_version_number:
        mismatches.append(
            f"version_number mismatch: expected {expected_version_number!r}, got {actual_version_number!r}"
        )

    files = version_json.get("files") or []
    pattern = _filename_pattern(pack_name, expected_version_number)
    matching_files = [f for f in files if pattern.match(f.get("filename") or "")]
    if len(matching_files) != 1:
        mismatches.append(
            f"expected exactly one attached file matching {pattern.pattern!r}, found {len(matching_files)} "
            f"(filenames: {[f.get('filename') for f in files]})"
        )

    if not source_revision_available:
        source_revision_status = "not_available"
    else:
        changelog = version_json.get("changelog") or ""
        match = _SOURCE_REVISION_RE.search(changelog)
        if match is None:
            source_revision_status = "mismatch"
            mismatches.append("changelog carries no Source-Revision trailer")
        elif match.group(1).lower() != expected_sha.lower():
            source_revision_status = "mismatch"
            mismatches.append(
                f"Source-Revision mismatch: expected {expected_sha}, got {match.group(1)}"
            )
        else:
            source_revision_status = "match"

    return {
        "ok": len(mismatches) == 0,
        "mismatches": mismatches,
        "source_revision_status": source_revision_status,
    }


# --------------------------------------------------------------------------
# Section 2.2 step 3: recover-publish's duplicate-then-verify fallback.
# --------------------------------------------------------------------------

def resolve_duplicate_publish(
    version_json: dict,
    *,
    expected_version_number: str,
    expected_sha: str,
    pack_name: str,
) -> dict:
    """`rinth publish` exited 5 (duplicate version_number); `version_json`
    is the matching version from `rinth versions latest`. MINECRAFT-44 has
    not shipped at this call site either, so source-revision is never
    checked here -- only version_number and filename identity, exactly as
    docs/release-policy.md's Section 2.2 step 3 describes for this
    fallback (it names "the returned version's identity (Section 3)",
    which is the three-field check, but the fallback predates a landed
    Source-Revision trailer the same way verify-publication does)."""
    identity = check_identity(
        version_json,
        expected_version_number=expected_version_number,
        expected_sha=expected_sha,
        pack_name=pack_name,
        source_revision_available=False,
    )
    if identity["ok"]:
        return {"outcome": "published", "detail": "benign replay: duplicate version matches this run's intent"}
    return {
        "outcome": "failed",
        "detail": "publish-identity conflict: " + "; ".join(identity["mismatches"]),
    }


# --------------------------------------------------------------------------
# Section 3: verify-publication (read-back).
# --------------------------------------------------------------------------

RINTH_EXIT_NOT_FOUND = 4
RINTH_EXIT_API_ERROR = 5
RINTH_EXIT_WAIT_TIMEOUT = 8


def resolve_verify_publication(
    *,
    rinth_exit_code: int,
    version_json: dict | None,
    expected_version_number: str,
    expected_sha: str,
    pack_name: str,
) -> dict:
    """`rinth_exit_code` is `versions latest ... --wait ...`'s own exit
    code; `version_json` is its parsed --json stdout on a 0 exit, else
    None. Returns {"outcome": "published"|"failed", "detail": str}."""
    if rinth_exit_code == RINTH_EXIT_WAIT_TIMEOUT:
        return {
            "outcome": "failed",
            "detail": f"verify-publication timed out (rinth exit {rinth_exit_code}) waiting for "
            f"v{expected_version_number} to appear on Modrinth",
        }
    if rinth_exit_code == RINTH_EXIT_NOT_FOUND:
        return {
            "outcome": "failed",
            "detail": f"verify-publication could not read the Modrinth project (rinth exit {rinth_exit_code})",
        }
    if rinth_exit_code != 0 or version_json is None:
        return {
            "outcome": "failed",
            "detail": f"verify-publication's read-back failed (rinth exit {rinth_exit_code})",
        }

    identity = check_identity(
        version_json,
        expected_version_number=expected_version_number,
        expected_sha=expected_sha,
        pack_name=pack_name,
        source_revision_available=False,
    )
    if identity["ok"]:
        detail = f"v{expected_version_number} == {expected_sha[:8]}"
        if identity["source_revision_status"] == "not_available":
            detail += " (Source-Revision check: not yet available (MINECRAFT-44 pending))"
        return {"outcome": "published", "detail": detail}
    return {
        "outcome": "failed",
        "detail": "verify-publication mismatch: " + "; ".join(identity["mismatches"]),
    }


# --------------------------------------------------------------------------
# Section 4: outcome -> commit-status mapping, and the top-level
# per-run outcome resolver record-outcome calls.
# --------------------------------------------------------------------------

OUTCOME_STATUS = {
    "held": "error",
    "skipped": "error",
    "publishing": "pending",
    "published": "success",
    "failed": "failure",
}


def outcome_to_status(outcome: str, description: str) -> dict:
    if outcome not in OUTCOME_STATUS:
        raise ValueError(f"unknown outcome {outcome!r}")
    return {"state": OUTCOME_STATUS[outcome], "description": description}


def resolve_run_outcome(
    *,
    held: bool,
    held_reason: str | None,
    existing_branch: str | None,
    conflict_detail: str | None,
    config_missing: list[str] | None,
    release_job_result: str | None,
    recover_outcome: str | None,
    recover_detail: str | None,
    verify_outcome: str | None,
    verify_detail: str | None,
) -> dict:
    """The single place `record-outcome` asks "what happened, overall?".
    Every argument mirrors one upstream job's own output/`result` exactly
    (see `.github/workflows/release.yml`'s `record-outcome` job) so this
    function can be fed fabricated values in tests without any job ever
    running. Never guesses: a combination it doesn't recognize is itself a
    `failed` outcome (fail-closed), never silently `build-only`/`skipped`.
    """
    if held:
        description = "HELD" + (f": {held_reason}" if held_reason else "")
        return {"outcome": "held", **outcome_to_status("held", description)}

    if existing_branch == "conflict":
        return {"outcome": "failed", **outcome_to_status("failed", f"FAILED: {conflict_detail}")}

    if config_missing:
        return {
            "outcome": "failed",
            **outcome_to_status("failed", f"FAILED: missing required Modrinth configuration: " +
                                 missing_config_message(config_missing)),
        }

    if existing_branch == "no_release":
        if release_job_result != "success":
            return {"outcome": "failed", **outcome_to_status("failed", f"FAILED: build/release job result={release_job_result}")}
        if verify_outcome == "published":
            return {"outcome": "published", **outcome_to_status("published", f"PUBLISHED: {verify_detail}")}
        return {"outcome": "failed", **outcome_to_status("failed", f"FAILED: {verify_detail}")}

    if existing_branch == "replay":
        if recover_outcome == "published":
            return {"outcome": "published", **outcome_to_status("published", f"PUBLISHED: {recover_detail}")}
        if recover_outcome == "publish_succeeded":
            if verify_outcome == "published":
                return {"outcome": "published", **outcome_to_status("published", f"PUBLISHED: {verify_detail}")}
            return {"outcome": "failed", **outcome_to_status("failed", f"FAILED: {verify_detail}")}
        return {"outcome": "failed", **outcome_to_status("failed", f"FAILED: {recover_detail}")}

    return {"outcome": "failed", **outcome_to_status("failed", "FAILED: unrecognized run shape (fail-closed)")}


# --------------------------------------------------------------------------
# CLI glue for the workflow's job steps. Each subcommand does the minimum
# shell-adjacent work (read argv/files, write GITHUB_OUTPUT-style lines or
# print a result to stdout) and nothing else -- all the actual deciding
# happens in the pure functions above.
# --------------------------------------------------------------------------

def _write_outputs(path: str | None, pairs: dict) -> None:
    text = "\n".join(f"{key}={value}" for key, value in pairs.items()) + "\n"
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    hold_p = sub.add_parser("check-hold")
    hold_p.add_argument("--message-file", required=True, type=Path)
    hold_p.add_argument("--github-output", default=None)

    existing_p = sub.add_parser("check-existing")
    existing_p.add_argument("--existing-target-commitish", default="")
    existing_p.add_argument("--sha", required=True)
    existing_p.add_argument("--version", required=True)
    existing_p.add_argument("--github-output", default=None)

    config_p = sub.add_parser("check-config")
    config_p.add_argument("--token-set", choices=["true", "false"], required=True)
    config_p.add_argument("--project-id-set", choices=["true", "false"], required=True)
    config_p.add_argument("--github-output", default=None)

    skipped_p = sub.add_parser("find-skipped")
    skipped_p.add_argument("--history-file", required=True, type=Path,
                            help="one pack.toml version string per line, any order")
    skipped_p.add_argument("--released-file", required=True, type=Path,
                            help="one released version string per line (no leading 'v')")

    duplicate_p = sub.add_parser("resolve-duplicate")
    duplicate_p.add_argument("--version-json-file", required=True, type=Path)
    duplicate_p.add_argument("--expected-version-number", required=True)
    duplicate_p.add_argument("--expected-sha", required=True)
    duplicate_p.add_argument("--pack-name", required=True)
    duplicate_p.add_argument("--github-output", default=None)

    verify_p = sub.add_parser("resolve-verify")
    verify_p.add_argument("--rinth-exit-code", required=True, type=int)
    verify_p.add_argument("--version-json-file", type=Path, default=None,
                           help="omit, or point at an empty/missing file, when the rinth call did not exit 0")
    verify_p.add_argument("--expected-version-number", required=True)
    verify_p.add_argument("--expected-sha", required=True)
    verify_p.add_argument("--pack-name", required=True)
    verify_p.add_argument("--github-output", default=None)

    outcome_p = sub.add_parser("resolve-outcome")
    outcome_p.add_argument("--held", choices=["true", "false"], required=True)
    outcome_p.add_argument("--held-reason", default="")
    outcome_p.add_argument("--existing-branch", default="")
    outcome_p.add_argument("--conflict-detail", default="")
    outcome_p.add_argument("--config-missing", default="",
                            help="comma-separated, e.g. 'MODRINTH_TOKEN,MODRINTH_PROJECT_ID'")
    outcome_p.add_argument("--release-job-result", default="")
    outcome_p.add_argument("--recover-outcome", default="")
    outcome_p.add_argument("--recover-detail", default="")
    outcome_p.add_argument("--verify-outcome", default="")
    outcome_p.add_argument("--verify-detail", default="")
    outcome_p.add_argument("--github-output", default=None)

    args = parser.parse_args(argv)

    if args.command == "check-hold":
        message = args.message_file.read_text(encoding="utf-8")
        result = parse_hold(message)
        _write_outputs(args.github_output, {
            "held": "true" if result["held"] else "false",
            "reason": result["reason"] or "",
        })
        return 0

    if args.command == "check-existing":
        existing = args.existing_target_commitish or None
        result = resolve_existing_release(existing, args.sha)
        outputs = {"branch": result["branch"]}
        if result["branch"] == "conflict":
            outputs["message"] = conflict_message(args.version, result["existing_sha"], result["this_sha"])
        _write_outputs(args.github_output, outputs)
        return 0

    if args.command == "check-config":
        result = check_modrinth_config(
            token="x" if args.token_set == "true" else None,
            project_id="x" if args.project_id_set == "true" else None,
        )
        outputs = {"configured": "true" if result["configured"] else "false"}
        if not result["configured"]:
            outputs["message"] = missing_config_message(result["missing"])
            outputs["missing"] = ",".join(result["missing"])
        _write_outputs(args.github_output, outputs)
        return 0 if result["configured"] else 1

    if args.command == "find-skipped":
        history = [line.strip() for line in args.history_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        released = {line.strip() for line in args.released_file.read_text(encoding="utf-8").splitlines() if line.strip()}
        skipped = find_skipped_intermediate_versions(history, released)
        if skipped:
            print(
                "Non-fatal finding: these pack.toml versions appear in git history with no "
                f"matching GitHub Release tag (likely dropped by the sickos-release concurrency "
                f"group -- see docs/release-policy.md Section 2's 'Overlap' correction): {', '.join(skipped)}"
            )
        else:
            print("No skipped intermediate versions detected.")
        return 0

    if args.command == "resolve-duplicate":
        version_json = json.loads(args.version_json_file.read_text(encoding="utf-8"))
        result = resolve_duplicate_publish(
            version_json,
            expected_version_number=args.expected_version_number,
            expected_sha=args.expected_sha,
            pack_name=args.pack_name,
        )
        _write_outputs(args.github_output, {"outcome": result["outcome"], "detail": result["detail"]})
        return 0

    if args.command == "resolve-verify":
        version_json = None
        if args.version_json_file is not None and args.version_json_file.is_file():
            text = args.version_json_file.read_text(encoding="utf-8").strip()
            if text:
                version_json = json.loads(text)
        result = resolve_verify_publication(
            rinth_exit_code=args.rinth_exit_code,
            version_json=version_json,
            expected_version_number=args.expected_version_number,
            expected_sha=args.expected_sha,
            pack_name=args.pack_name,
        )
        _write_outputs(args.github_output, {"outcome": result["outcome"], "detail": result["detail"]})
        return 0

    if args.command == "resolve-outcome":
        config_missing = [item for item in args.config_missing.split(",") if item]
        result = resolve_run_outcome(
            held=args.held == "true",
            held_reason=args.held_reason or None,
            existing_branch=args.existing_branch or None,
            conflict_detail=args.conflict_detail or None,
            config_missing=config_missing,
            release_job_result=args.release_job_result or None,
            recover_outcome=args.recover_outcome or None,
            recover_detail=args.recover_detail or None,
            verify_outcome=args.verify_outcome or None,
            verify_detail=args.verify_detail or None,
        )
        _write_outputs(args.github_output, {
            "outcome": result["outcome"],
            "state": result["state"],
            "description": result["description"],
        })
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
