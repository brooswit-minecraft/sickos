"""Generic SemVer 2.0 precedence comparison.

Extracted from bump-dynamic-atmosphere.py's parse_semver/compare_semver
(MINECRAFT-362) so the DA engine and the generalized multi-mod resolver
(scripts/modrinth_compat.py) use exactly one comparator for "is this
candidate version actually newer, or a downgrade/replay?" instead of two
copies that could drift. Pure, stdlib only, no I/O.

Precedence (semver.org 2.0, section 11):
- compare (major, minor, patch) numerically first;
- a version with no prerelease is newer than one with a prerelease that
  otherwise has the same (major, minor, patch);
- otherwise compare prerelease identifiers left to right, split on '.': a
  numeric identifier compares numerically, an alphanumeric identifier
  compares lexically (ASCII), a numeric identifier is always older than an
  alphanumeric one, and if all shared identifiers are equal, the
  prerelease with MORE identifiers is newer.
Build metadata (a "+..." suffix) is ignored for ordering, per semver's own
rule that build metadata never affects precedence.
"""

from __future__ import annotations

import re

_SEMVER_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


def parse_semver(version):
    match = _SEMVER_RE.match(version)
    if not match:
        return None
    core = (int(match.group("major")), int(match.group("minor")), int(match.group("patch")))
    prerelease = match.group("prerelease")
    identifiers = tuple(prerelease.split(".")) if prerelease else None
    return core, identifiers


def _identifier_key(identifier):
    return (0, int(identifier)) if identifier.isdigit() else (1, identifier)


def compare_semver(left, right):
    """Return -1, 0, or 1 comparing two version strings. Raises ValueError if unparsable."""
    left_parsed = parse_semver(left)
    right_parsed = parse_semver(right)
    if left_parsed is None or right_parsed is None:
        raise ValueError(f"cannot compare non-semver version(s): {left!r}, {right!r}")

    left_core, left_pre = left_parsed
    right_core, right_pre = right_parsed
    if left_core != right_core:
        return -1 if left_core < right_core else 1

    if left_pre is None and right_pre is None:
        return 0
    if left_pre is None:
        return 1
    if right_pre is None:
        return -1

    for left_id, right_id in zip(left_pre, right_pre):
        left_key = _identifier_key(left_id)
        right_key = _identifier_key(right_id)
        if left_key != right_key:
            return -1 if left_key < right_key else 1
    if len(left_pre) != len(right_pre):
        return -1 if len(left_pre) < len(right_pre) else 1
    return 0
