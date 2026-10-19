"""Resolve whether an enrolled mod has a newer, compatible Modrinth release.

This is the scheduled-reconciliation half of MINECRAFT-362's generalized
"any managed mod's verified release updates the pack" automation: given
one EligiblePin (scripts/mod_eligibility.py), ask Modrinth's version list
for that project, and decide whether there is a version that is both
compatible (declares the pinned game version and loader) and strictly
newer than what's pinned.

This module only *decides*; it never writes a pin, downloads a jar, or
runs a gate -- that remains scripts/bump-dynamic-atmosphere.py's job for
Dynamic Atmosphere today. Reaching the same decision for every mod, not
only DA, is tracked as the still-open "generalized write path" gap (see
docs/mod-auto-bump.md's "Status" section) -- this module exists so the
decision logic (compatibility + no-downgrade + dedup) is ready and
unit-tested ahead of that write path landing, and so the DA engine's own
identity/hash gate (modrinth_metadata.py) and no-downgrade comparator
(semver_compare.py) are exactly what a resolved candidate would be
verified against -- not a second, divergent implementation of either.

Dependency validation here is intentionally narrow: `find_dependency_conflicts`
catches only a candidate that declares another project `incompatible`
while that project is currently pinned -- the one thing Modrinth's own
version-level metadata can answer with no guessing, since NeoForge's own
per-jar manifest format has no `incompatible` declaration at all. It
used to also compare a `required` dependency's exact version_id
(seventeenth review round) and, briefly, refuse ANY unpinned `required`
dependency as unverifiable (eighteenth review round) -- both reverted in
the twenty-first round after the second produced a confirmed false
positive: Modrinth's `dependencies` array is a project-WIDE author
declaration, not loader-scoped, so a NeoForge build inherits whatever a
Fabric build of the same version happens to declare (Cristel Lib
declaring Cloth Config API `required` for a dependency the actual
NeoForge jar does not have). Required-dependency satisfaction is now
checked exclusively by `scripts/dependency_closure_check.py` (vendored
from brooswit-minecraft/schematic, the same checker `ci.yml`'s own
`dependency-check` job already trusts), run as a GATE against the
GENERATED tree's real jars after `build`, on both write paths -- see
`scripts/bump-dynamic-atmosphere.py`'s and `scripts/bump_mod.py`'s own
`default_gate_commands`/`run_engine`. Reading real
`META-INF/neoforge.mods.toml` manifests has no loader-mismatch
false-positive class and catches both a missing requirement and a
version-range mismatch, strictly superseding what the Modrinth-metadata
comparison attempted. A full dependency-GRAPH solve (transitive
requirements beyond one level) remains out of scope for both checks --
a documented gap, not silently assumed covered.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from semver_compare import (  # noqa: E402 - needs sys.path set up first
    compare_semver, parse_semver, strip_known_version_prefixes,
)

USER_AGENT = "brooswit-minecraft/sickos (MINECRAFT-362 mod auto-bump resolver)"


class CompatResolutionError(Exception):
    """Fetching or parsing Modrinth's version list for a project failed --
    the TRUE catch-all/systemic class (HTTP 5xx, timeout, DNS failure,
    malformed response, expired token, a project genuinely deleted/
    privated). scripts/poll-mod-updates.py's _is_expected_gap
    allowlists this by mod_id (KNOWN_ANONYMOUS_404_MOD_IDS) rather than
    as a whole class, specifically so this constructor stays reserved
    for the unexpected case -- the two subclasses below are each a
    PERMANENT, per-pin, deterministically-reproducible property instead
    (MINECRAFT-362 review, tenth round), and are classified as expected
    gaps unconditionally, never through that allowlist."""


class NoParseableCandidateError(CompatResolutionError):
    """At least one otherwise-compatible RELEASE entry exists for this
    project, but none had a parseable version_number even after
    stripping any leading game-version/loader component -- e.g. the
    whole version list uses some other, undocumented scheme. Distinct
    from returning None (which means "no compatible entry exists at
    all," the only case that is genuinely "nothing newer upstream") and
    distinct from the base CompatResolutionError (this is a permanent,
    deterministic per-pin property, not a transient/systemic failure)."""


class OnlyPrereleaseAvailableError(CompatResolutionError):
    """At least one otherwise-compatible entry exists for this project,
    but every one of them has `version_type != "release"` -- e.g. the
    mod has only ever published a beta/alpha build for this (game_version,
    loader). Distinct from returning None for the same reason as
    NoParseableCandidateError above: this is not "nothing newer
    upstream," it is "nothing RELEASE-grade upstream," a different,
    equally permanent and deterministic per-pin fact with a different
    remedy (wait for the author to publish a release; a human bump
    cannot fix it, unlike NoParseableCandidateError's pins)."""


@dataclass(frozen=True)
class VersionCandidate:
    version_id: str
    version_number: str
    file_name: str
    sha1: str
    sha512: str
    download_url: str
    game_versions: tuple = field(default_factory=tuple)
    loaders: tuple = field(default_factory=tuple)
    dependencies: tuple = field(default_factory=tuple)
    version_type: str = ""
    changelog: str = ""


def fetch_project_versions(mod_id, *, game_version, loader, fetcher):
    """Call GET /v2/project/{id}/version filtered server-side by Modrinth.

    `fetcher` is injected exactly like bump-dynamic-atmosphere.py's
    `fetcher` parameter, so tests never touch the network.
    """
    query = urllib.parse.urlencode(
        {"game_versions": json.dumps([game_version]), "loaders": json.dumps([loader])}
    )
    url = f"https://api.modrinth.com/v2/project/{urllib.parse.quote(mod_id, safe='')}/version?{query}"
    try:
        return fetcher(url)
    except urllib.error.HTTPError as error:
        raise CompatResolutionError(f"version list lookup for {mod_id} returned HTTP {error.code}") from error
    except Exception as error:  # noqa: BLE001 - any lookup failure is a resolution failure, not a crash
        raise CompatResolutionError(f"version list lookup for {mod_id} failed: {error!r}") from error


def make_fetcher(token=None):
    """Build a fetcher closure for `fetch_project_versions`/
    `resolve_latest_compatible_version`, optionally attaching
    MODRINTH_TOKEN as an Authorization header.

    Without this, a caller that has a token has no way to get it onto the
    version-LIST request: Modrinth will not resolve some projects (e.g.
    unlisted/private ones) to an anonymous caller at all, so a resolution
    that never sends the token can fail before the token-gated metadata
    lookup downstream ever gets a chance to help (MINECRAFT-362 review).
    """

    def fetcher(url):
        headers = {"User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = token
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    return fetcher


default_fetcher = make_fetcher()


def version_candidate_from_json(version_json):
    """Build a VersionCandidate from one raw Modrinth version object (as
    returned by both `GET /v2/project/{id}/version` -- one entry -- and
    `GET /v2/version/{id}` -- the whole response), carrying its REAL
    game_versions/loaders/dependencies/version_type/changelog, not empty
    defaults. Public (not `_`-prefixed) specifically so a caller that
    only has a version id (e.g. scripts/bump_mod.py's CLI) can fetch
    `GET /v2/version/{id}` and build a fully-populated candidate from it,
    the same way resolve_latest_compatible_version does from the list
    endpoint -- rather than constructing a VersionCandidate by hand from
    manually-transcribed flags, which has no compatibility or dependency
    data at all (MINECRAFT-362 review, fourth round)."""
    files = version_json.get("files")
    if not isinstance(files, list) or not files:
        return None
    # Modrinth marks exactly one file primary per version when there is a
    # choice (e.g. multiple loader jars); fall back to the first entry.
    primary = next((f for f in files if f.get("primary")), files[0])
    hashes = primary.get("hashes") if isinstance(primary.get("hashes"), dict) else {}
    return VersionCandidate(
        version_id=version_json.get("id"),
        version_number=version_json.get("version_number"),
        file_name=primary.get("filename"),
        sha1=str(hashes.get("sha1", "")).lower(),
        sha512=str(hashes.get("sha512", "")).lower(),
        download_url=primary.get("url"),
        game_versions=tuple(version_json.get("game_versions") or ()),
        loaders=tuple(version_json.get("loaders") or ()),
        dependencies=tuple(version_json.get("dependencies") or ()),
        version_type=str(version_json.get("version_type") or ""),
        changelog=str(version_json.get("changelog") or ""),
    )


def resolve_latest_compatible_version(mod_id, *, game_version, loader, fetcher):
    """Return the newest VersionCandidate compatible with (game_version, loader), or None.

    Modrinth's own `game_versions`/`loaders` query filter already does the
    compatibility narrowing server-side; this re-checks it client-side too
    (never trust a filter you did not verify) and picks the candidate with
    the highest version_number by semver precedence -- after stripping any
    leading game-version/loader component each candidate's OWN
    version_number carries (`semver_compare.strip_known_version_prefixes`),
    not the raw string. Without this normalization, a mod that bakes its
    game version and/or loader into its version string (e.g. Xaero's
    Minimap/World Map and Cristel Lib: "neoforge-1.21.1-26.6.0") would
    have every one of its real candidates skipped as unparsable, AND --
    the sharper failure mode -- if even one entry's raw string happens to
    parse as semver by coincidence, ordering against un-normalized
    strings can select a genuine downgrade (MINECRAFT-362 review, ninth
    round: confirmed live for both Xaero's pins, each permanently
    reporting a clean "no_candidate" today with 22-26 real compatible
    releases available, and for Cristel Lib, where the un-normalized
    comparison previously selected an older version as "best").

    Only `version_type == "release"` entries are considered: Modrinth's
    `version_type` ("release"/"beta"/"alpha") is independent of
    `version_number`, so a prerelease that happens to sort higher by
    semver (e.g. "1.3.0-beta.1" > "1.2.0") must not be auto-pinned into
    the public pack ahead of its own eventual full release
    (MINECRAFT-362 review).

    `None` means ONLY "no entry declaring (game_version, loader)
    compatibility exists at all for this project" -- genuinely nothing
    upstream to consider. Two related-but-distinct situations instead
    raise their own exception (MINECRAFT-362 review, tenth round: a
    prior round made the first of these distinct from `None`, but wired
    it to the wrong outcome class on the caller side -- see
    scripts/poll-mod-updates.py's _is_expected_gap):

    - `NoParseableCandidateError`: at least one compatible RELEASE entry
      exists, but none had a parseable version_number after
      normalization -- a permanent, deterministic per-pin property
      (some other, undocumented version scheme), not "nothing newer."
    - `OnlyPrereleaseAvailableError`: at least one compatible entry
      exists, but every one has `version_type != "release"` -- this
      project has never published a release-grade build for this
      (game_version, loader), also permanent and deterministic, and
      with a different remedy than the above (nothing a human bump can
      fix; only the mod author publishing a release helps).
    """
    versions_json = fetch_project_versions(mod_id, game_version=game_version, loader=loader, fetcher=fetcher)
    if not isinstance(versions_json, list):
        raise CompatResolutionError(f"version list for {mod_id} was not a JSON array")

    best = None
    best_normalized = None
    unparsable_count = 0
    prerelease_only_count = 0
    for entry in versions_json:
        if not isinstance(entry, dict):
            continue
        if game_version not in (entry.get("game_versions") or ()):
            continue
        if loader not in (entry.get("loaders") or ()):
            continue
        # Compatible by (game_version, loader) from here on -- now check
        # version_type, so a prerelease-only project can be told apart
        # from a project with no compatible entry at all.
        if entry.get("version_type") != "release":
            prerelease_only_count += 1
            continue
        candidate = version_candidate_from_json(entry)
        if candidate is None or not candidate.version_number:
            continue
        normalized = strip_known_version_prefixes(candidate.version_number, game_version, loader)
        if parse_semver(normalized) is None:
            unparsable_count += 1
            continue
        if best is None or compare_semver(normalized, best_normalized) > 0:
            best = candidate
            best_normalized = normalized

    if best is not None:
        return best
    if unparsable_count > 0:
        raise NoParseableCandidateError(
            f"{unparsable_count} compatible release version(s) for {mod_id} exist but none had "
            "a parseable version_number after stripping any leading game-version/loader "
            "component -- refusing to report this as 'nothing newer upstream'"
        )
    if prerelease_only_count > 0:
        raise OnlyPrereleaseAvailableError(
            f"{prerelease_only_count} compatible version(s) for {mod_id} exist but none has "
            "version_type == 'release' -- refusing to report this as 'nothing newer upstream'"
        )
    return None


_BREAKING_RE = re.compile(r"^#{1,6}\s*breaking\b", re.IGNORECASE | re.MULTILINE)
_FEATURE_RE = re.compile(r"^#{1,6}\s*features?\b", re.IGNORECASE | re.MULTILINE)
_FIX_RE = re.compile(r"^#{1,6}\s*fix(?:e[sd])?\b", re.IGNORECASE | re.MULTILINE)


def derive_category_from_changelog(changelog):
    """Best-effort `fix`/`feature`/`breaking` category derivation from a
    Modrinth version's own changelog text (AC4), for the generalized poll
    path -- Dynamic Atmosphere's dispatch path instead receives an
    explicit category in its trigger payload (docs/da-auto-bump.md), which
    has no equivalent here since nothing dispatches a poll run.

    Looks ONLY for a conventional "## Breaking" / "## Feature(s)" /
    "## Fix(es)" style heading, case-insensitive -- deliberately not a
    free-text phrase match on e.g. "breaking change(s)": that matched "No
    breaking changes." as breaking (MINECRAFT-362 review), since a bare
    substring search cannot tell a heading announcing a breaking change
    apart from a sentence denying one. The most severe match wins when
    more than one heading is present (a changelog that mixes fixes with a
    breaking change must still be treated as breaking, not patch). Returns
    None -- "unknown", per AC4 -- when no such heading is found, rather
    than guessing from arbitrary free text; the caller then defaults to
    minor and says so in the generated notes.
    """
    if not changelog:
        return None
    if _BREAKING_RE.search(changelog):
        return "breaking"
    if _FEATURE_RE.search(changelog):
        return "minor"
    if _FIX_RE.search(changelog):
        return "patch"
    return None


def decide_update(pin, candidate, *, previous_version_number=None, game_version=None, loader=None):
    """Compare an EligiblePin's current pin against a resolved VersionCandidate.

    Returns one of:
    - "no_candidate": Modrinth has no version matching (game_version, loader).
    - "already_pinned": candidate.version_id is exactly what's pinned (dedup
      -- a replay/retry of the same release, generalizing
      bump-dynamic-atmosphere.py's already-pinned no-op).
    - "downgrade_refused": `previous_version_number` was supplied and the
      candidate's version_number is not strictly newer than it by semver
      precedence (semver_compare.compare_semver), both normalized first
      via `semver_compare.strip_known_version_prefixes(game_version,
      loader)` -- without this, a mod that bakes its game version/loader
      into its version string compares raw, mis-parsed strings
      (MINECRAFT-362 review, ninth round).
    - "unparsable_version": `previous_version_number` was supplied but
      either it or the candidate's version_number (after normalization)
      is not valid semver -- fail closed by refusing to call this an
      update, rather than guessing.
    - "candidate": a real, newer (or not-yet-compared), compatible update.

    `previous_version_number` is optional because the only thing a pin
    file itself reliably carries is the previous Modrinth version ID, not
    its semver string (see scripts/mod_eligibility.py's EligiblePin) --
    dedup alone (by version_id) is always available; the stronger
    no-downgrade check additionally needs a caller that has looked up the
    previous version_number (e.g. from the engine's own prior-pin read, as
    bump-dynamic-atmosphere.py's load_da_pin does for DA specifically).
    """
    if candidate is None:
        return "no_candidate"
    if candidate.version_id == pin.pinned_version_id:
        return "already_pinned"
    if previous_version_number is None:
        return "candidate"
    normalized_candidate = strip_known_version_prefixes(candidate.version_number, game_version, loader)
    normalized_previous = strip_known_version_prefixes(previous_version_number, game_version, loader)
    try:
        comparison = compare_semver(normalized_candidate, normalized_previous)
    except ValueError:
        return "unparsable_version"
    return "downgrade_refused" if comparison <= 0 else "candidate"


def find_dependency_conflicts(candidate, pinned_versions_by_mod_id):
    """Check `candidate.dependencies` (Modrinth's own declared list, as
    returned by GET /v2/project/{id}/version) against what is currently
    pinned in the pack. `pinned_versions_by_mod_id` maps mod_id ->
    pinned_version_id for every currently-ELIGIBLE pin (a plain
    iterable of mod_ids also works here, since this function only ever
    checks membership, never a version value, any more -- see below).

    Returns a list of human-readable conflict strings; empty means no
    conflict found. Checks exactly ONE thing: a candidate that declares
    another project `incompatible` while that project is currently
    pinned in this pack is a real, verifiable conflict, regardless of
    version -- Modrinth's own data answers this with no guessing, and
    nothing else can check it (NeoForge's own per-jar manifest format
    has no `incompatible` declaration at all -- see
    scripts/dependency_closure_check.py's `read_mods()`, which only ever
    looks at `required` entries).

    A `required` dependency -- at an exact version_id or not, pinned or
    not -- is deliberately NOT checked here any more (MINECRAFT-362
    review, twenty-first round, reverting the seventeenth and
    eighteenth rounds' additions). Both of those additions compared
    Modrinth's version-level `dependencies` array, which is a
    project-WIDE author declaration, not loader-scoped -- a NeoForge
    build inherits whatever a Fabric build (or any other loader's
    build) of the SAME version happens to declare, with no way for this
    function to tell the two apart. That produced a confirmed false
    positive (Cristel Lib declaring Cloth Config API `required`, a
    Fabric-only need the actual NeoForge jar does not have) that
    permanently excluded a healthy pin from auto-bumps. Required-
    dependency satisfaction (both "missing entirely" and "present but
    wrong version") is now checked exclusively by
    scripts/dependency_closure_check.py, run as a GATE against the
    GENERATED tree's real jars (the actual NeoForge
    `META-INF/neoforge.mods.toml` manifest, not Modrinth's loader-
    agnostic metadata) -- strictly more accurate, with no loader-
    mismatch false-positive class, and already the gate this pack's own
    CI trusts for exactly this question.
    """
    conflicts = []
    for dependency in candidate.dependencies:
        if not isinstance(dependency, dict):
            continue
        if dependency.get("dependency_type") != "incompatible":
            continue
        project_id = dependency.get("project_id")
        if project_id in pinned_versions_by_mod_id:
            conflicts.append(
                f"{candidate.version_number} declares project {project_id!r} "
                "incompatible, and it is currently pinned in this pack"
            )
    return conflicts
