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
- No dependency-graph validation against other pinned mods -- only the
  candidate's own declared game-version/loader compatibility
  (already checked by modrinth_compat.resolve_latest_compatible_version
  before a candidate ever reaches this engine).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modrinth_metadata  # noqa: E402 - needs sys.path set up first
from modrinth_compat import decide_update  # noqa: E402


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

def render_notes(*, pin, new_pack_version, previous_pack_version, candidate, mapping_rule, category_note):
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
            "breaking change. No automated migration text is available for a generic mod "
            "bump -- a human must write migration/reset instructions before this release ships."
        )
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
                gate_commands=None, metadata_fetcher=None, token=None):
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

    category_received = category
    if category is not None and category not in KNOWN_CATEGORIES:
        category = "__unrecognised__"

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
    # actually newer. A failed lookup here only degrades to the dedup-only
    # safety already established above (version id mismatch => proceed) --
    # it does not fail the whole run, since the identity/hash gate above
    # already fail-closed on the candidate itself.
    try:
        previous_metadata = metadata_fetcher(pin.pinned_version_id)
        previous_version_number = (
            previous_metadata.get("version_number") if isinstance(previous_metadata, dict) else None
        )
    except Exception:  # noqa: BLE001 - degrade to dedup-only safety, do not crash
        previous_version_number = None

    if previous_version_number is not None:
        downgrade_decision = decide_update(pin, candidate, previous_version_number=previous_version_number)
        if downgrade_decision == "downgrade_refused":
            raise EngineError(
                EXIT_DOWNGRADE_REFUSED, "downgrade_refused",
                f"candidate version {candidate.version_number} for {pin.name} is not newer than "
                f"the currently pinned {previous_version_number}; refusing to downgrade",
            )
        if downgrade_decision == "unparsable_version":
            raise EngineError(
                EXIT_CONFIG_ERROR, "config_error",
                f"cannot compare non-semver version(s) for {pin.name}: "
                f"{candidate.version_number!r}, {previous_version_number!r}",
            )

    previous_pack_version = load_pack_version(repo_root)
    new_pack_version, mapping_rule = bump_pack_version(previous_pack_version, category)

    releases_dir = repo_root / "docs" / "releases"
    notes_target = releases_dir / f"{new_pack_version}.md"
    if notes_target.exists():
        raise EngineError(
            EXIT_NOTES_EXISTS, "notes_exists",
            f"docs/releases/{new_pack_version}.md already exists; refusing to overwrite a "
            "published release's notes",
        )

    category_note = None
    if category is None:
        category_note = (
            f"No category was supplied; defaulted to minor per {pin.name}'s mapping rule "
            f"({mapping_rule})."
        )
    elif category == "__unrecognised__":
        category_note = (
            f"Category {category_received!r} was not one of {KNOWN_CATEGORIES}; defaulted to "
            f"minor per {pin.name}'s mapping rule ({mapping_rule})."
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
