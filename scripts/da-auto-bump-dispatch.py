#!/usr/bin/env python3
"""Logic for `.github/workflows/da-auto-bump.yml` (SICKOS-78).

Kept out of the workflow YAML so it is unit-testable (see
tests/test_da_auto_bump_dispatch.py) without a GitHub Actions runner.
Contract lives in docs/da-auto-bump.md's "CI wiring" section. This script
never touches secrets directly -- credential values are checked for
emptiness in the workflow (via env:, never `if:`) and only booleans /
non-secret variable values are ever passed to this script.

Subcommands, each independently callable so the workflow YAML stays thin:

- `select-credential`: decide which push credential (App pair, PAT, or
  none) to use, given only booleans/non-secret values.
- `decide`: map the engine's exit code + summary JSON to a workflow
  outcome (no_op / bump / failure), and render the bulk of the evidence
  job summary.
- `finalize-summary`: append the one line the `decide` step cannot yet
  know -- the pushed commit sha, or why there isn't one.
- `build-commit-message`: construct the commit message, including the
  DA-Auto-Bump trailer in the exact shape git's trailer parser requires.
- `open-listing-issue`: file the listing-review-required GitHub issue
  (item 8). Never fails the run; any error is a warning.

The pure functions (pause_gate, select_credential, classify_engine_result,
render_job_summary, render_pushed_commit_line, build_commit_message) are
what tests/test_da_auto_bump_dispatch.py exercises directly.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

MISSING_CREDENTIAL_NAMES = (
    "SICKOS_DISPATCH_APP_ID (variable)",
    "SICKOS_DISPATCH_APP_PRIVATE_KEY (secret)",
    "SICKOS_DISPATCH_TOKEN (secret)",
)


# --------------------------------------------------------------------------
# Load apply-da-release-notes.py's has_da_auto_bump_trailer by file path,
# the same way its own test suite does -- the filename has hyphens, so it
# cannot be imported by a normal `import` statement. Imported (not
# reimplemented) so build_commit_message is tested against the real gate
# function SICKOS-79's workflow actually runs, not a re-derived copy of it.
# --------------------------------------------------------------------------

def _load_has_da_auto_bump_trailer():
    module_path = Path(__file__).resolve().parent / "apply-da-release-notes.py"
    spec = importlib.util.spec_from_file_location("apply_da_release_notes", module_path)
    module = importlib.util.module_from_spec(spec)
    # Must be registered in sys.modules before exec_module(): the target
    # module defines a @dataclass, whose decorator looks itself up via
    # sys.modules[cls.__module__] while the class body is still executing.
    sys.modules["apply_da_release_notes"] = module
    spec.loader.exec_module(module)
    return module.has_da_auto_bump_trailer


# --------------------------------------------------------------------------
# Pause switch (item 2). Exactly "true" pauses; unset or any other value
# (including "TRUE" or "") runs. The workflow's own gate step duplicates
# this as inline bash rather than calling this script, because it must run
# before anything is checked out (see the workflow file's comment on that
# step) -- this is the same rule, unit-tested here so the two cannot drift
# unnoticed.
# --------------------------------------------------------------------------

def pause_gate(value: str | None) -> bool:
    return value == "true"


# --------------------------------------------------------------------------
# Credential selection (item 3).
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CredentialSelection:
    method: str  # "app", "pat", or "none"
    warnings: list[str] = field(default_factory=list)


def select_credential(*, app_id: str, app_key_set: bool, pat_set: bool) -> CredentialSelection:
    app_id_set = bool(app_id)
    warnings: list[str] = []

    if app_id_set and app_key_set:
        return CredentialSelection(method="app", warnings=warnings)

    if app_id_set != app_key_set:
        # Exactly one half of the App pair is configured.
        missing = "SICKOS_DISPATCH_APP_PRIVATE_KEY (secret)" if app_id_set else "SICKOS_DISPATCH_APP_ID (variable)"
        warnings.append(
            f"GitHub App credential is half-configured: {missing} is missing. "
            "Falling back to SICKOS_DISPATCH_TOKEN." + ("" if pat_set else " (also not set.)")
        )

    if pat_set:
        return CredentialSelection(method="pat", warnings=warnings)
    return CredentialSelection(method="none", warnings=warnings)


# --------------------------------------------------------------------------
# Engine exit code / summary -> workflow decision (items 4, 7).
# --------------------------------------------------------------------------

def classify_engine_result(exit_code: int, outcome: str | None) -> str:
    """Return "no_op", "bump", or "failure".

    Fail closed by construction: only exit 0 with outcome=="already_pinned"
    or outcome=="bumped" is ever treated as non-failure. Any other exit
    code -- documented or not -- and any exit-0 run with an unrecognised
    outcome string, is "failure". This is deliberately not a lookup table
    of "known" exit codes: a code docs/da-auto-bump.md has not documented
    yet (a future engine change) still falls through to "failure" here,
    never to "no_op" or "bump" by accident.
    """
    if exit_code == 0:
        if outcome == "already_pinned":
            return "no_op"
        if outcome == "bumped":
            return "bump"
    return "failure"


def _verification_status(action: str, outcome: str | None) -> str:
    if action == "bump":
        return "verified (sha1 and sha512 both matched before any file was written)"
    if outcome == "already_pinned":
        return "not applicable (already pinned; nothing was downloaded)"
    if outcome == "integrity_failure":
        return "failed (downloaded jar hash mismatch, or the download itself failed)"
    return f"not verified (run did not reach or pass the integrity check; outcome={outcome!r})"


def _vouch_text(vouch: dict | None) -> str:
    if vouch is None:
        return "not run"
    text = f"`{vouch.get('result')}` (control status={vouch.get('control_status')}, DA status={vouch.get('da_status')})"
    if vouch.get("error"):
        text += f" -- error: {vouch['error']}"
    return text


def _listing_text(summary: dict) -> str:
    if not summary.get("listing_review_required"):
        return "no"
    lines = summary.get("listing_flagged_lines") or []
    if not lines:
        return "yes (no flagged lines recorded)"
    rendered = "; ".join(f"{item['file']}:{item['line']}" for item in lines)
    return f"yes -- flagged lines not auto-updated, may be stale: {rendered}"


def render_job_summary(summary: dict, *, action: str) -> str:
    """The bulk of the evidence summary (item 7), everything except the
    pushed-commit-sha line, which render_pushed_commit_line adds once the
    push has been attempted (or skipped)."""
    heading = {"no_op": "no-op (already pinned)", "bump": "bumped", "failure": "failed"}[action]
    lines = [
        f"## DA auto-bump: {heading}",
        "",
        f"- **Outcome:** `{summary.get('outcome')}`",
        f"- **Dynamic Atmosphere version:** `{summary.get('da_version')}`",
        f"- **Modrinth version id:** `{summary.get('modrinth_version_id')}`",
        f"- **sha1/sha512 verification:** {_verification_status(action, summary.get('outcome'))}",
        f"- **Category received:** `{summary.get('category_received')!r}`",
        f"- **Mapping rule:** `{summary.get('mapping_rule')}`",
        f"- **Previous Sickos version:** `{summary.get('previous_pack_version')}`",
        f"- **New Sickos version:** `{summary.get('new_pack_version')}`",
        f"- **Notes file:** `{summary.get('notes_path') or 'none'}`",
        f"- **Modrinth vouch check:** {_vouch_text(summary.get('vouch'))}",
        f"- **Listing review required:** {_listing_text(summary)}",
    ]
    if action == "failure":
        lines.append(f"- **Error:** {summary.get('error')}")
    return "\n".join(lines) + "\n"


def render_pushed_commit_line(*, action: str, credential_method: str, pushed_sha: str | None, push_failed: bool) -> str:
    if action == "no_op":
        return "- **Pushed commit:** none (already pinned; no push needed)\n"
    if action == "failure":
        return "- **Pushed commit:** none (run failed before any push was attempted; see Error above)\n"
    # action == "bump"
    if credential_method == "none":
        return "- **Pushed commit:** none (no push credential configured; see fail-closed section)\n"
    if push_failed:
        return "- **Pushed commit:** none (push was rejected; safe to re-run, the engine is idempotent)\n"
    if pushed_sha:
        return f"- **Pushed commit:** `{pushed_sha}`\n"
    return "- **Pushed commit:** none (push was not attempted)\n"


def render_paused_summary(da_version: str | None) -> str:
    version_text = da_version or "<unknown -- payload had no version>"
    return (
        "## DA auto-bump: paused\n"
        "\n"
        f"Declined to pin Dynamic Atmosphere `{version_text}`. "
        "This dispatch is NOT queued -- Dynamic Atmosphere fires the "
        "repository_dispatch once per version.\n"
        "\n"
        "To catch up once unpaused:\n"
        "- Use \"Re-run all jobs\" on this run -- it re-evaluates "
        "SICKOS_AUTOBUMP_PAUSED and reuses this dispatch's original payload, or\n"
        "- wait for a re-delivered dispatch, or\n"
        "- rely on the story-3 scheduled Modrinth poll backstop to catch up.\n"
        "\n"
        "Resume with: `gh variable delete SICKOS_AUTOBUMP_PAUSED "
        "--repo <owner>/<repo>`\n"
    )


# --------------------------------------------------------------------------
# Commit message construction (item 5). The DA-Auto-Bump trailer must sit
# alone in the commit message's final paragraph, separated from everything
# above it by a blank line -- git's trailer parser (and SICKOS-79's gate)
# silently ignores a trailer-shaped line glued directly under prose or a
# bullet list. See has_da_auto_bump_trailer's docstring in
# apply-da-release-notes.py.
# --------------------------------------------------------------------------

_FEAT_MAPPING_RULES = {
    "minor",
    "breaking_as_minor",
    "minor_default_absent_category",
    "minor_default_unrecognised_category",
}


def build_commit_message(*, mapping_rule: str, new_pack_version: str, da_version: str, modrinth_version_id: str) -> str:
    commit_type = "feat" if mapping_rule in _FEAT_MAPPING_RULES else "fix"
    if mapping_rule == "breaking_as_minor":
        # CONTRIBUTING.md requires a breaking change to be labelled
        # explicitly, never hidden inside a normal-looking release: mark
        # the subject itself, not only the mapping_rule buried in the body.
        subject = (
            f"{commit_type}!: release Sickos {new_pack_version} "
            f"pinning Dynamic Atmosphere {da_version} (BREAKING)"
        )
    else:
        subject = f"{commit_type}: release Sickos {new_pack_version} pinning Dynamic Atmosphere {da_version}"
    body = [
        "Automated Dynamic Atmosphere auto-bump via repository_dispatch.",
        "",
        f"- Dynamic Atmosphere version: {da_version}",
        f"- Modrinth version id: {modrinth_version_id}",
        f"- Mapping rule: {mapping_rule}",
    ]
    trailer = f"DA-Auto-Bump: {da_version} {modrinth_version_id}"
    return subject + "\n\n" + "\n".join(body) + "\n\n" + trailer + "\n"


# --------------------------------------------------------------------------
# Listing-review issue (item 8). Never fails the run.
# --------------------------------------------------------------------------

def build_listing_issue_body(summary: dict) -> str:
    lines = [
        "Dynamic Atmosphere auto-bump flagged listing text that was NOT "
        "auto-updated and may now be stale. Only the mechanical "
        "\"Sickos ... pins Dynamic Atmosphere ...\" sentence is rewritten "
        "automatically; everything else below needs a human check.",
        "",
    ]
    for item in summary.get("listing_flagged_lines") or []:
        lines.append(f"- `{item['file']}:{item['line']}`: {item['text']}")
    return "\n".join(lines) + "\n"


def open_listing_issue(*, summary: dict, new_pack_version: str, da_version: str, repo: str, run_command=subprocess.run) -> None:
    if not summary.get("listing_review_required"):
        return
    title = f"Sickos {new_pack_version}: listing text may be stale after Dynamic Atmosphere {da_version} bump"
    body = build_listing_issue_body(summary)
    try:
        result = run_command(
            ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"::warning::could not open the listing-review issue: {result.stderr.strip()}")
    except Exception as error:  # noqa: BLE001 - issue creation must never fail the run
        print(f"::warning::could not open the listing-review issue: {error!r}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _write_github_output(path: Path, values: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        for key, value in values.items():
            f.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cred_p = sub.add_parser("select-credential")
    cred_p.add_argument("--app-id", default="")
    cred_p.add_argument("--app-key-set", required=True, choices=["true", "false"])
    cred_p.add_argument("--pat-set", required=True, choices=["true", "false"])
    cred_p.add_argument("--github-output", type=Path, default=None)

    decide_p = sub.add_parser("decide")
    decide_p.add_argument("--exit-code", required=True, type=int)
    decide_p.add_argument("--summary-path", required=True, type=Path)
    decide_p.add_argument("--github-output", type=Path, default=None)
    decide_p.add_argument("--github-step-summary", type=Path, default=None)

    finalize_p = sub.add_parser("finalize-summary")
    finalize_p.add_argument("--action", required=True, choices=["no_op", "bump", "failure"])
    finalize_p.add_argument("--credential-method", default="none")
    finalize_p.add_argument("--pushed-sha", default="")
    finalize_p.add_argument("--push-failed", default="false", choices=["true", "false"])
    finalize_p.add_argument("--github-step-summary", type=Path, default=None)

    commit_p = sub.add_parser("build-commit-message")
    commit_p.add_argument("--mapping-rule", required=True)
    commit_p.add_argument("--new-pack-version", required=True)
    commit_p.add_argument("--da-version", required=True)
    commit_p.add_argument("--modrinth-version-id", required=True)

    issue_p = sub.add_parser("open-listing-issue")
    issue_p.add_argument("--summary-path", required=True, type=Path)
    issue_p.add_argument("--new-pack-version", required=True)
    issue_p.add_argument("--da-version", required=True)
    issue_p.add_argument("--repo", required=True)

    args = parser.parse_args(argv)

    if args.command == "select-credential":
        selection = select_credential(
            app_id=args.app_id, app_key_set=args.app_key_set == "true", pat_set=args.pat_set == "true",
        )
        for warning in selection.warnings:
            print(f"::warning::{warning}")
        print(f"Selected credential: {selection.method}")
        if args.github_output is not None:
            _write_github_output(args.github_output, {"method": selection.method})
        return 0

    if args.command == "decide":
        summary = json.loads(args.summary_path.read_text(encoding="utf-8"))
        action = classify_engine_result(args.exit_code, summary.get("outcome"))
        print(f"Decision: {action} (exit_code={args.exit_code}, outcome={summary.get('outcome')})")
        if args.github_step_summary is not None:
            with args.github_step_summary.open("a", encoding="utf-8") as f:
                f.write(render_job_summary(summary, action=action))
        if args.github_output is not None:
            _write_github_output(args.github_output, {
                "action": action,
                "error_message": (summary.get("error") or "").replace("\n", " "),
                "new_pack_version": summary.get("new_pack_version") or "",
                "da_version": summary.get("da_version") or "",
                "modrinth_version_id": summary.get("modrinth_version_id") or "",
                "mapping_rule": summary.get("mapping_rule") or "",
                "notes_path": summary.get("notes_path") or "",
                "listing_review_required": "true" if summary.get("listing_review_required") else "false",
            })
        return 0

    if args.command == "finalize-summary":
        line = render_pushed_commit_line(
            action=args.action, credential_method=args.credential_method,
            pushed_sha=args.pushed_sha or None, push_failed=args.push_failed == "true",
        )
        if args.github_step_summary is not None:
            with args.github_step_summary.open("a", encoding="utf-8") as f:
                f.write(line)
        print(line, end="")
        return 0

    if args.command == "build-commit-message":
        message = build_commit_message(
            mapping_rule=args.mapping_rule, new_pack_version=args.new_pack_version,
            da_version=args.da_version, modrinth_version_id=args.modrinth_version_id,
        )
        has_trailer = _load_has_da_auto_bump_trailer()
        if not has_trailer(message):
            print("::error::constructed commit message does not carry a recognised DA-Auto-Bump trailer", file=sys.stderr)
            return 1
        sys.stdout.write(message)
        return 0

    if args.command == "open-listing-issue":
        summary = json.loads(args.summary_path.read_text(encoding="utf-8"))
        open_listing_issue(
            summary=summary, new_pack_version=args.new_pack_version,
            da_version=args.da_version, repo=args.repo,
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
