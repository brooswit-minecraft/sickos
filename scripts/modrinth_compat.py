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
only catches a candidate that declares another project `incompatible`
while that project is currently pinned -- an unambiguous, verifiable
conflict. It does NOT resolve `required`/`optional` dependencies against
what else is pinned (a full dependency-graph solve, including version
ranges and transitively-missing requirements), since Modrinth's own
dependency entries do not carry enough information for this pack to tell
a genuinely-missing requirement from one that is local-only/out-of-pack
by design (see `find_dependency_conflicts`'s docstring). That fuller
solve remains a documented gap, not silently assumed covered.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from semver_compare import compare_semver, parse_semver  # noqa: E402 - needs sys.path set up first

USER_AGENT = "brooswit-minecraft/sickos (MINECRAFT-362 mod auto-bump resolver)"


class CompatResolutionError(Exception):
    """Fetching or parsing Modrinth's version list for a project failed."""


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


def default_fetcher(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _version_to_candidate(version_json):
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
    )


def resolve_latest_compatible_version(mod_id, *, game_version, loader, fetcher):
    """Return the newest VersionCandidate compatible with (game_version, loader), or None.

    Modrinth's own `game_versions`/`loaders` query filter already does the
    compatibility narrowing server-side; this re-checks it client-side too
    (never trust a filter you did not verify) and picks the candidate with
    the highest version_number by semver precedence. A candidate whose
    version_number is not valid semver is skipped, not crashed on -- fail
    closed on that single candidate, not the whole resolution.
    """
    versions_json = fetch_project_versions(mod_id, game_version=game_version, loader=loader, fetcher=fetcher)
    if not isinstance(versions_json, list):
        raise CompatResolutionError(f"version list for {mod_id} was not a JSON array")

    best = None
    for entry in versions_json:
        if not isinstance(entry, dict):
            continue
        if game_version not in (entry.get("game_versions") or ()):
            continue
        if loader not in (entry.get("loaders") or ()):
            continue
        candidate = _version_to_candidate(entry)
        if candidate is None or not candidate.version_number:
            continue
        if parse_semver(candidate.version_number) is None:
            continue
        if best is None or compare_semver(candidate.version_number, best.version_number) > 0:
            best = candidate
    return best


def decide_update(pin, candidate, *, previous_version_number=None):
    """Compare an EligiblePin's current pin against a resolved VersionCandidate.

    Returns one of:
    - "no_candidate": Modrinth has no version matching (game_version, loader).
    - "already_pinned": candidate.version_id is exactly what's pinned (dedup
      -- a replay/retry of the same release, generalizing
      bump-dynamic-atmosphere.py's already-pinned no-op).
    - "downgrade_refused": `previous_version_number` was supplied and the
      candidate's version_number is not strictly newer than it by semver
      precedence (semver_compare.compare_semver).
    - "unparsable_version": `previous_version_number` was supplied but
      either it or the candidate's version_number is not valid semver --
      fail closed by refusing to call this an update, rather than guessing.
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
    try:
        comparison = compare_semver(candidate.version_number, previous_version_number)
    except ValueError:
        return "unparsable_version"
    return "downgrade_refused" if comparison <= 0 else "candidate"


def find_dependency_conflicts(candidate, pinned_mod_ids):
    """Check `candidate.dependencies` (Modrinth's own declared list, as
    returned by GET /v2/project/{id}/version) against what is currently
    pinned in the pack, identified only by mod id (project id).

    Returns a list of human-readable conflict strings; empty means no
    conflict found. Deliberately narrow, not a full dependency-graph
    solve (see this module's docstring): a Modrinth dependency entry
    names a project and sometimes an exact version id, never a semver
    *range* this pack could evaluate without also fetching whatever is
    pinned for every dependency, transitively -- that solve is out of
    scope. What IS unambiguous and checked here: a candidate that
    declares another project `incompatible` while that project is
    currently pinned in this pack is a real, verifiable conflict,
    regardless of version. A `required` dependency whose project is not
    currently pinned is NOT reported as a conflict -- Modrinth has no way
    to tell this pack whether that project is itself local-only/out of
    the public pack (like Herobrine API/Baritone) or genuinely missing,
    so treating every unmet `required` entry as fatal would produce false
    positives this pass cannot distinguish from true ones.
    """
    conflicts = []
    for dependency in candidate.dependencies:
        if not isinstance(dependency, dict):
            continue
        if dependency.get("dependency_type") != "incompatible":
            continue
        conflicting_project_id = dependency.get("project_id")
        if conflicting_project_id in pinned_mod_ids:
            conflicts.append(
                f"{candidate.version_number} declares project {conflicting_project_id!r} "
                "incompatible, and it is currently pinned in this pack"
            )
    return conflicts
