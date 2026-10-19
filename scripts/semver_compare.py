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

# Four confirmed-live, NARROW native version shapes real enrolled mods
# publish on Modrinth that strict SemVer 2.0 (_SEMVER_RE above) rejects
# outright (MINECRAFT-421 review, point 2). Each transform below is
# structural -- padding a missing component or relocating an existing
# one -- never an invented ordering; see normalize_native_version_scheme's
# own docstring for the full justification and the one shape (a bare
# trailing letter suffix, Create: Easy Structures' "0.2a") this
# deliberately does NOT touch.
_NATIVE_SCHEME_RE = re.compile(
    r"^[vV]?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?:\.(?P<extra>\d+))?"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


def normalize_native_version_scheme(version):
    """Rewrite a handful of SPECIFIC, source-confirmed non-strict-SemVer
    native version schemes real enrolled mods publish into an equivalent
    SemVer 2.0 string `parse_semver` already understands, preserving
    ordering within that mod's own version history. Returns `version`
    UNCHANGED if none of the following narrow shapes match -- this is
    normalization, never a guess: an input this function does not
    recognise is passed straight through for `parse_semver` to reject
    exactly as before this round (MINECRAFT-421 review, point 2). Only
    ever consulted by `parse_semver` itself, as a fallback AFTER the
    strict `_SEMVER_RE` match already failed -- so a string that is
    already valid SemVer is never touched by this function at all.

    Confirmed-live shapes (mod -- Modrinth version_number -- transform):
    - A leading "v"/"V" immediately before the first digit (Dungeons and
      Taverns "v4.4.4+mod" -> "4.4.4+mod"): stripped, the same convention
      scripts/github_release_identity.py's `_tag_to_version_number`
      already uses for a GitHub release tag.
    - Exactly two numeric components, with or without its own
      "-prerelease" (Create: Dynamic Village "0.5", Create: Pillager
      Arise "132.36", Geophilic "3.7", IceAndFire Community Edition
      "2.1-beta.1"): a missing patch component is 0 ("0.5" -> "0.5.0",
      "2.1-beta.1" -> "2.1.0-beta.1") -- SemVer always has a patch; these
      authors simply never increment one, which is a padding fact, not
      an invented ordering.
    - Exactly four numeric components (Biomes O' Plenty "21.1.0.14",
      GlitchCore "2.1.0.2"): the fourth component is folded into the
      SemVer PRERELEASE slot as a single numeric identifier
      ("21.1.0.14" -> "21.1.0-14"). This is a real ordering transform,
      not only padding -- justified because every real candidate on this
      scheme for these two pins carries all four components (never
      3-only), so every comparison this engine ever performs for either
      pin has a mapped prerelease on BOTH sides, which keeps SemVer's
      "no-prerelease beats any prerelease" asymmetry from ever
      triggering here; within that pair, a single numeric prerelease
      identifier already compares numerically per SemVer precedence
      (semver.org 2.0, 11.4.1), which is exactly the ordering a 4th
      dotted-numeric component is meant to have. If a 4th component AND
      its own "-prerelease" both appear (a shape no real pin has shown),
      this refuses to guess which one should win and returns `version`
      unchanged, same as any other unrecognised shape. That "both sides
      mapped" precondition is NOT guaranteed just because this function
      ran on one string in isolation -- `compare_semver` carries its own
      separate guard (MINECRAFT-421 review, point 3) that refuses to
      compare a raw four-component string against a differently-shaped
      one at all, rather than letting this fold invent precedence
      against a scheme it was never confirmed to apply to.

    Deliberately NOT handled, left genuinely ambiguous (MINECRAFT-421
    review, point 2): Create: Easy Structures' pinned "0.2a" -- a bare
    trailing letter with no separator before it has no single,
    source-backed meaning this module can assign without guessing (an
    alpha/pre-release build? the author's own "second build of 0.2"
    convention? Modrinth's own listing does not say). Inventing a
    lexical/date ordering for it was explicitly rejected by review; it
    stays NoParseableCandidateError/unsupported_version_scheme until the
    mod author's own documentation (or a correction from them) resolves
    what the suffix means. `_NATIVE_SCHEME_RE` requires every character
    after the numeric components to be one of "-prerelease"/"+build",
    so "0.2a" (no separator before "a") never matches it."""
    match = _NATIVE_SCHEME_RE.match(version)
    if not match:
        return version
    major, minor = match.group("major"), match.group("minor")
    patch, extra = match.group("patch"), match.group("extra")
    prerelease, build = match.group("prerelease"), match.group("build")
    if extra is not None:
        if prerelease is not None:
            # No confirmed-live pin has both a 4th numeric component and
            # its own "-prerelease" -- refuse to guess which one wins.
            return version
        prerelease = extra
    result = f"{major}.{minor}.{patch or '0'}"
    if prerelease is not None:
        result += f"-{prerelease}"
    if build is not None:
        result += f"+{build}"
    return result


def parse_semver(version):
    match = _SEMVER_RE.match(version)
    if not match:
        normalized = normalize_native_version_scheme(version)
        if normalized != version:
            match = _SEMVER_RE.match(normalized)
    if not match:
        return None
    core = (int(match.group("major")), int(match.group("minor")), int(match.group("patch")))
    prerelease = match.group("prerelease")
    identifiers = tuple(prerelease.split(".")) if prerelease else None
    return core, identifiers


def _identifier_key(identifier):
    return (0, int(identifier)) if identifier.isdigit() else (1, identifier)


# Exactly 4 dotted numeric components, nothing else (the RAW shape, before
# normalize_native_version_scheme folds the 4th into a prerelease slot) --
# e.g. Biomes O' Plenty "21.1.0.14". Used ONLY by compare_semver's own
# cross-scheme guard below (MINECRAFT-421 review, point 3): the fold
# compares two SAME-scheme four-component strings correctly against each
# other, but comparing a folded four-component value against a
# DIFFERENTLY-shaped one (e.g. a plain "2.1.0") would silently invent
# precedence this module has no evidence for -- confirmed live: "2.1.0.2"
# folds to "2.1.0-2", which SemVer's own "no prerelease beats any
# prerelease" rule then ranks OLDER than a plain "2.1.0", even though
# nothing establishes that a bare 3-component string and this mod's own
# 4-component scheme are the same ordering at all. Refusing that
# comparison (ValueError, the same outcome an entirely-unparsable string
# already produces) is a KNOWN, deliberate limitation, not a bug: there is
# no real pin where the two schemes are legitimately compared against each
# other today.
_RAW_FOUR_COMPONENT_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def compare_semver(left, right):
    """Return -1, 0, or 1 comparing two version strings. Raises ValueError if unparsable."""
    left_is_four_component = bool(_RAW_FOUR_COMPONENT_RE.match(left))
    right_is_four_component = bool(_RAW_FOUR_COMPONENT_RE.match(right))
    if left_is_four_component != right_is_four_component:
        raise ValueError(
            f"refusing to compare a four-numeric-component native version against a "
            f"differently-shaped one -- no evidence either scheme's precedence applies to the "
            f"other: {left!r}, {right!r}"
        )

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


def strip_known_version_prefixes(version, game_version=None, loader=None):
    """Some mods version their Modrinth releases with the game version
    and/or loader name baked into the string as a leading component --
    e.g. "1.21.1-4.0.1" (game version only), "1.21.1-NeoForge-5.1.9"
    (game version then loader, the whole YUNG's family), or
    "neoforge-1.21.1-3.1.7" (loader then game version, Cristel Lib).
    semver's own grammar then parses the first numeric-looking component
    as the CORE and everything after as a single prerelease identifier,
    so the mod's own version lives entirely in a field this module
    otherwise ignores for delta purposes -- strip every leading
    "<component>-" that case-insensitively matches the pack's own pinned
    game_version or loader name (both already known to every caller via
    mod_eligibility.read_pack_targets), in any order, repeating until
    neither matches, so the REMAINING string -- the mod's own version --
    is what gets classified (MINECRAFT-362 review: seventh round added
    the game_version-only case; eighth round generalized it to also
    strip a loader component, after measuring live that the "<game_
    version>-<loader>-<mod_version>"/"<loader>-<game_version>-<mod_
    version>" orderings cover 12 more real pins -- the whole YUNG's
    family plus Cristel Lib -- for which the game_version-only strip
    left a non-semver remainder, e.g. "NeoForge-5.1.9", so
    semver_delta_category returned None for every update on this scheme
    too, including a major one)."""
    prefixes = [p for p in (game_version, loader) if p]
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if version.lower().startswith(prefix.lower() + "-"):
                version = version[len(prefix) + 1:]
                changed = True
    return version


def semver_delta_category(previous_version, candidate_version, *, game_version=None, loader=None):
    """Classify an update by which (major, minor, patch) component
    changed between two version strings: major changed -> "breaking",
    else minor changed -> "minor", else patch changed -> "patch". Returns
    None if either string (after stripping any leading game-version/
    loader component, see `strip_known_version_prefixes`) is not valid
    semver, or if the (major, minor, patch) core is identical between
    the two (e.g. only a prerelease identifier changed, or build
    metadata) -- there is no component delta to classify in that case,
    not an error.

    Added (MINECRAFT-362 review, sixth round) because a changelog-text
    heading (modrinth_compat.derive_category_from_changelog) measured
    live against real Modrinth changelogs: 0 of 15 real candidates
    matched any heading (real changelogs are bullet lists, not "##
    Breaking"/"## Fixes" sections), so it was never the deciding factor
    in practice and a genuine breaking change announced in a bullet
    would silently default to minor. The version numbers themselves are
    an always-available signal a mod author already committed to."""
    previous_version = strip_known_version_prefixes(previous_version, game_version, loader)
    candidate_version = strip_known_version_prefixes(candidate_version, game_version, loader)
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
