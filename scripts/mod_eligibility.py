"""Resolve the eligible-for-auto-update set of packwiz pins (MINECRAFT-362).

The generalized "any managed mod's verified release updates the pack"
automation needs an explicit enrollment/mapping step before it can touch
anything. This module is that step: it is read-only (never writes a pin)
and is the single place both the scheduled-poll backstop and a future
per-mod dispatch handler should ask "is this pin in scope, and what
Modrinth project does it track?"

Enrollment mechanism: a packwiz pin under mods/*.pw.toml with an
[update.modrinth] table is, by that fact alone, already enrolled in
Modrinth-tracked auto-update -- mod-id/version are exactly the fields
packwiz itself uses to know what a pin tracks, so this reuses the pack's
own existing metadata instead of inventing a second parallel enrollment
file that could drift from it. A pin with an [update.github] table is
enrolled the same way, for the GitHub-release-asset identity mechanism
scripts/github_release_identity.py and scripts/bump_mod_github.py
implement -- using packwiz's OWN native [update.github] schema (`slug`,
`tag`, `branch`, `regex`; see github/updater.go in packwiz itself), not
an invented owner/repo pair. This repo's 7 real [update.github] pins
already use this native shape (confirmed by reading them directly), and
every one of them stores `branch` as a frozen, exact 40-hex commit SHA
rather than a mutable branch name -- MINECRAFT-362 review (comment
48415): verified against pinned packwiz revision dfd8b68a, packwiz's own
ghUpdater.CheckUpdate calls getLatestRelease(slug, branch), which --
whenever branch is non-empty -- filters for a release whose OWN
target_commitish equals that exact string and returns nothing otherwise.
A release's target_commitish is essentially never going to equal an
old, frozen SHA again, so bare `packwiz update` can never discover a new
release for any of these pins as configured; this module and
scripts/github_release_identity.py discover independently of `branch`
entirely (see discover_github_pins and resolve_latest_matching_release),
using it only as the write path's own record of exactly which source
commit the CURRENTLY pinned jar was built from -- the one property these
pins' authors clearly cared about enough to freeze. The ticket owner's
own decision on comment 47927 (scope) and comment 48415 (schema) of
MINECRAFT-362 was that these mods belong in THIS ticket's scope, reusing
the shared fail-closed gates against packwiz's real schema -- not a
separate ticket, and not an invented one.

Three explicit kinds of exclusion/enrollment, all read from this one
place:
- EXCLUDED_MOD_NAMES: local-only addons that must never be pulled into the
  public pack (Herobrine API, Baritone -- MINECRAFT-362's own requirement).
  Neither currently has a pin under mods/ at all (confirmed by inspecting
  this repo directly), so this list is defence in depth: if one were ever
  mistakenly added as a pin, discover_eligible_pins excludes it by name
  rather than silently enrolling it.
- Pins with an [update.github] table are GitHub-release-tracked (see
  discover_github_pins below), not reported as untracked.
- Pins with neither table are not tracked by ANY mechanism this module
  knows about, so they are reported separately (see UNTRACKED_KIND)
  rather than silently dropped. This is a snapshot of mods/ at the time
  of writing, not a guarantee -- call discover_eligible_pins() and read
  its own `untracked` list rather than trusting a number in a docstring
  to stay current (MINECRAFT-362 review, ninth round: a prior version of
  this docstring undercounted this by half).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

EXCLUDED_MOD_NAMES = frozenset({"Herobrine API", "Baritone"})

MODRINTH_KIND = "modrinth"
UNTRACKED_KIND = "untracked"


@dataclass(frozen=True)
class EligiblePin:
    pin_path: str  # relative to repo root, e.g. "mods/dynamic-atmosphere.pw.toml"
    name: str
    side: str
    mod_id: str
    pinned_version_id: str


@dataclass(frozen=True)
class UntrackedPin:
    pin_path: str
    name: str
    reason: str


@dataclass(frozen=True)
class GithubPin:
    pin_path: str  # relative to repo root
    name: str
    side: str
    slug: str  # "owner/repo", packwiz's own [update.github].slug field verbatim
    pinned_tag: str
    pinned_source: str  # [update.github].branch verbatim -- see this module's own
    # docstring: in every real pin today this is an exact, frozen 40-hex commit SHA,
    # not a mutable branch name, and is NEVER used to filter discovery (that would
    # reproduce packwiz's own freeze bug) -- only written back by the bump engine as
    # a record of which source commit the CURRENTLY pinned jar was built from.
    regex: str  # [update.github].regex verbatim -- the asset-name filter
    pinned_url: str  # [download].url verbatim -- the on-disk artifact identity
    pinned_hash: str  # [download].hash verbatim
    pinned_hash_format: str  # [download].hash-format verbatim -- "sha256" on every
    # real pin this engine has ever written, but an older/human-authored pin may
    # still carry a different format (e.g. this pack's own dynamic-despawn pin was
    # "sha512" before this engine ever touched it) -- scripts/bump_mod_github.py's
    # own identity-conflict check only compares the hash when this is "sha256",
    # the one format GitHub's own asset digest can be compared against directly.


def discover_eligible_pins(repo_root: Path) -> tuple[list[EligiblePin], list[UntrackedPin]]:
    """Scan mods/*.pw.toml and split it into (eligible, untracked).

    `eligible` pins have an [update.modrinth] table and are not in
    EXCLUDED_MOD_NAMES. `untracked` pins exist under mods/ but have no
    tracking mechanism this module knows about at all -- callers must
    report these as a gap, never silently skip them. A pin with an
    [update.github] table is tracked too (see discover_github_pins
    below), so it is excluded from BOTH lists here, not reported as
    untracked -- MINECRAFT-362 review, twenty-second round.
    """
    mods_dir = Path(repo_root) / "mods"
    eligible: list[EligiblePin] = []
    untracked: list[UntrackedPin] = []

    for pin_path in sorted(mods_dir.glob("*.pw.toml")):
        relative = f"mods/{pin_path.name}"
        data = tomllib.loads(pin_path.read_text(encoding="utf-8"))
        name = data.get("name", pin_path.stem)

        if name in EXCLUDED_MOD_NAMES:
            untracked.append(UntrackedPin(relative, name, "excluded local-only mod"))
            continue

        update = data.get("update", {}) if isinstance(data.get("update"), dict) else {}
        modrinth = update.get("modrinth")
        if isinstance(modrinth, dict) and modrinth.get("mod-id"):
            eligible.append(
                EligiblePin(
                    pin_path=relative,
                    name=name,
                    side=data.get("side", "both"),
                    mod_id=modrinth["mod-id"],
                    pinned_version_id=modrinth.get("version", ""),
                )
            )
            continue

        github = update.get("github")
        if isinstance(github, dict) and github.get("slug"):
            continue  # tracked -- see discover_github_pins

        untracked.append(UntrackedPin(relative, name, "no [update.modrinth]/[update.github] table; untracked"))

    return eligible, untracked


def discover_github_pins(repo_root: Path) -> list[GithubPin]:
    """Scan mods/*.pw.toml for pins with an [update.github] table (native
    packwiz schema -- `slug`, `tag`, `branch`, `regex`; see this module's
    own docstring): the GitHub-release-asset analogue of
    discover_eligible_pins' Modrinth path. Deliberately a SEPARATE
    function, not a third element of discover_eligible_pins' return
    tuple -- that tuple's shape is a stable contract scripts/bump_mod.py's
    CLI and scripts/poll-mod-updates.py's Modrinth loop already depend
    on, and there is no reason a Modrinth-only caller should need to
    learn about or ignore a third list it never uses.

    `tag` and `branch` may legitimately be absent/empty on a pin that has
    never been through this engine before (MINECRAFT-362 review, comment
    48415's "add verified tracking for Despawn/MagicaVoxel/Battle Axes"):
    enrollment only requires `slug`, matching packwiz's own ParseUpdate,
    which has no required-field validation beyond what mapstructure
    gives it for free. A missing `regex` also enrolls (packwiz allows it,
    though an empty pattern would behave as "matches every asset name" if
    actually compiled) -- scripts/github_release_identity.py's own asset
    matcher fails closed with a clear message if `regex` is empty or not
    the one shape it understands, rather than silently matching
    everything."""
    mods_dir = Path(repo_root) / "mods"
    github_pins: list[GithubPin] = []
    for pin_path in sorted(mods_dir.glob("*.pw.toml")):
        relative = f"mods/{pin_path.name}"
        data = tomllib.loads(pin_path.read_text(encoding="utf-8"))
        name = data.get("name", pin_path.stem)
        if name in EXCLUDED_MOD_NAMES:
            continue
        update = data.get("update", {}) if isinstance(data.get("update"), dict) else {}
        github = update.get("github")
        if not isinstance(github, dict) or not github.get("slug"):
            continue
        download = data.get("download", {}) if isinstance(data.get("download"), dict) else {}
        github_pins.append(
            GithubPin(
                pin_path=relative,
                name=name,
                side=data.get("side", "both"),
                slug=github["slug"],
                pinned_tag=github.get("tag", ""),
                pinned_source=github.get("branch", ""),
                regex=github.get("regex", ""),
                pinned_url=download.get("url", ""),
                pinned_hash=download.get("hash", ""),
                pinned_hash_format=download.get("hash-format", ""),
            )
        )
    return github_pins


def read_pack_targets(repo_root):
    """The pack's own pinned (game_version, loader) -- the single
    compatibility target every caller (the scheduled poll, and
    scripts/bump_mod.py's manual CLI) must check a candidate against
    before writing it. pack.toml's [versions] table keys the loader's
    own name ("neoforge") to its version, alongside "minecraft" -- the
    loader is just "whichever key isn't minecraft", not a convention
    this needs to hardcode.

    Fails loudly, not silently, if pack.toml's [versions] table does not
    have this expected shape: a silent (None, None) here would make every
    compatibility check compare against `None`, which never equals
    anything, so a caller would quietly resolve nothing while reporting a
    clean "no_candidate" -- indistinguishable from a genuinely quiet run
    (MINECRAFT-362 review). Previously duplicated between
    scripts/poll-mod-updates.py and scripts/bump_mod.py's CLI; moved here
    so there is one implementation both call, not two that could drift
    (MINECRAFT-362 review, fourth round)."""
    pack_toml = Path(repo_root) / "pack.toml"
    import tomllib
    with pack_toml.open("rb") as handle:
        data = tomllib.load(handle)
    versions = data.get("versions", {})
    game_version = versions.get("minecraft")
    loader_names = [key for key in versions if key != "minecraft"]
    if not game_version or len(loader_names) != 1:
        raise ValueError(
            "pack.toml's [versions] table is not the expected shape (exactly 'minecraft' plus "
            f"one loader key); got {sorted(versions)!r}. Refusing to proceed with an "
            "unverifiable compatibility target."
        )
    return game_version, loader_names[0]
