#!/usr/bin/env python3
"""Generalized per-mod auto-bump engine (MINECRAFT-362).

Turns a resolved, compatibility-filtered Modrinth update candidate for ANY
enrolled mod (scripts/mod_eligibility.py + scripts/modrinth_compat.py) into
a correct, built, ready-to-commit working tree -- the same shape
bump-dynamic-atmosphere.py already produces for Dynamic Atmosphere alone,
generalized to the other 77 eligible pins. Never pushes, commits, tags, or
touches Modrinth beyond the read-only metadata lookups this needs. Stdlib
only.

Deliberately reuses, rather than re-implements, the DA engine's generic
(not actually DA-specific) pieces: TOML pin rendering, pack.toml
read/bump/write, jar download+hash verification, gate running
(`make refresh/check/build`), and git plumbing. These are loaded directly
from bump-dynamic-atmosphere.py (see `_load_da_engine()` below) so there is
one implementation of each, not a second copy that could silently drift.
The identity/hash gate and no-downgrade comparator were already extracted
into modrinth_metadata.py/semver_compare.py for the same reason, and both
engines use those too.

Deliberately NOT implemented here, by design, not oversight (see
docs/mod-auto-bump.md's "Status" section for the full list):
- DA's README-sentence rewrite and Migration-section extraction are
  DA-specific (most mods have no equivalent "pins X version Y" sentence,
  and no release-body Migration-section convention). A generic bump
  always sets `listing_review_required = True` instead of attempting
  either, so listing copy is never silently left stale without being
  flagged.
- The Modrinth "vouch check" (AC8) was a DA-specific sanity check against
  a control mod; it has no generic analogue and is not attempted here.
- No FULL dependency-graph validation against other pinned mods (version
  ranges, transitively-missing requirements). This engine does refuse a
  candidate that declares another currently-pinned project
  `incompatible` (modrinth_compat.find_dependency_conflicts, checked
  below against the pack's current on-disk pins) -- an unambiguous
  conflict Modrinth's own data can answer with no guessing. A `required`
  dependency on a project that is not currently pinned is NOT treated as
  a conflict, for the reason find_dependency_conflicts documents: this
  pass cannot distinguish a genuinely missing requirement from one that
  is local-only/out-of-pack by design.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modrinth_metadata  # noqa: E402 - needs sys.path set up first
import mod_eligibility  # noqa: E402
import server_update_policy  # noqa: E402
import semver_compare  # noqa: E402
from modrinth_compat import (  # noqa: E402
    VersionCandidate, decide_update, find_dependency_conflicts, version_candidate_from_json,
)


def _load_da_engine():
    """Load bump-dynamic-atmosphere.py by path (its filename has hyphens,
    so it cannot be `import`ed normally) to reuse its generic helpers.
    Importing it only defines constants/functions at module level (guarded
    by `if __name__ == "__main__"`), so this has no side effects of its
    own -- the same tradeoff da-auto-bump-dispatch.py already accepts for
    reusing has_da_auto_bump_trailer this way."""
    module_path = Path(__file__).resolve().parent / "bump-dynamic-atmosphere.py"
    spec = importlib.util.spec_from_file_location("bump_dynamic_atmosphere_shared", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bump_dynamic_atmosphere_shared"] = module
    spec.loader.exec_module(module)
    return module


_da = _load_da_engine()

EngineError = _da.EngineError
EXIT_OK = _da.EXIT_OK
EXIT_INTEGRITY_FAILURE = _da.EXIT_INTEGRITY_FAILURE
EXIT_DOWNGRADE_REFUSED = _da.EXIT_DOWNGRADE_REFUSED
EXIT_ANOMALY = _da.EXIT_ANOMALY
EXIT_GATE_FAILURE = _da.EXIT_GATE_FAILURE
EXIT_NOTES_EXISTS = _da.EXIT_NOTES_EXISTS
EXIT_CONFIG_ERROR = _da.EXIT_CONFIG_ERROR
EXIT_UNEXPECTED_ERROR = _da.EXIT_UNEXPECTED_ERROR
EXIT_DIRTY_TREE = _da.EXIT_DIRTY_TREE
EXIT_MIGRATION_MISSING = _da.EXIT_MIGRATION_MISSING

KNOWN_CATEGORIES = _da.KNOWN_CATEGORIES
render_pin = _da.render_da_pin  # fully generic despite the name: name/filename/side/url/hash/mod-id/version
load_pack_version = _da.load_pack_version
bump_pack_version = _da.bump_pack_version
write_pack_version = _da.write_pack_version
download_and_verify = _da.download_and_verify
default_downloader = _da.default_downloader
default_gate_commands = _da.default_gate_commands
run_gate = _da.run_gate

USER_AGENT = "brooswit-minecraft/sickos (MINECRAFT-362 generic mod auto-bump engine)"


def fetch_version_metadata(version_id, *, token=None):
    """GET /v2/version/{id}. Generic analogue of the DA engine's own
    fetch_modrinth_version_metadata, kept independent of that module's
    _MODRINTH_TOKEN global so this engine's token handling is explicit
    per call rather than implicit module state."""
    url = f"https://api.modrinth.com/v2/version/{urllib.parse.quote(version_id, safe='')}"
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = token
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


# --------------------------------------------------------------------------
# git plumbing, generalized over an explicit restore-target list instead of
# bump-dynamic-atmosphere.py's fixed RESTORE_TARGET_PATHS (which includes
# README.md -- not relevant here since this engine never touches it).
# --------------------------------------------------------------------------

def _restore_targets(pin_path):
    return ("pack.toml", "index.toml", pin_path)


def assert_restore_targets_clean(repo_root, pin_path, run_command=subprocess.run):
    targets = _restore_targets(pin_path)
    result = run_command(["git", "status", "--porcelain", "--", *targets],
                          cwd=str(repo_root), capture_output=True, text=True)
    if result.returncode != 0:
        raise EngineError(
            EXIT_CONFIG_ERROR, "config_error",
            f"refusing to run: 'git status --porcelain' failed (exit {result.returncode}) while "
            "checking whether " + ", ".join(targets) + " are clean; nothing was touched. "
            f"stderr: {(result.stderr or '').strip()}",
        )
    dirty = [line for line in result.stdout.splitlines() if line]
    if dirty:
        raise EngineError(
            EXIT_DIRTY_TREE, "dirty_tree",
            "refusing to run: " + ", ".join(targets) + " must be clean before this engine "
            "starts (it restores or stages exactly these paths, so pre-existing uncommitted "
            "changes to them would be discarded on failure or stolen into the bump on success); "
            "git status --porcelain reports: " + "; ".join(dirty),
        )


def restore_repo(repo_root, pin_path, notes_path=None, run_command=subprocess.run):
    targets = _restore_targets(pin_path)
    run_command(["git", "restore", "--staged", "--worktree", "--", *targets],
                cwd=str(repo_root), check=True)
    if notes_path is not None and notes_path.exists():
        relative_notes_path = str(notes_path.relative_to(repo_root))
        run_command(["git", "restore", "--staged", "--", relative_notes_path],
                    cwd=str(repo_root), check=False, capture_output=True, text=True)
        notes_path.unlink()


def stage_intended_files(repo_root, pin_path, notes_path, run_command=subprocess.run):
    targets = _restore_targets(pin_path)
    paths = [*targets, str(notes_path.relative_to(repo_root))]
    run_command(["git", "add", "--", *paths], cwd=str(repo_root), check=True)


def assert_only_pin_changed_under_mods(repo_root, pin_path, run_command=subprocess.run):
    result = run_command(["git", "diff", "--name-only", "HEAD", "--", "mods"],
                          cwd=str(repo_root), capture_output=True, text=True)
    if result.returncode != 0:
        raise EngineError(
            EXIT_CONFIG_ERROR, "config_error",
            f"refusing to commit: 'git diff --name-only HEAD -- mods' failed (exit "
            f"{result.returncode}); cannot confirm only {pin_path} changed under mods/. "
            f"stderr: {(result.stderr or '').strip()}",
        )
    changed = [line for line in result.stdout.splitlines() if line]
    unexpected = [path for path in changed if path != pin_path]
    if unexpected:
        raise EngineError(EXIT_CONFIG_ERROR, "config_error",
                           f"unexpected changes under mods/: {unexpected}")


# --------------------------------------------------------------------------
# Notes file. Deliberately simpler than DA's: no vouch check, no Migration
# extraction -- listing_review_required is unconditionally True (see this
# module's docstring).
# --------------------------------------------------------------------------

MAX_CHANGELOG_CHARS = 4000


def _fenced(text, *, max_chars=MAX_CHANGELOG_CHARS):
    """Render `text` inside a fenced code block so none of ITS OWN
    markdown (headings, links, anything) can restructure the surrounding
    notes file -- a prior round spliced a mod's Modrinth changelog in
    verbatim, letting an author-controlled "# " heading visually hijack
    the release notes (MINECRAFT-362 review). Any triple-backtick inside
    the text is defanged (a zero-width space inserted) so it cannot
    prematurely close the fence, and the text is capped so one mod's
    long changelog cannot make the notes file unbounded."""
    text = text.strip().replace("```", "``​`")
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n... (truncated)"
    return "```\n" + text + "\n```"


def render_notes(*, pin, new_pack_version, previous_pack_version, candidate, mapping_rule, category_note,
                  migration_text=None):
    breaking = mapping_rule == "breaking_as_minor"
    heading = f"# Sickos {new_pack_version}" + (" -- BREAKING" if breaking else "")
    lines = [
        heading,
        "",
        f"{pin.name}: {candidate.version_number} (Modrinth version id `{candidate.version_id}`)",
        f"Previous Sickos version: {previous_pack_version}",
        f"Mapping rule that fired: `{mapping_rule}`",
        "",
    ]
    if category_note:
        lines.append(category_note)
        lines.append("")
    if breaking:
        lines.append("## BREAKING")
        lines.append("")
        lines.append(
            "This release advances the minor version (Sickos is pre-1.0) and includes a "
            "breaking change."
        )
        lines.append("")
        lines.append("### Migration / reset instructions")
        lines.append("")
        # run_engine refuses to reach this point at all unless allow_breaking
        # was explicitly True AND migration_text was non-empty -- see its
        # own gate -- so this is the real, human-authored text, never the
        # old placeholder that said instructions were absent while
        # providing none.
        lines.append(migration_text.strip())
        lines.append("")
    if candidate.changelog:
        lines.append(f"## {pin.name}'s own Modrinth changelog")
        lines.append("")
        lines.append(_fenced(candidate.changelog))
        lines.append("")
    lines.append("## Listing review")
    lines.append("")
    lines.append(
        f"listing_review_required is always true for a generalized {pin.name} bump: this "
        "engine has no per-mod listing-text heuristic (unlike Dynamic Atmosphere's dedicated "
        "one), so README.md and .modrinth/description.md are NOT inspected or rewritten "
        "automatically and may now be stale. A human should check them before this release ships."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def write_notes(repo_root, new_pack_version, content):
    releases_dir = repo_root / "docs" / "releases"
    releases_dir.mkdir(parents=True, exist_ok=True)
    notes_path = releases_dir / f"{new_pack_version}.md"
    if notes_path.exists():
        raise EngineError(EXIT_NOTES_EXISTS, "notes_exists",
                           f"{notes_path.relative_to(repo_root)} already exists; refusing to overwrite "
                           "a published release's notes")
    notes_path.write_text(content, encoding="utf-8")
    return notes_path


def _no_candidate_summary(pin):
    return {
        "changed": False,
        "outcome": "no_candidate",
        "mod_name": pin.name,
        "mod_id": pin.mod_id,
        "pin_path": pin.pin_path,
        "candidate_version_id": None,
        "candidate_version_number": None,
        "previous_pack_version": None,
        "new_pack_version": None,
        "mapping_rule": None,
        "notes_path": None,
        "listing_review_required": False,
        "error": None,
    }


# --------------------------------------------------------------------------
# Engine entry point.
# --------------------------------------------------------------------------

def run_engine(repo_root, pin, candidate, *, category=None,
                downloader=default_downloader, run_command=subprocess.run,
                gate_commands=None, metadata_fetcher=None, token=None, allow_breaking=False,
                migration_text=None):
    """Bump `pin` (scripts/mod_eligibility.EligiblePin) to `candidate`
    (scripts/modrinth_compat.VersionCandidate, already filtered for
    game-version/loader compatibility by the caller). Returns
    (summary_dict, exit_code, human_message), matching
    bump-dynamic-atmosphere.py's run_engine shape so a dispatch/poll caller
    can reuse the same decide/report pattern as da-auto-bump-dispatch.py.
    """
    repo_root = Path(repo_root)
    metadata_fetcher = metadata_fetcher or (lambda version_id: fetch_version_metadata(version_id, token=token))

    assert_restore_targets_clean(repo_root, pin.pin_path, run_command=run_command)

    # Raw caller-supplied category, kept only for the notes text below --
    # the FINAL category is resolved later, once previous_version_number
    # is known (see the no-downgrade check), by combining this with the
    # semver delta (MINECRAFT-362 review, sixth round).
    category_received = category
    caller_category = category if category in KNOWN_CATEGORIES else None

    dedup_decision = decide_update(pin, candidate)
    if dedup_decision == "no_candidate":
        return (
            _no_candidate_summary(pin),
            EXIT_OK,
            f"No compatible Modrinth update found for {pin.name}. No changes made.",
        )
    if dedup_decision == "already_pinned":
        return (
            {
                "changed": False,
                "outcome": "already_pinned",
                "mod_name": pin.name,
                "mod_id": pin.mod_id,
                "pin_path": pin.pin_path,
                "candidate_version_id": candidate.version_id,
                "candidate_version_number": candidate.version_number,
                "previous_pack_version": load_pack_version(repo_root),
                "new_pack_version": None,
                "mapping_rule": None,
                "notes_path": None,
                "listing_review_required": False,
                "error": None,
            },
            EXIT_OK,
            f"Already pinned: {pin.name} is already at this version. No changes made.",
        )

    # Re-verify the candidate's identity/hash against Modrinth's authoritative
    # version-file metadata, fetched fresh right now -- the version-LIST
    # response modrinth_compat used to pick this candidate is never trusted
    # alone for a write decision. Same gate DA's engine uses.
    try:
        metadata = metadata_fetcher(candidate.version_id)
    except urllib.error.HTTPError as error:
        raise EngineError(
            EXIT_INTEGRITY_FAILURE, "integrity_failure",
            f"Modrinth version metadata check failed for {candidate.version_id}: "
            f"metadata lookup returned HTTP {error.code}",
        )
    except Exception as error:  # noqa: BLE001 - any lookup failure fails closed
        raise EngineError(
            EXIT_INTEGRITY_FAILURE, "integrity_failure",
            f"Modrinth version metadata check failed for {candidate.version_id}: "
            f"metadata lookup failed: {error!r}",
        )

    try:
        modrinth_metadata.verify_version_file_metadata(
            metadata,
            expected_version_id=candidate.version_id,
            expected_project_id=pin.mod_id,
            expected_version_number=candidate.version_number,
            expected_file_name=candidate.file_name,
            expected_sha512=candidate.sha512,
            expected_sha1=candidate.sha1,
            expected_download_url=candidate.download_url,
        )
    except modrinth_metadata.MetadataVerificationError as error:
        raise EngineError(
            EXIT_INTEGRITY_FAILURE, "integrity_failure",
            f"Modrinth version metadata check failed for {candidate.version_id}: {error}",
        )

    # Strong no-downgrade check: a pin only stores the previously-pinned
    # Modrinth version ID, not its semver string, so look that version's
    # own version_number up before judging whether the candidate is
    # actually newer. This lookup must fail CLOSED, not degrade to the
    # weaker dedup-only safety above: if the currently-pinned version is
    # ever unpublished/deleted on Modrinth, a bare "lookup failed, skip
    # the check" would let an attacker-controlled or merely stale resolver
    # response silently pin a real downgrade (MINECRAFT-362 review) --
    # exactly the "stale-event handling without downgrades" guarantee this
    # pass exists to provide.
    try:
        previous_metadata = metadata_fetcher(pin.pinned_version_id)
    except Exception as error:  # noqa: BLE001 - any lookup failure fails closed
        raise EngineError(
            EXIT_INTEGRITY_FAILURE, "integrity_failure",
            f"could not verify {pin.name} is not a downgrade: looking up the currently-pinned "
            f"version {pin.pinned_version_id!r}'s own metadata failed: {error!r}",
        )
    previous_version_number = (
        previous_metadata.get("version_number") if isinstance(previous_metadata, dict) else None
    )
    if previous_version_number is None:
        raise EngineError(
            EXIT_INTEGRITY_FAILURE, "integrity_failure",
            f"could not verify {pin.name} is not a downgrade: the currently-pinned version "
            f"{pin.pinned_version_id!r}'s metadata had no version_number",
        )

    downgrade_decision = decide_update(pin, candidate, previous_version_number=previous_version_number)
    if downgrade_decision == "downgrade_refused":
        raise EngineError(
            EXIT_DOWNGRADE_REFUSED, "downgrade_refused",
            f"candidate version {candidate.version_number} for {pin.name} is not newer than "
            f"the currently pinned {previous_version_number}; refusing to downgrade",
        )
    if downgrade_decision == "unparsable_version":
        # Deliberately NOT the generic "config_error" outcome: a non-semver
        # version scheme is a per-pin, permanent property (not a one-off
        # configuration mistake this run could recover from), so callers
        # that poll many pins need to tell it apart from an unexpected
        # failure -- see poll-mod-updates.py's EXPECTED_GAP_OUTCOMES
        # (MINECRAFT-362 review).
        raise EngineError(
            EXIT_CONFIG_ERROR, "unsupported_version_scheme",
            f"cannot compare non-semver version(s) for {pin.name}: "
            f"{candidate.version_number!r}, {previous_version_number!r}",
        )

    # AC4 category determination (MINECRAFT-362 review, sixth round):
    # the semver delta between the currently-pinned and candidate version
    # numbers (both confirmed parseable above) is an always-available
    # signal the mod author already committed to -- major changed ->
    # breaking, minor changed -> minor, patch changed -> patch. A
    # caller-supplied category (a changelog heading, or an explicit
    # --category on scripts/bump_mod.py's CLI) can only ESCALATE this,
    # never downgrade it: a heading that says "breaking" is trusted over
    # a merely-patch-looking version bump, but a heading that says "fix"
    # on a major version bump does not get to suppress the breaking
    # classification the version number itself declares. Measured live
    # against real Modrinth changelogs: 0 of 15 real candidates matched
    # derive_category_from_changelog's heading-only regex (real
    # changelogs are bullet lists), so the semver delta -- not the
    # changelog -- is the one signal that is actually always available.
    delta_category = semver_compare.semver_delta_category(previous_version_number, candidate.version_number)
    category = semver_compare.more_severe_category(caller_category, delta_category)

    # Dependency check (narrow, see modrinth_compat.find_dependency_conflicts):
    # refuse a candidate that declares another currently-pinned project
    # incompatible. Reads the pack's CURRENT on-disk pins, before this
    # run writes anything.
    eligible_pins, _untracked = mod_eligibility.discover_eligible_pins(repo_root)
    pinned_mod_ids = {eligible_pin.mod_id for eligible_pin in eligible_pins}
    conflicts = find_dependency_conflicts(candidate, pinned_mod_ids)
    if conflicts:
        raise EngineError(
            EXIT_CONFIG_ERROR, "dependency_conflict",
            f"{pin.name} candidate {candidate.version_number} has dependency conflicts: "
            + "; ".join(conflicts),
        )

    previous_pack_version = load_pack_version(repo_root)
    new_pack_version, mapping_rule = bump_pack_version(previous_pack_version, category)

    # AC4: a generic bump has no Migration-section-extraction mechanism
    # (unlike DA's dedicated engine), so it cannot itself produce the
    # "migration/reset instructions" AC4 requires for a breaking change --
    # auto-publishing a placeholder saying "a human must write migration
    # instructions" is not the same as having them. Fail closed by
    # default; a human who has actually written real migration/reset
    # instructions can run `scripts/bump_mod.py` directly (see its
    # `__main__` CLI, `--help`) with `--allow-breaking` and
    # `--migration-text-file <path>` to proceed. Both are required
    # together: `allow_breaking=True` with no text is still refused below,
    # since the point is to require the real text, not merely a flag
    # (MINECRAFT-362 review -- the previous message here named a
    # `--allow-breaking` flag that did not exist on any runnable command).
    if mapping_rule == "breaking_as_minor":
        if not allow_breaking:
            raise EngineError(
                EXIT_MIGRATION_MISSING, "breaking_change_requires_human_review",
                f"{pin.name} candidate {candidate.version_number} is a breaking change; this "
                "generic engine cannot auto-generate migration/reset instructions. Refusing to "
                "auto-publish a placeholder in their place -- a human must review this release "
                "and publish it by hand: `python3 scripts/bump_mod.py --repo-root . --pin-path "
                f"{pin.pin_path} --version-id {candidate.version_id} --category breaking "
                "--allow-breaking --migration-text-file <path>` (run `python3 scripts/"
                "bump_mod.py --help` for the remaining optional flags) once real "
                "migration/reset instructions are written.",
            )
        if not migration_text or not migration_text.strip():
            raise EngineError(
                EXIT_CONFIG_ERROR, "migration_text_required",
                f"{pin.name} candidate {candidate.version_number}: allow_breaking=True was "
                "passed without migration_text. A breaking release must carry real "
                "migration/reset instructions, not just the flag that permits publishing one -- "
                "pass --migration-text-file with real content.",
            )

    releases_dir = repo_root / "docs" / "releases"
    notes_target = releases_dir / f"{new_pack_version}.md"
    if notes_target.exists():
        raise EngineError(
            EXIT_NOTES_EXISTS, "notes_exists",
            f"docs/releases/{new_pack_version}.md already exists; refusing to overwrite a "
            "published release's notes",
        )

    if category is None:
        category_note = (
            f"No category could be determined -- the version delta "
            f"{previous_version_number} -> {candidate.version_number} changed no semver "
            f"component, and no changelog/explicit category was supplied or recognised "
            f"(received {category_received!r}); defaulted to minor per {pin.name}'s mapping "
            f"rule ({mapping_rule})."
        )
    else:
        category_note = (
            f"Category: {category!r} -- semver delta {previous_version_number} -> "
            f"{candidate.version_number} => {delta_category!r}; changelog/explicit category "
            f"=> {category_received!r}. The more severe of the two is used; a changelog/"
            "explicit category can only escalate the semver-delta classification, never "
            "downgrade it."
        )

    download_and_verify(candidate.download_url, candidate.sha1, candidate.sha512, downloader)

    notes_path = None
    try:
        new_pin_content = render_pin(
            pin.name, candidate.file_name, pin.side,
            candidate.download_url, candidate.sha512,
            pin.mod_id, candidate.version_id,
        )
        (repo_root / pin.pin_path).write_text(new_pin_content, encoding="utf-8")

        write_pack_version(repo_root, previous_pack_version, new_pack_version)

        notes_content = render_notes(
            pin=pin, new_pack_version=new_pack_version, previous_pack_version=previous_pack_version,
            candidate=candidate, mapping_rule=mapping_rule, category_note=category_note,
            migration_text=migration_text,
        )
        notes_path = write_notes(repo_root, new_pack_version, notes_content)

        gates = gate_commands or default_gate_commands(repo_root)
        for gate_name in ("refresh", "check", "build"):
            run_gate(repo_root, run_command, gates, gate_name)

        assert_only_pin_changed_under_mods(repo_root, pin.pin_path, run_command=run_command)
        stage_intended_files(repo_root, pin.pin_path, notes_path, run_command=run_command)
    except EngineError:
        restore_repo(repo_root, pin.pin_path, notes_path, run_command=run_command)
        raise
    except Exception as error:  # noqa: BLE001 - any unforeseen failure still restores and fails closed
        restore_repo(repo_root, pin.pin_path, notes_path, run_command=run_command)
        raise EngineError(EXIT_UNEXPECTED_ERROR, "unexpected_error", f"unexpected error: {error!r}") from error

    summary = {
        "changed": True,
        "outcome": "bumped",
        "mod_name": pin.name,
        "mod_id": pin.mod_id,
        "pin_path": pin.pin_path,
        "candidate_version_id": candidate.version_id,
        "candidate_version_number": candidate.version_number,
        "previous_pack_version": previous_pack_version,
        "new_pack_version": new_pack_version,
        "mapping_rule": mapping_rule,
        "notes_path": str(notes_path.relative_to(repo_root)),
        "listing_review_required": True,
        "error": None,
    }
    return summary, EXIT_OK, f"Bumped {pin.name} to {candidate.version_number} (Sickos {new_pack_version})."



# --------------------------------------------------------------------------
# CLI: a real, runnable entry point for the ONE case this module is
# otherwise import-only for -- a human manually publishing a breaking
# change with real migration/reset instructions in hand, after the
# allow_breaking gate above refused to do it unattended (MINECRAFT-362
# review: a prior round's error message named a `--allow-breaking` flag
# that existed on no runnable command at all). Not used by
# poll-mod-updates.py, which never passes allow_breaking/migration_text
# and so never needs this; this is strictly for the human-in-the-loop
# path.
#
# Fourth review round found three required defects, all confined to this
# CLI, all from one root cause: it had been building the pin and the
# candidate from flags instead of from the pin on disk plus the real
# Modrinth version metadata run_engine already fetches elsewhere. Fixed
# by: fetching the candidate's full metadata here too (so it carries real
# game_versions/loaders/dependencies, not empty defaults, and the
# operator no longer hand-transcribes hash/url/filename fields at all);
# checking that metadata against the pack's own compatibility target
# before ever calling run_engine; defaulting --side (and every other
# pin-identity field) from the pin actually on disk and refusing an
# explicit value that disagrees, rather than silently overriding it; and
# committing with the exact same trailer-carrying commit message
# poll-mod-updates.py uses, instead of leaving the operator to write
# their own commit message with no Release-Only/Mod-Auto-Bump trailer
# (which server_update_policy.should_deploy reads as permission to
# deploy/restart the live server).
# --------------------------------------------------------------------------

def _load_build_generic_commit_message():
    module_path = Path(__file__).resolve().parent / "da-auto-bump-dispatch.py"
    spec = importlib.util.spec_from_file_location("da_auto_bump_dispatch_shared_cli", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["da_auto_bump_dispatch_shared_cli"] = module
    spec.loader.exec_module(module)
    return module.build_generic_commit_message


build_generic_commit_message = _load_build_generic_commit_message()


def cli_commit_bump(repo_root, message, paths, run_command=subprocess.run):
    """Same shape and bot identity as scripts/poll-mod-updates.py's own
    commit_bump -- a CLI-published bump is still the generalized
    mod-auto-bump mechanism, just human-triggered instead of scheduled,
    so it carries the same trailers and the same committer identity.

    Commits ONLY `paths` (pack.toml, index.toml, the pin, the notes
    file), never a bare `git commit` with no pathspec: unlike the
    automated poll's fresh CI checkout, this runs on a human's working
    checkout, where an unrelated file being already staged (e.g. an
    in-progress config edit) is ordinary -- a bare `git commit` would
    sweep that into this release commit too, unreviewed, published as
    Release-Only under this bot's identity (MINECRAFT-362 review, fifth
    round, confirmed empirically). A pathspec'd commit leaves anything
    else staged untouched -- still staged, neither stolen nor discarded."""
    run_command(
        ["git", "-c", "user.name=sickos-mod-auto-bump[bot]",
         "-c", "user.email=sickos-mod-auto-bump[bot]@users.noreply.github.com",
         "commit", "-F", "-", "--", *paths],
        cwd=str(repo_root), input=message, text=True, check=True,
    )
    result = run_command(["git", "rev-parse", "HEAD"], cwd=str(repo_root),
                          capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _build_cli_parser():
    parser = argparse.ArgumentParser(
        description="Manually bump one enrolled mod's pin to a specific, already-known "
                     "Modrinth version id -- primarily for publishing a breaking change by "
                     "hand with real migration/reset instructions (run_engine's allow_breaking "
                     "gate refuses this unattended). The candidate's identity, hashes, "
                     "compatibility and dependency data are all fetched fresh from Modrinth by "
                     "version id, the same way the unattended poll path does -- this CLI never "
                     "takes them as flags, so there is nothing here to hand-transcribe.",
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--pin-path", required=True, help="e.g. mods/example-mod.pw.toml")
    parser.add_argument("--version-id", required=True, help="Candidate Modrinth version id.")
    parser.add_argument("--mod-id", default=None,
                         help="Defaults to the pin's own [update.modrinth] mod-id; an explicit "
                              "value that disagrees with the pin on disk is refused, not "
                              "overridden -- this is the field every identity/hash check trusts "
                              "as ground truth (run_engine verifies the fetched candidate's "
                              "project_id against it), so an unguarded mismatch here would let "
                              "one mistyped flag publish an entirely different mod's jar under "
                              "this pin's name/side (MINECRAFT-362 review, fifth round).")
    parser.add_argument("--mod-name", default=None,
                         help="Defaults to the pin's own [name]; an explicit value that "
                              "disagrees with the pin on disk is refused, not overridden.")
    parser.add_argument("--side", default=None, choices=("both", "client", "server"),
                         help="Defaults to the pin's own current [side]; an explicit value "
                              "that disagrees with the pin on disk is refused, not overridden "
                              "-- a prior round's default of 'both' silently moved 6 client-/"
                              "server-only pins onto both sides.")
    parser.add_argument("--pinned-version-id", default=None,
                         help="Defaults to reading the pin's current [update.modrinth] version; "
                              "an explicit value that disagrees is refused, not overridden.")
    parser.add_argument("--changelog-file", default=None,
                         help="Optional: included verbatim (fenced) in the generated notes. "
                              "Not needed to satisfy AC4 for a breaking change -- that is "
                              "--migration-text-file's job.")
    parser.add_argument("--category", default=None, choices=("patch", "minor", "breaking"))
    parser.add_argument("--allow-breaking", action="store_true",
                         help="Required (together with --migration-text-file) to publish a "
                              "--category breaking bump.")
    parser.add_argument("--migration-text-file", default=None,
                         help="Required when --allow-breaking is set: real migration/reset "
                              "instructions, included in the generated notes. Only used when "
                              "--category is breaking; supplying it otherwise is a no-op and "
                              "produces a warning, not silent discarding.")
    parser.add_argument("--no-commit", action="store_true",
                         help="Stage the bump but do not commit it (for inspection). Default "
                              "behaviour commits with the same trailer-carrying message "
                              "poll-mod-updates.py uses, so server_update_policy.should_deploy "
                              "reads it as Release-Only, same as the automated path.")
    parser.add_argument("--summary-out", default=None)
    return parser


def _discover_pin_or_die(repo_root, pin_path):
    eligible, _untracked = mod_eligibility.discover_eligible_pins(repo_root)
    matching = next((p for p in eligible if p.pin_path == pin_path), None)
    if matching is None:
        raise SystemExit(
            f"--pin-path {pin_path!r} is not an eligible, Modrinth-tracked pin under "
            f"{repo_root} (see scripts/mod_eligibility.py). This CLI only operates on pins "
            "already enrolled this way; it does not add a new pin."
        )
    return matching


def _resolve_or_die(field_name, explicit_value, on_disk_value):
    """Default a pin-identity field from the pin actually on disk; refuse
    (do not silently override) an explicit value that disagrees. This is
    the fix for --side's old 'both' default silently moving client-/
    server-only pins (MINECRAFT-362 review, fourth round) -- and,
    crucially, ALSO applies to --mod-id (fifth round): a fourth round's
    claim that every pin-identity field used this helper was wrong,
    --mod-id was the one exception, and it is the field run_engine's own
    identity gate trusts as ground truth, so an unguarded mismatch there
    defeated artifact-identity validation on the one path that can
    publish without passing through it."""
    if explicit_value is None:
        return on_disk_value
    if explicit_value != on_disk_value:
        raise SystemExit(
            f"--{field_name} {explicit_value!r} disagrees with the pin on disk "
            f"({on_disk_value!r}). Refusing to override pin identity/membership via a flag -- "
            f"omit --{field_name} to use the pin's own current value, or fix the pin on disk "
            "first if it is genuinely wrong."
        )
    return explicit_value


def _pin_from_cli(args):
    on_disk = _discover_pin_or_die(args.repo_root, args.pin_path)
    return mod_eligibility.EligiblePin(
        pin_path=args.pin_path,
        name=_resolve_or_die("mod-name", args.mod_name, on_disk.name),
        side=_resolve_or_die("side", args.side, on_disk.side),
        mod_id=_resolve_or_die("mod-id", args.mod_id, on_disk.mod_id),
        pinned_version_id=_resolve_or_die("pinned-version-id", args.pinned_version_id, on_disk.pinned_version_id),
    )


def _candidate_from_cli(args, *, token=None):
    """Fetch GET /v2/version/{id} and build a FULLY POPULATED
    VersionCandidate from it -- real game_versions/loaders/dependencies,
    not the empty defaults a hand-built VersionCandidate(...) call would
    carry (MINECRAFT-362 review, fourth round: those empty defaults made
    the compatibility check and find_dependency_conflicts both no-ops on
    this path)."""
    try:
        metadata = fetch_version_metadata(args.version_id, token=token)
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"GET /v2/version/{args.version_id} returned HTTP {error.code}. If this project "
            "is one that Modrinth will not serve anonymously (see docs/mod-auto-bump.md's "
            "KNOWN_ANONYMOUS_404_MOD_IDS), set MODRINTH_TOKEN in the environment before running "
            "this CLI and try again."
        ) from error
    except urllib.error.URLError as error:
        # Distinct from HTTPError above: this is a transport-level
        # failure (DNS, connection refused, timeout) that never reached
        # Modrinth at all, not an HTTP status Modrinth itself returned --
        # a token would not help here (MINECRAFT-362 review, sixth
        # round: only HTTPError was previously handled).
        raise SystemExit(
            f"GET /v2/version/{args.version_id} failed before reaching Modrinth: {error.reason}. "
            "Check your network connection and try again."
        ) from error
    if metadata.get("id") != args.version_id:
        raise SystemExit(
            f"Modrinth's response for version {args.version_id!r} carries a different id "
            f"({metadata.get('id')!r}); refusing to proceed."
        )
    candidate = version_candidate_from_json(metadata)
    if candidate is None:
        raise SystemExit(f"Modrinth's version {args.version_id!r} has no usable file entry.")
    if args.changelog_file:
        candidate = dataclasses.replace(
            candidate, changelog=Path(args.changelog_file).read_text(encoding="utf-8"),
        )
    return candidate


def _check_compatibility_or_die(repo_root, candidate):
    """The only (game_version, loader) check in the system otherwise
    lives inside modrinth_compat.resolve_latest_compatible_version, which
    this CLI bypasses by construction (it is given a version id, not
    asked to resolve one) -- so re-check explicitly here, now that
    _candidate_from_cli has given this candidate its real
    game_versions/loaders, before run_engine ever writes anything
    (MINECRAFT-362 review, fourth round)."""
    game_version, loader = mod_eligibility.read_pack_targets(repo_root)
    if game_version not in candidate.game_versions or loader not in candidate.loaders:
        raise SystemExit(
            f"Modrinth version {candidate.version_id!r} does not declare compatibility with "
            f"this pack's target (game_version={game_version!r}, loader={loader!r}) -- it "
            f"declares game_versions={candidate.game_versions!r}, loaders={candidate.loaders!r}. "
            "Refusing to pin an incompatible build."
        )


def cli_main(argv=None):
    args = _build_cli_parser().parse_args(argv)

    if args.category == "breaking" and not args.allow_breaking:
        print("::error::--category breaking requires --allow-breaking (and --migration-text-file "
              "with real content) -- see --help.", file=sys.stderr)
        return 2
    migration_text = None
    if args.migration_text_file:
        migration_text = Path(args.migration_text_file).read_text(encoding="utf-8")
        if args.category != "breaking":
            print("::warning::--migration-text-file was supplied but --category is not "
                  "'breaking'; render_notes only emits migration text on a breaking bump, so "
                  "this file's content will not appear anywhere in the generated notes.",
                  file=sys.stderr)
    if args.allow_breaking and not (migration_text and migration_text.strip()):
        print("::error::--allow-breaking requires --migration-text-file with real, non-empty "
              "content.", file=sys.stderr)
        return 2

    token = os.environ.pop("MODRINTH_TOKEN", None) or None

    pin = _pin_from_cli(args)
    candidate = _candidate_from_cli(args, token=token)
    _check_compatibility_or_die(args.repo_root, candidate)

    try:
        summary, exit_code, message = run_engine(
            args.repo_root, pin, candidate, category=args.category, token=token,
            allow_breaking=args.allow_breaking, migration_text=migration_text,
        )
    except EngineError as error:
        print(f"::error::{error.message}", file=sys.stderr)
        return error.exit_code

    print(message)

    if summary["changed"] and not args.no_commit:
        commit_message = build_generic_commit_message(
            mapping_rule=summary["mapping_rule"], new_pack_version=summary["new_pack_version"],
            mod_name=pin.name, mod_id=pin.mod_id,
            modrinth_version_id=summary["candidate_version_id"],
            version_number=summary["candidate_version_number"],
            source_text="a human-run CLI (scripts/bump_mod.py)",
        )
        # Defense in depth, not the only thing standing between this and
        # a live deploy: build_generic_commit_message already always
        # attaches Release-Only: true, but check it holds for the exact
        # message about to be committed anyway (MINECRAFT-362 review,
        # fourth round -- a CLI-authored commit with no Release-Only
        # trailer reads as permission to deploy/restart the live server).
        # A plain `if`, not `assert`: an assert vanishes entirely under
        # `python3 -O`, silently dropping this exact guard (fifth round).
        if server_update_policy.should_deploy(commit_message):
            raise SystemExit(
                "refusing to commit: the generated commit message would not be read as "
                "Release-Only by server_update_policy.should_deploy -- this should not be "
                "possible; please report it rather than committing by hand."
            )
        commit_paths = ("pack.toml", "index.toml", pin.pin_path, summary["notes_path"])
        commit_sha = cli_commit_bump(args.repo_root, commit_message, commit_paths)
        summary["commit_sha"] = commit_sha
        print(
            f"Committed {commit_sha} (Release-Only; will not trigger a server deploy/restart). "
            "This commit is LOCAL ONLY -- push it to main yourself (e.g. `git push origin "
            "main`) for release.yml to fire; nothing here pushes on your behalf."
        )
    elif summary["changed"]:
        print(
            "--no-commit: bump staged but not committed. Do NOT run a bare `git commit` -- "
            "that would also commit anything else you already had staged, and a commit with "
            "no Release-Only trailer is read as permission to deploy/restart the live server. "
            "Commit explicitly: `git commit -F <message-file> -- $(git diff --cached --name-only"
            ")`, where <message-file> holds a message carrying `Release-Only: true` and "
            f"`Mod-Auto-Bump: {pin.mod_id} {candidate.version_id}` alone in its final paragraph "
            "(see scripts/da-auto-bump-dispatch.py's build_generic_commit_message for the exact "
            "shape git's trailer parser requires)."
        )

    summary_text = json.dumps(summary, indent=2, sort_keys=True)
    print(summary_text)
    if args.summary_out:
        Path(args.summary_out).write_text(summary_text + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    sys.exit(cli_main())
