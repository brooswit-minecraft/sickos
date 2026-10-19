#!/usr/bin/env python3
"""GitHub-release-asset auto-bump engine (MINECRAFT-362).

scripts/bump_mod.py's generalized engine covers every managed mod that
has a Modrinth project. Several do not and ship their jars as GitHub
Release assets instead, tracked via packwiz's own native [update.github]
pin schema (see scripts/mod_eligibility.py's own docstring). The ticket
owner's decisions on comment 47927 (scope) and comment 48415 (schema) of
MINECRAFT-362 were that these mods belong in THIS ticket's scope, reusing
the shared fail-closed gates against packwiz's REAL pin shape, not an
invented one and not a separate mechanism with weaker guarantees -- this
module is that reuse: identity/hash verification via
scripts/github_release_identity.py (GitHub's own sha256 digest, read
anonymously, in place of Modrinth's sha1/sha512 version-file metadata),
everything else -- pin write, pack.toml version bump, notes file, git
plumbing, refresh/check/build/dependency_closure gates,
check_runtime_compatibility_or_die -- loaded directly from
bump-dynamic-atmosphere.py via scripts/bump_mod.py's already-loaded
`_da` reference (not a second `_load_da_engine()` call: bump_mod.py's
own loader already registered that module in sys.modules under a fixed
key, so importing bump_mod and reusing `bump_mod._da` executes
bump-dynamic-atmosphere.py's module body exactly once, not twice).

Deliberately NOT implemented here, same reasoning scripts/bump_mod.py's
own docstring already gives for the generalized Modrinth path:
- No Modrinth-style find_dependency_conflicts check: GitHub releases
  carry no structured dependency metadata to check it against in the
  first place (see scripts/github_release_identity.py's own docstring).
  Required-dependency satisfaction is still fully covered by the real-
  jar `dependency_closure` gate below, which reads the jar's own
  manifest, not any API's metadata.
- No changelog-heading category escalation: a GitHub release body is
  free-form prose with no heading convention this repo has ever
  measured against real releases (scripts/bump_mod.py's own docstring
  notes the Modrinth-changelog-heading equivalent matched 0 of 15 real
  candidates anyway) -- category comes from the semver delta alone.
- No allow_breaking/migration-text CLI path: none of these four mods
  has shipped a major-version release yet to design against; if/when
  one does, the breaking_change_requires_human_review gate below refuses
  it unattended, same as the generalized Modrinth path's own gate, and
  a human-run CLI can be added then with a real case to design around.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bump_mod  # noqa: E402 - needs sys.path set up first; also loads `_da`
import github_release_identity  # noqa: E402
import mod_eligibility  # noqa: E402
import semver_compare  # noqa: E402

_da = bump_mod._da

EngineError = _da.EngineError
EXIT_OK = _da.EXIT_OK
EXIT_INTEGRITY_FAILURE = _da.EXIT_INTEGRITY_FAILURE
EXIT_DOWNGRADE_REFUSED = _da.EXIT_DOWNGRADE_REFUSED
EXIT_GATE_FAILURE = _da.EXIT_GATE_FAILURE
EXIT_NOTES_EXISTS = _da.EXIT_NOTES_EXISTS
EXIT_CONFIG_ERROR = _da.EXIT_CONFIG_ERROR
EXIT_UNEXPECTED_ERROR = _da.EXIT_UNEXPECTED_ERROR

load_pack_version = _da.load_pack_version
bump_pack_version = _da.bump_pack_version
write_pack_version = _da.write_pack_version
check_runtime_compatibility_or_die = _da.check_runtime_compatibility_or_die
default_gate_commands = _da.default_gate_commands
run_gate = _da.run_gate
toml_escape = _da.toml_escape

# Pin-path-generic git plumbing: these already take pin_path as a
# parameter in scripts/bump_mod.py, not hardcoded to anything
# Modrinth-specific, so reused directly rather than redefined a third
# time (MINECRAFT-362 review).
assert_restore_targets_clean = bump_mod.assert_restore_targets_clean
restore_repo = bump_mod.restore_repo
stage_intended_files = bump_mod.stage_intended_files
assert_only_pin_changed_under_mods = bump_mod.assert_only_pin_changed_under_mods

default_downloader = github_release_identity.default_downloader


def render_github_pin(name, filename, side, download_url, sha256, slug, tag, source_sha, regex):
    """Native packwiz [update.github] shape (slug/tag/branch/regex -- see
    scripts/mod_eligibility.py's own docstring), NOT the owner/repo pair
    an earlier round of this ticket invented. `source_sha` is written to
    `branch` as the new release's own resolved, immutable commit (see
    github_release_identity.resolve_source_sha) -- a record of
    provenance, never read back for discovery (MINECRAFT-362 review,
    comment 48415)."""
    return (
        f'name = "{toml_escape(name)}"\n'
        f'filename = "{toml_escape(filename)}"\n'
        f'side = "{toml_escape(side)}"\n'
        "\n"
        "[download]\n"
        f'url = "{toml_escape(download_url)}"\n'
        'hash-format = "sha256"\n'
        f'hash = "{toml_escape(sha256)}"\n'
        "\n"
        "[update]\n"
        "[update.github]\n"
        f'slug = "{toml_escape(slug)}"\n'
        f'tag = "{toml_escape(tag)}"\n'
        f'branch = "{toml_escape(source_sha)}"\n'
        f'regex = "{toml_escape(regex)}"\n'
    )


MAX_CHANGELOG_CHARS = bump_mod.MAX_CHANGELOG_CHARS
_fenced = bump_mod._fenced


def render_notes(*, pin, new_pack_version, previous_pack_version, candidate, mapping_rule, category_note):
    lines = [
        f"# Sickos {new_pack_version}",
        "",
        f"{pin.name}: {candidate.version_number} "
        f"(GitHub release https://github.com/{candidate.slug}/releases/tag/{candidate.tag})",
        f"Previous Sickos version: {previous_pack_version}",
        f"Mapping rule that fired: `{mapping_rule}`",
        "",
    ]
    if category_note:
        lines.append(category_note)
        lines.append("")
    if candidate.body:
        lines.append(f"## {pin.name}'s own GitHub release notes")
        lines.append("")
        lines.append(_fenced(candidate.body))
        lines.append("")
    lines.append("## Listing review")
    lines.append("")
    lines.append(
        f"listing_review_required is always true for a generalized {pin.name} bump: this "
        "engine has no per-mod listing-text heuristic, so README.md and "
        ".modrinth/description.md are NOT inspected or rewritten automatically and may now be "
        "stale. A human should check them before this release ships."
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
        "pin_path": pin.pin_path,
        "candidate_tag": None,
        "candidate_version_number": None,
        "previous_pack_version": None,
        "new_pack_version": None,
        "mapping_rule": None,
        "notes_path": None,
        "listing_review_required": False,
        "error": None,
    }


def run_engine(repo_root, pin, candidate, *, downloader=default_downloader, run_command=subprocess.run,
                gate_commands=None):
    """Bump `pin` (scripts/mod_eligibility.GithubPin) to `candidate`
    (scripts/github_release_identity.GithubReleaseCandidate). Returns
    (summary_dict, exit_code, human_message), matching scripts/bump_mod.
    py's run_engine shape so scripts/poll-mod-updates.py can reuse the
    same decide/report pattern for both engines.
    """
    repo_root = Path(repo_root)

    assert_restore_targets_clean(repo_root, pin.pin_path, run_command=run_command)

    if candidate.tag == pin.pinned_tag:
        return (
            {
                "changed": False,
                "outcome": "already_pinned",
                "mod_name": pin.name,
                "pin_path": pin.pin_path,
                "candidate_tag": candidate.tag,
                "candidate_version_number": candidate.version_number,
                "previous_pack_version": load_pack_version(repo_root),
                "new_pack_version": None,
                "mapping_rule": None,
                "notes_path": None,
                "listing_review_required": False,
                "error": None,
            },
            EXIT_OK,
            f"Already pinned: {pin.name} is already at {pin.pinned_tag}. No changes made.",
        )

    if not pin.pinned_tag:
        return (
            _no_candidate_summary(pin),
            EXIT_OK,
            f"{pin.name} has no currently-pinned tag to compare against. No changes made.",
        )

    # Re-verify the CURRENTLY-pinned tag's own metadata against GitHub
    # fresh, right now -- never trust the on-disk pin's tag string alone
    # for the no-downgrade comparison below, same "never trust a prior
    # lookup this call did not itself verify" principle scripts/
    # bump_mod.py's own run_engine applies to Modrinth's previously-
    # pinned version metadata.
    try:
        previous_release = github_release_identity.fetch_release_by_tag_json(
            pin.slug, pin.pinned_tag,
        )
    except github_release_identity.GithubIdentityError as error:
        raise EngineError(
            EXIT_INTEGRITY_FAILURE, "integrity_failure",
            f"could not verify {pin.name} is not a downgrade: looking up the currently-pinned "
            f"tag {pin.pinned_tag!r} failed: {error.message}",
        ) from error
    previous_version_number = github_release_identity._tag_to_version_number(
        previous_release.get("tag_name", pin.pinned_tag),
    )

    try:
        comparison = semver_compare.compare_semver(previous_version_number, candidate.version_number)
    except ValueError:
        raise EngineError(
            EXIT_CONFIG_ERROR, "unsupported_version_scheme",
            f"cannot compare non-semver version(s) for {pin.name}: "
            f"{candidate.version_number!r}, {previous_version_number!r}",
        )
    if comparison > 0:
        raise EngineError(
            EXIT_DOWNGRADE_REFUSED, "downgrade_refused",
            f"candidate version {candidate.version_number} for {pin.name} is not newer than "
            f"the currently pinned {previous_version_number}; refusing to downgrade",
        )
    if comparison == 0:
        # Same version number under a different tag spelling (e.g. a
        # re-tag) -- nothing to do, not an error.
        return (
            {
                **_no_candidate_summary(pin),
                "outcome": "already_pinned",
                "candidate_tag": candidate.tag,
                "candidate_version_number": candidate.version_number,
                "previous_pack_version": load_pack_version(repo_root),
            },
            EXIT_OK,
            f"Already pinned: {pin.name} candidate {candidate.version_number} is not newer "
            f"than the currently-pinned {previous_version_number}. No changes made.",
        )

    category = semver_compare.semver_delta_category(previous_version_number, candidate.version_number)

    previous_pack_version = load_pack_version(repo_root)
    new_pack_version, mapping_rule = bump_pack_version(previous_pack_version, category)

    if mapping_rule == "breaking_as_minor":
        # Same fail-closed-by-default shape scripts/bump_mod.py's own
        # run_engine uses: this engine has no migration/reset-
        # instructions mechanism of its own (see this module's
        # docstring), so a breaking change is refused unattended rather
        # than auto-publishing a placeholder in place of real
        # instructions.
        raise EngineError(
            _da.EXIT_MIGRATION_MISSING, "breaking_change_requires_human_review",
            f"{pin.name} candidate {candidate.version_number} is a breaking change; this "
            "engine has no migration/reset-instructions mechanism of its own. Refusing to "
            "auto-publish a placeholder in their place -- a human must review this release "
            "and publish it by hand.",
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
            f"component; defaulted to minor per {pin.name}'s mapping rule ({mapping_rule})."
        )
    else:
        category_note = (
            f"Category: {category!r} -- semver delta {previous_version_number} -> "
            f"{candidate.version_number}."
        )

    try:
        jar_bytes = github_release_identity.download_and_verify_sha256(
            candidate.download_url, candidate.sha256, downloader,
        )
    except github_release_identity.GithubIdentityError as error:
        raise EngineError(EXIT_INTEGRITY_FAILURE, error.outcome, error.message) from error
    # Same real-jar platform/runtime check every other write path gets
    # (MINECRAFT-362 review, twenty-second round) -- see bump-dynamic-
    # atmosphere.py's own check_runtime_compatibility_or_die docstring.
    check_runtime_compatibility_or_die(
        jar_bytes, bump_mod.dependency_closure_check.pack_versions(repo_root / "pack.toml"),
    )

    # Resolve the NEW release's own target_commitish to an immutable
    # commit SHA before writing anything (MINECRAFT-362 review, comment
    # 48415) -- a failure here fails closed exactly like the identity/
    # hash checks above, before any file is touched.
    try:
        source_sha = github_release_identity.resolve_source_sha(
            pin.slug, candidate.source_commitish,
        )
    except github_release_identity.GithubIdentityError as error:
        raise EngineError(EXIT_INTEGRITY_FAILURE, error.outcome, error.message) from error

    notes_path = None
    try:
        new_pin_content = render_github_pin(
            pin.name, candidate.asset_name, pin.side,
            candidate.download_url, candidate.sha256,
            pin.slug, candidate.tag, source_sha, pin.regex,
        )
        (repo_root / pin.pin_path).write_text(new_pin_content, encoding="utf-8")

        write_pack_version(repo_root, previous_pack_version, new_pack_version)

        notes_content = render_notes(
            pin=pin, new_pack_version=new_pack_version, previous_pack_version=previous_pack_version,
            candidate=candidate, mapping_rule=mapping_rule, category_note=category_note,
        )
        notes_path = write_notes(repo_root, new_pack_version, notes_content)

        gates = gate_commands or default_gate_commands(repo_root)
        # Same ordering constraint as scripts/bump_mod.py's own
        # run_engine: refresh before staging, staging before check.
        run_gate(repo_root, run_command, gates, "refresh")
        stage_intended_files(repo_root, pin.pin_path, notes_path, run_command=run_command)
        run_gate(repo_root, run_command, gates, "check")
        run_gate(repo_root, run_command, gates, "build")
        run_gate(repo_root, run_command, gates, "dependency_closure")

        assert_only_pin_changed_under_mods(repo_root, pin.pin_path, run_command=run_command)
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
        "pin_path": pin.pin_path,
        "candidate_tag": candidate.tag,
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
# Commit message construction. Mirrors da-auto-bump-dispatch.py's own
# build_generic_commit_message shape, keyed by slug/tag identity instead
# of a Modrinth mod_id/version_id -- these mods have no Modrinth project
# at all (see this module's own docstring), so there is nothing to put
# in a "Modrinth mod id"/"Modrinth version id" line. Same Release-Only/
# Mod-Auto-Bump trailer shape; server_update_policy.should_deploy only
# ever reads Release-Only, so a GitHub-sourced bump's commit is read the
# exact same way a Modrinth-sourced one's is.
# --------------------------------------------------------------------------

def _load_da_dispatch_module():
    module_path = Path(__file__).resolve().parent / "da-auto-bump-dispatch.py"
    key = "da_auto_bump_dispatch_shared_github"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def build_github_commit_message(*, mapping_rule, new_pack_version, mod_name, slug, tag, version_number,
                                  source_text="scheduled GitHub-release reconciliation"):
    dispatch = _load_da_dispatch_module()
    commit_type = "feat" if mapping_rule in dispatch._FEAT_MAPPING_RULES else "fix"
    if mapping_rule == "breaking_as_minor":
        subject = (
            f"{commit_type}!: release Sickos {new_pack_version} pinning {mod_name} {version_number} (BREAKING)"
        )
    else:
        subject = f"{commit_type}: release Sickos {new_pack_version} pinning {mod_name} {version_number}"
    body = [
        f"Automated {mod_name} auto-bump via {source_text}.",
        "",
        f"- {mod_name} version: {version_number}",
        f"- GitHub release: https://github.com/{slug}/releases/tag/{tag}",
        f"- Mapping rule: {mapping_rule}",
    ]
    # Same Release-Only reasoning as build_generic_commit_message: an
    # automatic pack commit must never deploy/restart the server.
    trailer = f"Release-Only: true\nMod-Auto-Bump: github:{slug} {tag}"
    return subject + "\n\n" + "\n".join(body) + "\n\n" + trailer + "\n"
