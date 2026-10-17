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
file that could drift from it.

Two explicit kinds of exclusion, both read from this one place:
- EXCLUDED_MOD_NAMES: local-only addons that must never be pulled into the
  public pack (Herobrine API, Baritone -- MINECRAFT-362's own requirement).
  Neither currently has a pin under mods/ at all (confirmed by inspecting
  this repo directly), so this list is defence in depth: if one were ever
  mistakenly added as a pin, discover_eligible_pins excludes it by name
  rather than silently enrolling it.
- Pins with no [update.modrinth] table are not Modrinth-tracked at all, so
  they are reported separately (see UNTRACKED_KIND) rather than silently
  dropped -- at the time of writing this is 4 pins: mods/dynamic-despawn.pw.toml
  and mods/magicavoxel-loader.pw.toml (GitHub-release-only first-party mods),
  plus mods/dynamic-battle-axes.pw.toml and mods/dynamic-whips.pw.toml (added
  since this count was first written; same reason -- no [update.modrinth]
  table). Verifying a GitHub release's identity/hash is a different, not yet
  implemented, mechanism (GitHub's release-asset API, not Modrinth's
  version-file metadata) -- out of scope for this pass; callers should
  surface them as a known gap, not pretend they are covered. This count
  is a snapshot of mods/ at the time of writing, not a guarantee -- call
  discover_eligible_pins() and read its own `untracked` list rather than
  trusting a number in a docstring to stay current (MINECRAFT-362 review,
  ninth round: a prior version of this docstring undercounted this by
  half).
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


def discover_eligible_pins(repo_root: Path) -> tuple[list[EligiblePin], list[UntrackedPin]]:
    """Scan mods/*.pw.toml and split it into (eligible, untracked).

    `eligible` pins have an [update.modrinth] table and are not in
    EXCLUDED_MOD_NAMES. `untracked` pins exist under mods/ but have no
    Modrinth tracking this module can verify against -- callers must
    report these as a gap, never silently skip them.
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

        modrinth = data.get("update", {}).get("modrinth") if isinstance(data.get("update"), dict) else None
        if not isinstance(modrinth, dict) or not modrinth.get("mod-id"):
            untracked.append(UntrackedPin(relative, name, "no [update.modrinth] table; not Modrinth-tracked"))
            continue

        eligible.append(
            EligiblePin(
                pin_path=relative,
                name=name,
                side=data.get("side", "both"),
                mod_id=modrinth["mod-id"],
                pinned_version_id=modrinth.get("version", ""),
            )
        )

    return eligible, untracked


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
