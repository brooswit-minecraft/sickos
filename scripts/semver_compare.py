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


CATEGORY_RANK = {"patch": 1, "minor": 2, "breaking": 3}


def _strip_game_version_prefix(version, game_version):
    """Some mods version their Modrinth releases as "<game_version>-
    <mod_version>" (e.g. "1.21.1-4.0.1") -- semver's own grammar then
    parses "1.21.1" as the CORE and "4.0.1" as a prerelease identifier,
    so the mod's own version lives entirely in the field this module
    otherwise ignores for delta purposes. Strip a leading
    "<game_version>-" (the pack's own pinned game_version, already known
    to every caller via mod_eligibility.read_pack_targets) so the
    REMAINING string -- the mod's own version -- is what gets classified
    (MINECRAFT-362 review, seventh round: without this,
    semver_delta_category's core never changes between releases for
    this scheme, so it returns None for every update, including a major
    one, confirmed live for Supplementaries/Moonlight Lib/Project:
    Vibrant Journeys/Create: Diesel Generators)."""
    if game_version and version.startswith(game_version + "-"):
        return version[len(game_version) + 1:]
    return version


def semver_delta_category(previous_version, candidate_version, *, game_version=None):
    """Classify an update by which (major, minor, patch) component
    changed between two version strings: major changed -> "breaking",
    else minor changed -> "minor", else patch changed -> "patch". Returns
    None if either string (after stripping a leading "<game_version>-",
    see `_strip_game_version_prefix`) is not valid semver, or if the
    (major, minor, patch) core is identical between the two (e.g. only a
    prerelease identifier changed, or build metadata) -- there is no
    component delta to classify in that case, not an error.

    Added (MINECRAFT-362 review, sixth round) because a changelog-text
    heading (modrinth_compat.derive_category_from_changelog) measured
    live against real Modrinth changelogs: 0 of 15 real candidates
    matched any heading (real changelogs are bullet lists, not "##
    Breaking"/"## Fixes" sections), so it was never the deciding factor
    in practice and a genuine breaking change announced in a bullet
    would silently default to minor. The version numbers themselves are
    an always-available signal a mod author already committed to."""
    previous_version = _strip_game_version_prefix(previous_version, game_version)
    candidate_version = _strip_game_version_prefix(candidate_version, game_version)
    previous_parsed = parse_semver(previous_version)
    candidate_parsed = parse_semver(candidate_version)
    if previous_parsed is None or candidate_parsed is None:
        return None
    (p_major, p_minor, p_patch), _ = previous_parsed
    (c_major, c_minor, c_patch), _ = candidate_parsed
    if c_major != p_major:
        return "breaking"
    if c_minor != p_minor:
        return "minor"
    if c_patch != p_patch:
        return "patch"
    return None


def more_severe_category(*categories):
    """Return whichever of `categories` ranks highest in CATEGORY_RANK
    (breaking > minor > patch), ignoring any that are None or not a
    known category. Returns None if none qualify. Used to let a
    changelog/explicit category ESCALATE a semver-delta classification
    (or vice versa) without ever letting either one downgrade the other
    (MINECRAFT-362 review, sixth round)."""
    ranked = [c for c in categories if c in CATEGORY_RANK]
    if not ranked:
        return None
    return max(ranked, key=lambda c: CATEGORY_RANK[c])
