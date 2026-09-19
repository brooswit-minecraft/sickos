#!/usr/bin/env python3
"""Bump the pinned Dynamic Atmosphere (DA) version from a repository_dispatch payload.

See docs/da-auto-bump.md for the full contract (args, exit codes, summary
fields, notes path, Migration extraction rule, vouch interpretation).
Never pushes, commits, tags, or touches Modrinth. Stdlib only.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "brooswit-minecraft/sickos (SICKOS-75 DA auto-bump engine)"
DA_MOD_ID = "PZV7RorC"
DA_RELEASE_OWNER = "brooswit-minecraft"
DA_RELEASE_REPO = "dynamic-atmosphere"
CONTROL_MOD_PATH = "mods/create.pw.toml"
DA_PIN_PATH = "mods/dynamic-atmosphere.pw.toml"
README_PATH = "README.md"
DESCRIPTION_PATH = ".modrinth/description.md"
FILENAME_PREFIX = "dynamicatmosphere-"
FILENAME_SUFFIX = ".jar"

# The fixed set of tracked paths restore_repo() ever touches. Kept as one
# constant so the dirty-tree guard below and the restore it guards can never
# drift apart from each other.
RESTORE_TARGET_PATHS = ("pack.toml", "index.toml", DA_PIN_PATH, README_PATH)

# Exit codes. 1 is reserved for Python's own unhandled-exception default,
# used only if something escapes main()'s own catch-all below (which should
# never happen). Any OTHER exception the engine itself catches -- a missing
# binary, an OSError writing a file, anything not raised as a designed
# EngineError -- is still fail-closed (the repo is restored) but reported as
# EXIT_UNEXPECTED_ERROR with a machine-readable summary, not left to exit 1.
EXIT_OK = 0
EXIT_INVALID_PAYLOAD = 2
EXIT_INTEGRITY_FAILURE = 3
EXIT_DOWNGRADE_REFUSED = 4
EXIT_ANOMALY = 5
EXIT_GATE_FAILURE = 6
EXIT_MIGRATION_MISSING = 7
EXIT_NOTES_EXISTS = 8
EXIT_CONFIG_ERROR = 9
EXIT_UNEXPECTED_ERROR = 10
EXIT_DIRTY_TREE = 11

REQUIRED_PAYLOAD_FIELDS = (
    "version", "modrinth_version_id", "sha1", "sha512",
    "download_url", "file_name", "category", "github_release_url",
)


class EngineError(Exception):
    """A designed, fail-closed stop. Carries the exit code and summary outcome."""

    def __init__(self, exit_code, outcome, message):
        super().__init__(message)
        self.exit_code = exit_code
        self.outcome = outcome
        self.message = message


# --------------------------------------------------------------------------
# Payload validation. Every payload field is untrusted input from the DA
# release workflow's repository_dispatch. Nothing here may be able to inject
# TOML syntax or a filesystem path.
# --------------------------------------------------------------------------

_SHA1_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SHA512_RE = re.compile(r"^[0-9a-fA-F]{128}$")
_MODRINTH_ID_RE = re.compile(r"^[A-Za-z0-9]+$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.jar$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9.+-]+$")
_GITHUB_RELEASE_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/releases/tag/(?P<tag>[A-Za-z0-9_.+-]+)$"
)
KNOWN_CATEGORIES = ("patch", "minor", "breaking")


def _require_str(payload, field):
    value = payload.get(field)
    if not isinstance(value, str) or value == "":
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload",
                           f"payload field {field!r} must be a non-empty string")
    return value


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload", "payload must be a JSON object")

    for field in REQUIRED_PAYLOAD_FIELDS:
        if field not in payload:
            raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload", f"payload is missing field {field!r}")

    sha1 = _require_str(payload, "sha1")
    if not _SHA1_RE.match(sha1):
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload", "sha1 must be exactly 40 hex characters")

    sha512 = _require_str(payload, "sha512")
    if not _SHA512_RE.match(sha512):
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload", "sha512 must be exactly 128 hex characters")

    modrinth_version_id = _require_str(payload, "modrinth_version_id")
    if not _MODRINTH_ID_RE.match(modrinth_version_id):
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload",
                           "modrinth_version_id must be alphanumeric")

    file_name = _require_str(payload, "file_name")
    if "/" in file_name or "\\" in file_name or file_name in (".", ".."):
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload",
                           "file_name must be a bare filename with no path separators")
    if not _FILENAME_RE.match(file_name):
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload",
                           "file_name must be a bare filename ending in .jar")

    download_url = _require_str(payload, "download_url")
    split = urllib.request.urlsplit(download_url)
    if split.scheme != "https" or split.netloc != "cdn.modrinth.com":
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload",
                           "download_url must be an https URL on cdn.modrinth.com")

    version = _require_str(payload, "version")
    if not _VERSION_RE.match(version):
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload",
                           "version must contain only letters, digits, '.', '+', '-'")

    github_release_url = _require_str(payload, "github_release_url")
    github_match = _GITHUB_RELEASE_URL_RE.match(github_release_url)
    if not github_match:
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload",
                           "github_release_url must look like https://github.com/<owner>/<repo>/releases/tag/<tag>")
    if (github_match.group("owner"), github_match.group("repo")) != (DA_RELEASE_OWNER, DA_RELEASE_REPO):
        raise EngineError(
            EXIT_INVALID_PAYLOAD, "invalid_payload",
            f"github_release_url must point at {DA_RELEASE_OWNER}/{DA_RELEASE_REPO}, got "
            f"{github_match.group('owner')}/{github_match.group('repo')}",
        )

    category = payload.get("category")
    if category is not None and not isinstance(category, str):
        raise EngineError(EXIT_INVALID_PAYLOAD, "invalid_payload",
                           "category must be a string or absent")

    return {
        "version": version,
        "modrinth_version_id": modrinth_version_id,
        "sha1": sha1.lower(),
        "sha512": sha512.lower(),
        "download_url": download_url,
        "file_name": file_name,
        "category": category,
        "github_release_url": github_release_url,
    }


# --------------------------------------------------------------------------
# Semver-with-prerelease comparison for DA version strings like
# "0.19.0-alpha.1". Standard semver precedence (see semver.org #11):
#   - compare (major, minor, patch) numerically first;
#   - a version WITH a prerelease suffix is OLDER than the same numeric
#     triple with no suffix;
#   - otherwise compare prerelease identifiers left to right, split on '.':
#     a numeric identifier compares numerically, an alphanumeric identifier
#     compares lexically (ASCII), a numeric identifier is always older than
#     an alphanumeric one, and if all shared identifiers are equal, the
#     prerelease with MORE identifiers is newer.
# Build metadata (a "+..." suffix) is not used by this pack's DA versions
# and is intentionally ignored for ordering if present, per semver's own
# rule that build metadata never affects precedence.
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# TOML read/write helpers. Reading uses stdlib tomllib. Writing is a small,
# careful hand-writer (packwiz's own pin format is a flat, known shape), so
# no third-party TOML library is needed. Every string value written is
# escaped as a TOML basic string.
# --------------------------------------------------------------------------

def toml_escape(value):
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def render_da_pin(name, filename, side, url, hash_value, mod_id, mod_version):
    return (
        f'name = "{toml_escape(name)}"\n'
        f'filename = "{toml_escape(filename)}"\n'
        f'side = "{toml_escape(side)}"\n'
        "\n"
        "[download]\n"
        f'url = "{toml_escape(url)}"\n'
        'hash-format = "sha512"\n'
        f'hash = "{toml_escape(hash_value)}"\n'
        "\n"
        "[update]\n"
        "[update.modrinth]\n"
        f'mod-id = "{toml_escape(mod_id)}"\n'
        f'version = "{toml_escape(mod_version)}"\n'
    )


def load_da_pin(repo_root):
    path = repo_root / DA_PIN_PATH
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    modrinth = data.get("update", {}).get("modrinth", {})
    filename = data.get("filename", "")
    if not (filename.startswith(FILENAME_PREFIX) and filename.endswith(FILENAME_SUFFIX)):
        raise EngineError(
            EXIT_CONFIG_ERROR, "config_error",
            f"cannot extract the currently pinned DA version from {DA_PIN_PATH}'s "
            f"filename {filename!r}; expected '{FILENAME_PREFIX}<version>{FILENAME_SUFFIX}'",
        )
    pinned_version = filename[len(FILENAME_PREFIX):-len(FILENAME_SUFFIX)]
    return {
        "name": data.get("name", "Dynamic Atmosphere"),
        "side": data.get("side", "both"),
        "filename": filename,
        "url": data.get("download", {}).get("url"),
        "hash": (data.get("download", {}).get("hash") or "").lower(),
        "hash_format": data.get("download", {}).get("hash-format"),
        "mod_id": modrinth.get("mod-id"),
        "modrinth_version_id": modrinth.get("version"),
        "pinned_version": pinned_version,
    }


def load_pack_version(repo_root):
    path = repo_root / "pack.toml"
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    version = data.get("version")
    if not isinstance(version, str) or not re.match(r"^\d+\.\d+\.\d+$", version):
        raise EngineError(EXIT_CONFIG_ERROR, "config_error",
                           f"pack.toml version {version!r} is not a plain x.y.z string")
    return version


def bump_pack_version(current, category):
    major, minor, patch = (int(part) for part in current.split("."))
    if category == "patch":
        return f"{major}.{minor}.{patch + 1}", "patch"
    if category == "minor":
        return f"{major}.{minor + 1}.0", "minor"
    if category == "breaking":
        return f"{major}.{minor + 1}.0", "breaking_as_minor"
    if category is None:
        return f"{major}.{minor + 1}.0", "minor_default_absent_category"
    return f"{major}.{minor + 1}.0", "minor_default_unrecognised_category"


def write_pack_version(repo_root, old_version, new_version):
    path = repo_root / "pack.toml"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r'(?m)^version = "' + re.escape(old_version) + r'"$')
    new_text, count = pattern.subn(f'version = "{new_version}"', text, count=1)
    if count != 1:
        raise EngineError(EXIT_CONFIG_ERROR, "config_error",
                           "could not find a single 'version = \"...\"' line in pack.toml to rewrite")
    path.write_text(new_text, encoding="utf-8")


_README_SENTENCE_RE = re.compile(
    r"^Sickos (?P<pack_version>\S+) pins Dynamic Atmosphere (?P<da_version>\S+) for client and server\.$",
    re.MULTILINE,
)


def rewrite_readme_sentence(repo_root, new_pack_version, new_da_version):
    path = repo_root / README_PATH
    text = path.read_text(encoding="utf-8")
    matches = list(_README_SENTENCE_RE.finditer(text))
    if len(matches) != 1:
        raise EngineError(
            EXIT_CONFIG_ERROR, "config_error",
            f"expected exactly one 'Sickos ... pins Dynamic Atmosphere ...' sentence in "
            f"{README_PATH}, found {len(matches)}",
        )
    match = matches[0]
    replacement = f"Sickos {new_pack_version} pins Dynamic Atmosphere {new_da_version} for client and server."
    new_text = text[: match.start()] + replacement + text[match.end():]
    path.write_text(new_text, encoding="utf-8")
    line_number = text.count("\n", 0, match.start()) + 1
    return line_number


# --------------------------------------------------------------------------
# AC9(b): listing/README lines that mention DA materials or behaviour and
# are NOT mechanically derivable, so they must be flagged for human review
# rather than rewritten. This is the one function that decides what gets
# auto-rewritten (the mechanical sentence, handled above) versus flagged
# (everything else here) -- kept isolated so a future machine-readable
# renames/listing section from DA's own contract could slot in without
# reworking the rest of the engine. No such section exists yet and this
# engine does not wait for one.
# --------------------------------------------------------------------------

_LISTING_KEYWORDS = (
    "dynamic atmosphere", "atmospher", "vapor", "smoke", "dust", "exhaust",
    "ender gas", "violence", "slime",
)


def _line_mentions_da(text):
    lowered = text.lower()
    return any(keyword in lowered for keyword in _LISTING_KEYWORDS)


def find_readme_listing_lines(repo_root, skip_line_number):
    path = repo_root / README_PATH
    lines = path.read_text(encoding="utf-8").splitlines()
    section_start = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        if section_start is None and line.strip() == "## Dynamic Atmosphere":
            section_start = index
            continue
        if section_start is not None and index > section_start and line.startswith("## "):
            section_end = index
            break
    flagged = []
    if section_start is not None:
        for index in range(section_start, section_end):
            line_number = index + 1
            if line_number == skip_line_number:
                continue
            if _line_mentions_da(lines[index]):
                flagged.append({"file": README_PATH, "line": line_number, "text": lines[index]})
    return flagged


def find_description_listing_lines(repo_root):
    path = repo_root / DESCRIPTION_PATH
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    flagged = []
    for index, line in enumerate(lines):
        if _line_mentions_da(line):
            flagged.append({"file": DESCRIPTION_PATH, "line": index + 1, "text": line})
    return flagged


# --------------------------------------------------------------------------
# AC7: Migration section extraction from the DA GitHub release body. DA's
# release body is a cumulative changelog with "# <version>" (heading level
# 1) sections, one per historical release, newest first. Only a
# "## Migration" heading (heading level 2, case-insensitive, nothing else
# on the line) found WITHIN this release's OWN top-level section counts --
# i.e. after the body's first "# ..." heading (this release's own) and
# before the next "# " heading (the start of the previous release's
# section). The extracted text is everything between the "## Migration"
# heading line and the next heading line of level 1 or 2 (or end of body),
# copied byte-for-byte with no reformatting or trimming beyond stripping
# the single leading/trailing blank line directly adjacent to the heading
# markers.
#
# Heading detection is fence-aware: a line inside a fenced code block
# (delimited by a line starting, after up to 3 leading spaces per
# CommonMark, with 3+ backticks or 3+ tildes, closed by a later such line
# using the same character with a run at least as long) is never treated as
# a heading, no matter what it starts with. Migration instructions routinely
# contain shell blocks with "#" comment lines, and without this a fenced
# "# step 1: ..." line would be misread as the next top-level heading and
# silently truncate the "verbatim" text mid-fence.
# --------------------------------------------------------------------------

_FENCE_MARKER_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_H1_LINE_RE = re.compile(r"^#\s+\S")
_H1_OR_H2_LINE_RE = re.compile(r"^#{1,2}\s+\S")
_MIGRATION_HEADING_LINE_RE = re.compile(r"(?i)^##\s+Migration\s*$")


def _iter_unfenced_lines(body):
    """Yield (start, end, line) for each line of `body` that is not inside a
    fenced code block, where start/end are character offsets into `body`
    and `end` excludes the line's own trailing newline. A fence line is one
    whose content, after up to 3 leading spaces, starts with 3+ backticks
    or 3+ tildes; the block it opens closes at the next such line using the
    same character with a run at least as long (an unterminated fence runs
    to the end of the body, and nothing inside it is ever yielded)."""
    in_fence = False
    fence_char = None
    fence_len = 0
    offset = 0
    for raw_line in body.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        line_end = offset + len(line)
        fence_match = _FENCE_MARKER_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence, fence_char, fence_len = True, marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                in_fence, fence_char, fence_len = False, None, 0
        elif not in_fence:
            yield offset, line_end, line
        offset += len(raw_line)


def _find_heading_offsets(body, line_re):
    return [(start, end) for start, end, line in _iter_unfenced_lines(body) if line_re.match(line)]


def extract_own_release_section(body):
    offsets = _find_heading_offsets(body, _H1_LINE_RE)
    if not offsets:
        return body
    start = offsets[0][0]
    end = offsets[1][0] if len(offsets) > 1 else len(body)
    return body[start:end]


def extract_migration_section(body):
    own_section = extract_own_release_section(body)
    heading_offsets = _find_heading_offsets(own_section, _MIGRATION_HEADING_LINE_RE)
    if not heading_offsets:
        return None
    heading_end = heading_offsets[0][1]
    remainder = own_section[heading_end:]
    next_headings = _find_heading_offsets(remainder, _H1_OR_H2_LINE_RE)
    section_text = remainder[: next_headings[0][0]] if next_headings else remainder
    return section_text.strip("\n")


def fetch_release_body(github_release_url, override_path):
    if override_path is not None:
        return Path(override_path).read_text(encoding="utf-8")
    match = _GITHUB_RELEASE_URL_RE.match(github_release_url)
    owner, repo, tag = match.group("owner"), match.group("repo"), match.group("tag")
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
    request = urllib.request.Request(api_url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError) as error:
        raise EngineError(EXIT_MIGRATION_MISSING, "migration_fetch_failed",
                           f"could not fetch GitHub release body for {tag}: {error}") from error
    return data.get("body") or ""


# --------------------------------------------------------------------------
# AC8: Modrinth vouch check. Never fails the run or changes the exit code --
# so the whole check, including reading the control mod's pin file, is
# wrapped: ANY exception (a transport failure urllib does not wrap in
# URLError, such as http.client.RemoteDisconnected; a missing or unreadable
# mods/create.pw.toml; anything else) is recorded as "inconclusive" with the
# raw error text and never propagates out of this function.
# Interpretation (also documented in docs/da-auto-bump.md and the notes
# file written per run):
#   control=200, da=404  -> "not_vouched"              (expected today; PZV7RorC is private)
#   control=200, da=200  -> "vouched"
#   control=404 (any da) -> "inconclusive_control_failed" (query/endpoint itself is wrong)
#   either status is anything else (429, 5xx, transport error, or an
#   exception caught here)
#                         -> "inconclusive" with the raw status/error recorded, NEVER collapsed into 404
# --------------------------------------------------------------------------

def _version_file_status(sha512):
    url = f"https://api.modrinth.com/v2/version_file/{sha512}?algorithm=sha512"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except urllib.error.URLError:
        return None
    except Exception:  # noqa: BLE001 - e.g. http.client.RemoteDisconnected, never URLError-wrapped
        return None


def run_vouch_check(repo_root, da_sha512):
    try:
        control_path = repo_root / CONTROL_MOD_PATH
        with control_path.open("rb") as handle:
            control_data = tomllib.load(handle)
        control_sha512 = (control_data.get("download", {}).get("hash") or "").lower()

        control_status = _version_file_status(control_sha512)
        da_status = _version_file_status(da_sha512)
    except Exception as error:  # noqa: BLE001 - vouch check must never fail the bump
        return {
            "control_status": None, "da_status": None,
            "result": "inconclusive", "error": repr(error),
        }

    if control_status != 200:
        result = "inconclusive_control_failed"
    elif da_status not in (200, 404):
        result = "inconclusive"
    elif da_status == 404:
        result = "not_vouched"
    else:
        result = "vouched"
    if control_status not in (200, 404):
        result = "inconclusive"

    return {"control_status": control_status, "da_status": da_status, "result": result, "error": None}


# --------------------------------------------------------------------------
# AC5: pack gates. Commands are overridable via env vars so tests can stub
# them without Go/packwiz installed, and so CI/local stay in sync with the
# Makefile's own PACKWIZ override convention.
# --------------------------------------------------------------------------

def default_gate_commands(repo_root):
    return {
        "refresh": shlex.split(os.environ.get("DA_BUMP_REFRESH_CMD", "make refresh")),
        "check": shlex.split(os.environ.get("DA_BUMP_CHECK_CMD", "make check")),
        "build": shlex.split(os.environ.get("DA_BUMP_BUILD_CMD", "make build")),
    }


def run_gate(repo_root, run_command, commands, name):
    result = run_command(commands[name], cwd=str(repo_root))
    if result.returncode != 0:
        raise EngineError(EXIT_GATE_FAILURE, "gate_failed", f"'make {name}' failed (exit {result.returncode})")


# --------------------------------------------------------------------------
# Integrity: download the jar to a temp dir OUTSIDE the repo and verify
# both hashes before anything is written to the repo.
# --------------------------------------------------------------------------

def download_and_verify(download_url, expected_sha1, expected_sha512, downloader):
    fd, temp_path = tempfile.mkstemp(prefix="da-bump-", suffix=".jar")
    os.close(fd)
    try:
        try:
            downloader(download_url, temp_path)
        except Exception as error:  # noqa: BLE001 - any download failure fails closed
            raise EngineError(EXIT_INTEGRITY_FAILURE, "integrity_failure",
                               f"failed to download {download_url}: {error}") from error

        sha1 = hashlib.sha1()
        sha512 = hashlib.sha512()
        with open(temp_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha1.update(chunk)
                sha512.update(chunk)
        got_sha1 = sha1.hexdigest()
        got_sha512 = sha512.hexdigest()
        if got_sha1 != expected_sha1 or got_sha512 != expected_sha512:
            raise EngineError(
                EXIT_INTEGRITY_FAILURE, "integrity_failure",
                f"downloaded jar hash mismatch: sha1 expected={expected_sha1} got={got_sha1}, "
                f"sha512 expected={expected_sha512} got={got_sha512}",
            )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def default_downloader(url, dest_path):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        with open(dest_path, "wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)


# --------------------------------------------------------------------------
# Notes file (AC7).
# --------------------------------------------------------------------------

def render_notes(*, new_pack_version, previous_pack_version, payload, mapping_rule,
                  migration_text, vouch, listing_review_required, listing_flagged,
                  category_note):
    breaking = mapping_rule == "breaking_as_minor"
    heading = f"# Sickos {new_pack_version}" + (" -- BREAKING" if breaking else "")
    lines = [
        heading,
        "",
        f"Dynamic Atmosphere: {payload['version']} (Modrinth version id `{payload['modrinth_version_id']}`)",
        f"DA GitHub release: {payload['github_release_url']}",
        f"Previous Sickos version: {previous_pack_version}",
        f"Category received from the trigger payload: {payload['category']!r}",
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
            "breaking change. The Migration section below is copied verbatim, byte-for-byte, "
            "from the Dynamic Atmosphere GitHub release body."
        )
        lines.append("")
        lines.append("## Migration")
        lines.append("")
        lines.append(migration_text)
        lines.append("")

    lines.append("## Modrinth vouch check")
    lines.append("")
    lines.append(
        "Interpretation: control=200/DA=404 means DA genuinely is not vouched for by Modrinth "
        "(expected today, PZV7RorC is private); control=404 (any DA status) means the query or "
        "endpoint itself is wrong and this run says nothing about DA; control=200/DA=200 means "
        "vouched; any 429/5xx/transport error is recorded as inconclusive with its raw status, "
        "never collapsed into 404."
    )
    lines.append("")
    vouch_line = (
        f"Result: `{vouch['result']}` (control status={vouch['control_status']}, "
        f"DA status={vouch['da_status']})."
    )
    if vouch.get("error"):
        vouch_line += f" Error: {vouch['error']}"
    lines.append(vouch_line)
    lines.append("")

    lines.append("## Listing review")
    lines.append("")
    if listing_review_required:
        lines.append(
            "listing_review_required is true for this release category. The following "
            "README.md and .modrinth/description.md lines mention Dynamic Atmosphere materials "
            "or behaviour and were NOT auto-updated by this engine -- they may now be stale and "
            "need a human to check and rewrite them:"
        )
        lines.append("")
        for item in listing_flagged:
            lines.append(f"- {item['file']}:{item['line']}: {item['text']}")
        lines.append("")
    else:
        lines.append("listing_review_required is false for this release category (patch).")
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


# --------------------------------------------------------------------------
# git plumbing.
# --------------------------------------------------------------------------

def git(repo_root, *args, run_command=subprocess.run):
    result = run_command(["git", *args], cwd=str(repo_root), capture_output=True, text=True)
    return result


def stage_intended_files(repo_root, notes_path, run_command=subprocess.run):
    paths = [*RESTORE_TARGET_PATHS, str(notes_path.relative_to(repo_root))]
    run_command(["git", "add", "--", *paths], cwd=str(repo_root), check=True)


def assert_restore_targets_clean(repo_root, run_command=subprocess.run):
    """Refuse to run at all if any of RESTORE_TARGET_PATHS already has an
    uncommitted change (staged, unstaged, or untracked) before this run
    touches anything. Without this, restore_repo()'s fixed-path restore on a
    failed run would discard pre-existing edits to those paths that had
    nothing to do with this run, and a successful run would silently stage
    them into the bump -- see docs/da-auto-bump.md's Fail-closed restore
    section for the full story."""
    result = run_command(["git", "status", "--porcelain", "--", *RESTORE_TARGET_PATHS],
                          cwd=str(repo_root), capture_output=True, text=True)
    dirty = [line for line in result.stdout.splitlines() if line]
    if dirty:
        raise EngineError(
            EXIT_DIRTY_TREE, "dirty_tree",
            "refusing to run: " + ", ".join(RESTORE_TARGET_PATHS) + " must be clean before "
            "this engine starts (it restores or stages exactly these paths, so pre-existing "
            "uncommitted changes to them would be discarded on failure or stolen into the bump "
            "on success); git status --porcelain reports: " + "; ".join(dirty),
        )


def restore_repo(repo_root, notes_path=None, run_command=subprocess.run):
    run_command(["git", "restore", "--staged", "--worktree", "--", *RESTORE_TARGET_PATHS],
                cwd=str(repo_root), check=True)
    if notes_path is not None and notes_path.exists():
        # The notes file is untracked until staged; if the failure happened
        # after staging it, it must be unstaged too or it is left behind as
        # a staged-but-deleted ("AD") entry once we unlink it below.
        relative_notes_path = str(notes_path.relative_to(repo_root))
        run_command(["git", "restore", "--staged", "--", relative_notes_path],
                    cwd=str(repo_root), check=False, capture_output=True, text=True)
        notes_path.unlink()


def assert_only_da_pin_changed_under_mods(repo_root, run_command=subprocess.run):
    result = run_command(["git", "diff", "--name-only", "HEAD", "--", "mods"],
                          cwd=str(repo_root), capture_output=True, text=True)
    changed = [line for line in result.stdout.splitlines() if line]
    unexpected = [path for path in changed if path != DA_PIN_PATH]
    if unexpected:
        raise EngineError(EXIT_CONFIG_ERROR, "config_error",
                           f"unexpected changes under mods/: {unexpected}")


# --------------------------------------------------------------------------
# Engine entry point.
# --------------------------------------------------------------------------

def run_engine(repo_root, payload_raw, *, release_body_override=None,
                downloader=default_downloader, run_command=subprocess.run,
                gate_commands=None):
    repo_root = Path(repo_root)
    assert_restore_targets_clean(repo_root, run_command=run_command)
    payload = validate_payload(payload_raw)
    category_received = payload["category"]
    if category_received in KNOWN_CATEGORIES:
        category = category_received
    elif category_received is None:
        category = None
    else:
        category = "__unrecognised__"

    pin = load_da_pin(repo_root)
    previous_pack_version = load_pack_version(repo_root)

    if pin["modrinth_version_id"] == payload["modrinth_version_id"]:
        if pin["hash"] == payload["sha512"]:
            return {
                "changed": False,
                "outcome": "already_pinned",
                "previous_pack_version": previous_pack_version,
                "new_pack_version": None,
                "da_version": payload["version"],
                "modrinth_version_id": payload["modrinth_version_id"],
                "sha512": payload["sha512"],
                "category_received": category_received,
                "mapping_rule": None,
                "notes_path": None,
                "vouch": None,
                "listing_review_required": False,
                "listing_flagged_lines": [],
                "error": None,
            }, EXIT_OK, "Already pinned: Dynamic Atmosphere is already at this version. No changes made."
        raise EngineError(
            EXIT_ANOMALY, "anomaly_hash_mismatch",
            "modrinth_version_id matches the currently pinned version but the pinned sha512 "
            "differs from the payload's sha512 -- refusing to no-op on a hash anomaly",
        )

    try:
        comparison = compare_semver(payload["version"], pin["pinned_version"])
    except ValueError as error:
        raise EngineError(EXIT_CONFIG_ERROR, "config_error", str(error)) from error

    if comparison < 0:
        raise EngineError(
            EXIT_DOWNGRADE_REFUSED, "downgrade_refused",
            f"payload DA version {payload['version']} is older than the pinned "
            f"{pin['pinned_version']}; refusing to downgrade",
        )
    if comparison == 0:
        raise EngineError(
            EXIT_ANOMALY, "anomaly_same_version_different_id",
            f"payload DA version {payload['version']} equals the pinned version string but "
            f"modrinth_version_id differs ({payload['modrinth_version_id']!r} vs "
            f"{pin['modrinth_version_id']!r})",
        )

    new_pack_version, mapping_rule = bump_pack_version(previous_pack_version, category)

    releases_dir = repo_root / "docs" / "releases"
    notes_target = releases_dir / f"{new_pack_version}.md"
    if notes_target.exists():
        raise EngineError(EXIT_NOTES_EXISTS, "notes_exists",
                           f"docs/releases/{new_pack_version}.md already exists; refusing to overwrite "
                           "a published release's notes")

    migration_text = None
    if mapping_rule == "breaking_as_minor":
        body = fetch_release_body(payload["github_release_url"], release_body_override)
        migration_text = extract_migration_section(body)
        if not migration_text:
            raise EngineError(
                EXIT_MIGRATION_MISSING, "migration_missing",
                "category is breaking but no '## Migration' section was found in this release's "
                "own section of the DA GitHub release body",
            )

    download_and_verify(payload["download_url"], payload["sha1"], payload["sha512"], downloader)

    vouch = run_vouch_check(repo_root, payload["sha512"])

    listing_review_required = mapping_rule != "patch"
    category_note = None
    if mapping_rule == "minor_default_absent_category":
        category_note = ("The trigger payload did not include a category. Minor was used as the default bump.")
    elif mapping_rule == "minor_default_unrecognised_category":
        category_note = (
            f"The trigger payload's category ({category_received!r}) was not one of "
            "patch/minor/breaking. Minor was used as the default bump."
        )

    notes_dir_existed_before = releases_dir.exists()
    notes_path = None
    try:
        new_pin_content = render_da_pin(
            pin["name"], payload["file_name"], pin["side"], payload["download_url"],
            payload["sha512"], DA_MOD_ID, payload["modrinth_version_id"],
        )
        (repo_root / DA_PIN_PATH).write_text(new_pin_content, encoding="utf-8")
        assert_only_da_pin_changed_under_mods(repo_root, run_command=run_command)

        write_pack_version(repo_root, previous_pack_version, new_pack_version)

        readme_line = rewrite_readme_sentence(repo_root, new_pack_version, payload["version"])

        listing_flagged = []
        if listing_review_required:
            listing_flagged = find_readme_listing_lines(repo_root, skip_line_number=readme_line)
            listing_flagged += find_description_listing_lines(repo_root)

        notes_content = render_notes(
            new_pack_version=new_pack_version, previous_pack_version=previous_pack_version,
            payload=payload, mapping_rule=mapping_rule, migration_text=migration_text,
            vouch=vouch, listing_review_required=listing_review_required,
            listing_flagged=listing_flagged, category_note=category_note,
        )
        notes_path = write_notes(repo_root, new_pack_version, notes_content)

        commands = gate_commands or default_gate_commands(repo_root)
        # refresh MUST run before staging: it regenerates index.toml, and
        # staging is what makes `make check`'s own `git diff --quiet` pass.
        run_gate(repo_root, run_command, commands, "refresh")
        stage_intended_files(repo_root, notes_path, run_command=run_command)
        run_gate(repo_root, run_command, commands, "check")
        run_gate(repo_root, run_command, commands, "build")
    except Exception:
        # Fail-closed applies to ANY exception here, not only a designed
        # EngineError: a missing make/packwiz binary, a CalledProcessError
        # from `git add`, an OSError writing a file, etc. must all leave the
        # repo exactly as it was before the run, same as a designed failure.
        restore_repo(repo_root, notes_path=notes_path, run_command=run_command)
        if not notes_dir_existed_before and releases_dir.exists() and not any(releases_dir.iterdir()):
            releases_dir.rmdir()
        raise

    summary = {
        "changed": True,
        "outcome": "bumped",
        "previous_pack_version": previous_pack_version,
        "new_pack_version": new_pack_version,
        "da_version": payload["version"],
        "modrinth_version_id": payload["modrinth_version_id"],
        "sha512": payload["sha512"],
        "category_received": category_received,
        "mapping_rule": mapping_rule,
        "notes_path": str(notes_path.relative_to(repo_root)),
        "vouch": vouch,
        "listing_review_required": listing_review_required,
        "listing_flagged_lines": listing_flagged,
        "error": None,
    }
    return summary, EXIT_OK, f"Bumped Sickos {previous_pack_version} -> {new_pack_version} (Dynamic Atmosphere {payload['version']})."


def build_error_summary(outcome, message, payload_raw):
    return {
        "changed": False,
        "outcome": outcome,
        "previous_pack_version": None,
        "new_pack_version": None,
        "da_version": payload_raw.get("version") if isinstance(payload_raw, dict) else None,
        "modrinth_version_id": payload_raw.get("modrinth_version_id") if isinstance(payload_raw, dict) else None,
        "sha512": payload_raw.get("sha512") if isinstance(payload_raw, dict) else None,
        "category_received": payload_raw.get("category") if isinstance(payload_raw, dict) else None,
        "mapping_rule": None,
        "notes_path": None,
        "vouch": None,
        "listing_review_required": False,
        "listing_flagged_lines": [],
        "error": message,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, help="path to the client_payload JSON file")
    parser.add_argument("--repo-root", default=".", help="the packwiz repo root to operate on (default: cwd)")
    parser.add_argument("--summary-out", default=None, help="also write the summary JSON to this path")
    parser.add_argument("--release-body", default=None,
                         help="local file to use instead of fetching the GitHub release body")
    args = parser.parse_args(argv)

    with open(args.payload, "r", encoding="utf-8") as handle:
        payload_raw = json.load(handle)

    try:
        summary, exit_code, message = run_engine(
            args.repo_root, payload_raw, release_body_override=args.release_body,
        )
    except EngineError as error:
        summary = build_error_summary(error.outcome, error.message, payload_raw)
        exit_code = error.exit_code
        message = error.message
    except Exception as error:  # noqa: BLE001 - always machine-readable output, never a bare traceback exit
        message = f"unexpected error: {error!r}"
        summary = build_error_summary("unexpected_error", message, payload_raw)
        exit_code = EXIT_UNEXPECTED_ERROR

    print(message, file=sys.stderr)
    summary_text = json.dumps(summary, indent=2, sort_keys=True)
    print(summary_text)
    if args.summary_out:
        Path(args.summary_out).write_text(summary_text + "\n", encoding="utf-8")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
