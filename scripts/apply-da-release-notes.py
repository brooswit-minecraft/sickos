#!/usr/bin/env python3
"""Edit a GitHub release BODY to carry the DA auto-bump notes file.

Contract lives in docs/release-notes-plumbing.md. This script has two jobs,
each callable independently so tests never have to shell out to `gh` or
`git`'s network paths:

- `gate`: decide whether the calling workflow should act at all, given the
  triggering "Release" workflow_run's conclusion/event and the pushed head
  commit's message, plus the repo checked out at that head sha. Never fails
  the process (always exits 0) -- it only reports pass/fail, because most
  "Release" runs are ordinary releases with nothing for this workflow to do,
  and that is not an error.
- `apply`: read the current GitHub release body, compose the new body (see
  compose_release_body), and write it back with `gh release edit`. Fails
  loudly (non-zero exit) if the release does not exist -- this script never
  creates one.

Pure functions (has_da_auto_bump_trailer, read_pack_version,
notes_file_path, evaluate_gate, compose_release_body) are unit-tested
directly in tests/test_apply_da_release_notes.py without touching the
network or a real git/gh state beyond what subprocess calls are unavoidable
(git interpret-trailers itself, which is local and network-free).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Marks the boundary between the DA notes file's own content and GitHub's
# generated notes preserved below it. Must never appear in a notes file's
# own content for the idempotency split in compose_release_body to work;
# docs/da-auto-bump.md's notes-file contract does not use HTML comments, so
# this is safe in practice, not just in principle.
MARKER = "<!-- da-auto-bump-notes: GitHub's generated notes are preserved below -->"


class GateError(Exception):
    """Raised only for a repo-state problem that means the gate itself could not run."""


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str
    version: str | None = None


def has_da_auto_bump_trailer(commit_message: str) -> bool:
    """True iff `commit_message` carries a DA-Auto-Bump trailer, by key only.

    Uses git's own trailer parser rather than a loose grep, so a mention of
    the words in the message body (not in a trailer block) never counts,
    and any value shape after the colon is accepted -- the convention
    `DA-Auto-Bump: <da_version> <modrinth_version_id>` is not enforced here.
    """
    result = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=commit_message,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if key.lower() == "da-auto-bump":
            return True
    return False


def read_pack_version(repo_root: Path) -> str:
    """Read pack.toml's `version = "..."` at the given repo root."""
    pack_toml = repo_root / "pack.toml"
    text = pack_toml.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise GateError(f"{pack_toml} has no version field")
    return m.group(1)


def notes_file_path(repo_root: Path, version: str) -> Path:
    return repo_root / "docs" / "releases" / f"{version}.md"


def evaluate_gate(
    *,
    conclusion: str,
    event: str,
    commit_message: str,
    repo_root: Path,
) -> GateResult:
    """All four conditions from the ticket, each checked independently.

    Order matters only for which reason is reported when more than one
    condition fails; each condition is still checked on its own (see the
    tests), not folded into a single combined boolean.
    """
    if conclusion != "success":
        return GateResult(False, f"Release run did not succeed (conclusion={conclusion!r})")
    if event != "push":
        return GateResult(False, f"Release run was not triggered by push (event={event!r})")
    if not has_da_auto_bump_trailer(commit_message):
        return GateResult(False, "head commit carries no DA-Auto-Bump trailer")
    try:
        version = read_pack_version(repo_root)
    except GateError as e:
        return GateResult(False, f"could not read pack version: {e}")
    notes = notes_file_path(repo_root, version)
    if not notes.is_file():
        return GateResult(False, f"notes file not found: {notes}")
    return GateResult(True, f"gate passed for pack version {version}", version=version)


def _generated_tail(current_body: str) -> str:
    """Extract the generated-notes tail preserved from a prior edit.

    Returns the whole (trimmed) body when the marker is absent -- i.e. this
    is the first edit and `current_body` is GitHub's own generated notes.
    """
    if MARKER in current_body:
        return current_body.split(MARKER, 1)[1].strip("\n")
    return current_body.strip("\n")


def compose_release_body(current_body: str | None, notes_content: str) -> str:
    """Compose the new release body: notes file, marker, then generated notes.

    Idempotent: calling this again with the body it just produced as
    `current_body` (and the same `notes_content`) yields a byte-identical
    result, because the generated-notes tail is read back out from behind
    the marker rather than re-appended on top of it. Handles an empty (or
    null) body the same way, without a stray double marker or a dangling
    blank section.
    """
    generated = _generated_tail(current_body or "")
    parts = [notes_content.rstrip("\n"), MARKER]
    if generated:
        parts.append(generated)
    return "\n\n".join(parts) + "\n"


def fetch_current_body(tag: str) -> str:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--json", "body", "-q", ".body"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GateError(
            f"release {tag} not found or `gh release view` failed: {result.stderr.strip()}"
        )
    return result.stdout


def apply_release_notes(*, repo_root: Path, version: str, summary_path: Path | None) -> None:
    tag = f"v{version}"
    notes_path = notes_file_path(repo_root, version)
    if not notes_path.is_file():
        raise GateError(f"notes file not found: {notes_path}")
    notes_content = notes_path.read_text(encoding="utf-8")

    current_body = fetch_current_body(tag)
    new_body = compose_release_body(current_body, notes_content)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(new_body)
        tmp_path = f.name

    subprocess.run(["gh", "release", "edit", tag, "--notes-file", tmp_path], check=True)

    summary_line = f"Edited GitHub release {tag}'s body from {notes_path}\n"
    if summary_path is not None:
        with summary_path.open("a", encoding="utf-8") as f:
            f.write(summary_line)
    print(summary_line, end="")


def _write_github_output(path: Path, passed: bool, reason: str, version: str | None) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(f"passed={'true' if passed else 'false'}\n")
        f.write(f"reason={reason}\n")
        f.write(f"version={version or ''}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gate_p = sub.add_parser("gate", help="Check whether this push qualifies for a notes edit.")
    gate_p.add_argument("--conclusion", required=True)
    gate_p.add_argument("--event", required=True)
    gate_p.add_argument("--commit-message-file", required=True, type=Path)
    gate_p.add_argument("--repo-root", required=True, type=Path)
    gate_p.add_argument("--github-output", type=Path, default=None)

    apply_p = sub.add_parser("apply", help="Edit the GitHub release body.")
    apply_p.add_argument("--repo-root", required=True, type=Path)
    apply_p.add_argument("--version", required=True)
    apply_p.add_argument("--github-step-summary", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.command == "gate":
        commit_message = args.commit_message_file.read_text(encoding="utf-8")
        result = evaluate_gate(
            conclusion=args.conclusion,
            event=args.event,
            commit_message=commit_message,
            repo_root=args.repo_root,
        )
        print(result.reason)
        if args.github_output is not None:
            _write_github_output(args.github_output, result.passed, result.reason, result.version)
        return 0

    if args.command == "apply":
        try:
            apply_release_notes(
                repo_root=args.repo_root,
                version=args.version,
                summary_path=args.github_step_summary,
            )
        except GateError as e:
            print(f"::error::{e}", file=sys.stderr)
            return 1
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
