#!/usr/bin/env python3
"""Self-policing check: is scripts/dependency_closure_check.py still the
exact file ci.yml's own `dependency-check` job trusts? (MINECRAFT-362
review, twenty-second round, smaller item.)

Nothing previously enforced this. ci.yml pins
reusable-dependency-check.yml@<workflow_sha>, which in turn checks out
brooswit-minecraft/schematic at a SECOND, inner commit (the one that
actually contains scripts/dependency_check.py) -- and our own vendored
copy's header comment separately claims a commit SHA for that inner
checkout. Three numbers that must all agree, with nothing that previously
re-checked them against each other after the day they were written down.

This is a MANUAL/periodic check, deliberately not wired into `unittest
discover` or any CI gate: it makes two unauthenticated GitHub reads
(raw.githubusercontent.com), and a transient network failure or GitHub
outage must never fail a build or block a release the way a flaky
network-dependent unit test would. Run it by hand (or from a separate,
non-blocking scheduled job) when bumping either pin.

Usage: python3 scripts/check_vendored_dependency_closure_sync.py
Exit 0 if in sync, 1 if drifted or unreachable.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

CI_YML = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
VENDORED = Path(__file__).parent / "dependency_closure_check.py"

REUSABLE_WORKFLOW_REF_RE = re.compile(
    r"reusable-dependency-check\.yml@([0-9a-f]{40})"
)
VENDORED_HEADER_SHA_RE = re.compile(
    r"Vendored VERBATIM from brooswit-minecraft/schematic at commit\s*\n#\s*([0-9a-f]{40})"
)
INNER_CHECKOUT_REF_RE = re.compile(
    r"repository:\s*brooswit-minecraft/schematic\s*\n\s*ref:\s*([0-9a-f]{40})"
)

RAW_URL = (
    "https://raw.githubusercontent.com/brooswit-minecraft/schematic/"
    "{ref}/.github/workflows/reusable-dependency-check.yml"
)


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as handle:  # noqa: S310 (fixed https host, read-only)
        return handle.read().decode("utf-8")


def main() -> int:
    ci_yml_text = CI_YML.read_text()
    workflow_ref_match = REUSABLE_WORKFLOW_REF_RE.search(ci_yml_text)
    if not workflow_ref_match:
        print("could not find a reusable-dependency-check.yml@<sha> pin in ci.yml", file=sys.stderr)
        return 1
    workflow_ref = workflow_ref_match.group(1)

    vendored_text = VENDORED.read_text()
    header_match = VENDORED_HEADER_SHA_RE.search(vendored_text)
    if not header_match:
        print("could not find the vendored-commit header line in dependency_closure_check.py", file=sys.stderr)
        return 1
    header_sha = header_match.group(1)

    try:
        workflow_contents = _fetch(RAW_URL.format(ref=workflow_ref))
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"could not fetch reusable-dependency-check.yml@{workflow_ref}: {exc}", file=sys.stderr)
        return 1

    inner_match = INNER_CHECKOUT_REF_RE.search(workflow_contents)
    if not inner_match:
        print(
            f"reusable-dependency-check.yml@{workflow_ref} no longer has a schematic checkout "
            "step in the expected shape -- update this script's regex",
            file=sys.stderr,
        )
        return 1
    inner_sha = inner_match.group(1)

    if inner_sha != header_sha:
        print(
            "DRIFTED: ci.yml's dependency-check job (via "
            f"reusable-dependency-check.yml@{workflow_ref}) now checks out schematic at "
            f"{inner_sha}, but scripts/dependency_closure_check.py's header still claims "
            f"{header_sha}. Re-vendor from the new commit (verbatim, do not hand-edit) and "
            "update the header.",
            file=sys.stderr,
        )
        return 1

    print(f"in sync: both pin schematic commit {inner_sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
