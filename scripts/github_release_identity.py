"""GitHub-release-asset identity resolution (MINECRAFT-362).

Several managed mods have no Modrinth project at all and ship their jars
as GitHub Release assets instead, tracked via packwiz's OWN native
[update.github] pin schema (`slug`, `tag`, `branch`, `regex` -- see
scripts/mod_eligibility.py's own docstring for the full story, including
why `branch` is a frozen commit SHA, never a mutable branch name, and is
never trusted for discovery). The ticket owner's decisions on comment
47927 (scope) and comment 48415 (schema -- "adapt the existing verified
adapter to native packwiz slug/branch/regex/tag") of MINECRAFT-362 were
that these mods belong in this ticket's scope, with verified-release
adapters reusing the shared fail-closed gates against packwiz's REAL
pin shape -- not an invented one, and not a separate mechanism with
weaker guarantees.

This module is the identity layer: anonymous, read-only GET requests to
api.github.com (same class of call already used elsewhere in this repo
to read PR reviews -- never a write, dispatch, or credentialed call),
returning a candidate whose own hash is independently verifiable the
same way a Modrinth VersionCandidate's sha1/sha512 is -- here, the
sha256 digest GitHub itself now attaches to a release asset's metadata
(confirmed live, read anonymously: api.github.com/repos/<slug>/releases
returns `assets[].digest` as `"sha256:<hex>"`).

Discovery deliberately does NOT reuse packwiz's own
getLatestRelease(slug, branch) shape (MINECRAFT-362 review, comment
48415, verified against pinned packwiz revision dfd8b68a): that
function, whenever `branch` is non-empty, filters for a release whose
OWN `target_commitish` equals that exact string -- and every real pin in
this pack stores `branch` as a frozen, exact 40-hex commit SHA from the
PREVIOUS bump, which a NEW release's target_commitish will essentially
never match again. Calling it with the pin's own `branch` value would
therefore reproduce packwiz's own permanent-freeze bug. Instead,
resolve_latest_matching_release below lists ALL releases for the slug
and picks the newest non-draft, non-prerelease one by `created_at`
(explicitly sorted, not trusted to already be ordered -- GitHub's docs
say the list endpoint returns newest-first, but this repo has been
burned before by trusting an API's implicit ordering without checking
it), completely independent of `branch`.

`branch` is still meaningful on the WRITE side: after a bump, the new
pin's `branch` is written as the resolved, *immutable* commit the new
release's own `target_commitish` names -- resolved via the commits API
when target_commitish is a mutable ref (a branch name) rather than
already a bare 40-hex SHA, so this never freezes a pin to a ref that can
move out from under it after the fact (see resolve_source_sha).

Deliberately narrower than modrinth_compat.py's VersionCandidate:
- No structured dependency data (GitHub releases carry no equivalent of
  Modrinth's `dependencies` array), so there is no
  find_dependency_conflicts analogue for a GitHub-sourced candidate.
  Required-dependency satisfaction is still fully covered by the same
  real-jar `dependency_closure` gate every other pin goes through
  (scripts/dependency_closure_check.py reads the jar's own manifest, not
  any API's metadata) -- this is the exact gap-filling relationship
  modrinth_compat's own docstring already describes for the generalized
  Modrinth path's narrowed dependency check, just total here instead of
  partial.
- Exactly one jar asset per release, selected by the pin's own `regex`
  the same "exactly-one-file" shape modrinth_metadata.
  verify_version_file_metadata already enforces for a Modrinth version --
  a release with zero or multiple matching assets has no unambiguous
  identity to pin. See `match_asset` for the one regex SHAPE this module
  understands (it fails closed, by name, on anything else, rather than
  silently mis-evaluating an exotic pattern): `^.+(?<!SUF1|SUF2|...)\\.jar$`
  or the no-lookbehind `^.+\\.jar$` -- every real pin in this pack uses
  the first shape today (packwiz's own install-command default), and
  Python's stdlib `re` cannot evaluate a variable-width negative
  lookbehind alternation like that one at all (this repo is stdlib-only,
  see bump-dynamic-atmosphere.py's own docstring), so this module
  special-cases exactly that shape instead of depending on a third-party
  regex engine.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "brooswit-minecraft/sickos (MINECRAFT-362 GitHub-release mod auto-bump engine)"
GITHUB_API_BASE = "https://api.github.com"

# Same sanity cap as bump-dynamic-atmosphere.py's MAX_JAR_SIZE_BYTES, kept
# independent rather than imported: this module must not depend on that
# one (see bump_mod_github.py for the reuse direction -- identity
# resolution has no reason to load the DA engine module at all).
MAX_ASSET_SIZE_BYTES = 512 * 1024 * 1024

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# The one regex shape this module evaluates without a third-party engine
# (see this module's own docstring): `^.+(?<!SUF1|SUF2|...)\.jar$`, with
# the lookbehind group optional. Group 1 (if present) is the pipe-
# separated list of excluded suffixes (each still missing its own
# literal "." -- packwiz's own default pattern writes "-api" meaning
# "-api.jar", not ".-api.jar").
_LOOKBEHIND_JAR_PATTERN = re.compile(r"^\^\.\+(?:\(\?<!([^)]+)\))?\\\.jar\$$")


class GithubIdentityError(Exception):
    """Carries an `outcome` string in the same vocabulary
    modrinth_compat.py's own resolver exceptions use
    (resolution_failed/no_parseable_candidate/only_prerelease_available/
    no_candidate), so callers (scripts/bump_mod_github.py,
    scripts/poll-mod-updates.py) can reconcile a GitHub-sourced pin's
    gaps against the exact same _is_expected_gap vocabulary a
    Modrinth-sourced pin's gaps already use, rather than inventing a
    second, parallel taxonomy."""

    def __init__(self, outcome, message):
        super().__init__(message)
        self.outcome = outcome
        self.message = message


@dataclasses.dataclass(frozen=True)
class GithubReleaseCandidate:
    slug: str
    tag: str
    version_number: str  # tag with a single leading "v" stripped, if present
    asset_name: str
    download_url: str
    sha256: str
    size: int
    body: str  # release notes text, GitHub's rough analogue of a Modrinth changelog
    source_commitish: str  # the release's OWN target_commitish, unresolved -- may be
    # a branch name or a SHA; see resolve_source_sha for turning this into the
    # immutable value actually written to the pin's `branch` field


def _tag_to_version_number(tag):
    return tag[1:] if tag.lower().startswith("v") and len(tag) > 1 and tag[1].isdigit() else tag


def _split_slug(slug):
    if "/" not in slug:
        raise GithubIdentityError("no_parseable_candidate", f"slug {slug!r} is not owner/repo-shaped")
    owner, repo = slug.split("/", 1)
    return owner, repo


def _api_get(path, *, token=None):
    url = f"{GITHUB_API_BASE}{path}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def fetch_releases_json(slug, *, token=None):
    """GET /repos/{slug}/releases -- the full list (newest-created-first
    per GitHub's docs, but resolve_latest_matching_release re-sorts
    rather than trusting that), INCLUDING drafts and prereleases (unlike
    `/releases/latest`) -- those are filtered explicitly below so this
    module controls that logic rather than depending on an endpoint this
    pack's discovery must NOT otherwise rely on (see this module's own
    docstring for why `/releases/latest` alone is not used: nothing
    about it is wrong, there is just no reason to use a second,
    differently-shaped endpoint when this one call already has to list
    everything to re-sort it)."""
    owner, repo = _split_slug(slug)
    try:
        return _api_get(f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/releases", token=token)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise GithubIdentityError("no_candidate", f"{slug} has no releases (404)") from error
        raise GithubIdentityError(
            "resolution_failed", f"GET /repos/{slug}/releases returned HTTP {error.code}",
        ) from error
    except urllib.error.URLError as error:
        raise GithubIdentityError(
            "resolution_failed", f"GET /repos/{slug}/releases failed before reaching GitHub: {error.reason}",
        ) from error


def fetch_release_by_tag_json(slug, tag, *, token=None):
    """GET /repos/{slug}/releases/tags/{tag} -- used by bump_mod_github.py
    to independently re-verify the CURRENTLY-pinned tag's own metadata
    still resolves before trusting its version_number for the
    no-downgrade check, the same "never trust a prior lookup this call
    did not itself verify" principle scripts/bump_mod.py's own run_engine
    already applies to the Modrinth path's previously-pinned version
    metadata."""
    owner, repo = _split_slug(slug)
    try:
        return _api_get(
            f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/releases/tags/{urllib.parse.quote(tag)}",
            token=token,
        )
    except urllib.error.HTTPError as error:
        raise GithubIdentityError(
            "integrity_failure", f"GET /repos/{slug}/releases/tags/{tag} returned HTTP {error.code}",
        ) from error
    except urllib.error.URLError as error:
        raise GithubIdentityError(
            "integrity_failure",
            f"GET /repos/{slug}/releases/tags/{tag} failed before reaching GitHub: {error.reason}",
        ) from error


def resolve_source_sha(slug, commitish, *, token=None):
    """Turn a release's own `target_commitish` into the exact, immutable
    commit SHA this module writes to a pin's `branch` field. Already a
    bare 40-hex SHA -> returned as-is, no extra call (every real pin
    resolved this way today happens to be this case, but a mod author is
    free to tag a release against a named branch instead, as
    magicavoxel-loader's real `MINECRAFT-284` target_commitish does --
    confirmed live, read anonymously). Otherwise resolved via GET
    /repos/{slug}/commits/{commitish}, the same anonymous, read-only
    call class used everywhere else in this module -- never trust a
    mutable ref as a frozen identity (MINECRAFT-362 review, comment
    48415's "resolve tag to immutable source")."""
    if _SHA_RE.match(commitish or ""):
        return commitish
    owner, repo = _split_slug(slug)
    try:
        data = _api_get(
            f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/commits/{urllib.parse.quote(commitish)}",
            token=token,
        )
    except urllib.error.HTTPError as error:
        raise GithubIdentityError(
            "integrity_failure",
            f"could not resolve {slug}@{commitish!r} to an immutable commit SHA: HTTP {error.code}",
        ) from error
    except urllib.error.URLError as error:
        raise GithubIdentityError(
            "integrity_failure",
            f"could not resolve {slug}@{commitish!r} to an immutable commit SHA: {error.reason}",
        ) from error
    sha = data.get("sha")
    if not sha or not _SHA_RE.match(sha):
        raise GithubIdentityError(
            "integrity_failure", f"GET /repos/{slug}/commits/{commitish} returned no usable commit sha",
        )
    return sha


def match_asset(regex, asset_names):
    """Evaluate `regex` (a pin's own [update.github].regex) against
    `asset_names`, returning the list of names it matches. Understands
    exactly two shapes (see this module's own docstring for why): plain
    `^.+\\.jar$`, and `^.+(?<!SUF1|SUF2|...)\\.jar$` where each SUFn is a
    bare suffix with no leading "." (packwiz's own convention -- "-api"
    means "-api.jar", not literal "." then "-api.jar"). Raises
    GithubIdentityError("no_parseable_candidate", ...) on anything else,
    rather than silently mis-evaluating an exotic pattern -- this is a
    documented limitation, not a bug: every real pin in this pack uses
    one of these two shapes today."""
    match = _LOOKBEHIND_JAR_PATTERN.match(regex or "")
    if not match:
        raise GithubIdentityError(
            "no_parseable_candidate",
            f"this module only understands the two regex shapes documented in its own "
            f"docstring (plain '^.+\\.jar$', or that with a negative-suffix lookbehind); "
            f"got {regex!r}, which is neither -- refusing to silently mis-evaluate it",
        )
    excluded_suffixes = tuple(f"{part}.jar" for part in match.group(1).split("|")) if match.group(1) else ()
    return [
        name for name in asset_names
        if name.endswith(".jar") and not any(name.endswith(suffix) for suffix in excluded_suffixes)
    ]


def candidate_from_release_json(slug, data, regex):
    """Build a GithubReleaseCandidate from one /releases/... response,
    selecting its asset via `match_asset(regex, ...)`. Fails closed
    (no_parseable_candidate) on anything ambiguous: zero or multiple
    matching assets, or a missing/non-sha256 digest -- the same
    "exactly one file, with a hash this code can independently verify"
    shape modrinth_metadata.py already requires of a Modrinth version."""
    tag = data.get("tag_name")
    if not tag:
        raise GithubIdentityError("no_parseable_candidate", f"{slug} release has no tag_name")
    assets = data.get("assets") or []
    matching_names = match_asset(regex, [asset.get("name", "") for asset in assets])
    if len(matching_names) != 1:
        raise GithubIdentityError(
            "no_parseable_candidate",
            f"{slug}@{tag} has {len(matching_names)} assets matching regex {regex!r}, not exactly "
            "1 -- no unambiguous jar identity to pin",
        )
    asset = next(asset for asset in assets if asset.get("name") == matching_names[0])
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise GithubIdentityError(
            "no_parseable_candidate",
            f"{slug}@{tag} asset {asset.get('name')!r} has no usable sha256 digest (got {digest!r})",
        )
    download_url = asset.get("browser_download_url")
    if not download_url:
        raise GithubIdentityError(
            "no_parseable_candidate", f"{slug}@{tag} asset {asset.get('name')!r} has no browser_download_url",
        )
    return GithubReleaseCandidate(
        slug=slug, tag=tag, version_number=_tag_to_version_number(tag),
        asset_name=asset["name"], download_url=download_url,
        sha256=digest.split(":", 1)[1], size=asset.get("size", 0),
        body=data.get("body") or "", source_commitish=data.get("target_commitish") or "",
    )


def resolve_latest_matching_release(slug, regex, *, token=None):
    """List every release for `slug`, keep only non-draft, non-prerelease
    ones, sort by `created_at` descending (explicitly -- see this
    module's own docstring on why this does not trust the list
    endpoint's own ordering), and build a candidate from the newest one.
    Deliberately does NOT filter by, or otherwise consult, any pin's own
    `branch`/`pinned_source` value -- that is exactly the discovery
    mechanism packwiz's own ghUpdater.CheckUpdate uses, and exactly the
    one a frozen-SHA `branch` value permanently breaks (MINECRAFT-362
    review, comment 48415)."""
    releases = fetch_releases_json(slug, token=token)
    candidates = [r for r in releases if not r.get("draft") and not r.get("prerelease")]
    if not candidates:
        raise GithubIdentityError("no_candidate", f"{slug} has no published, non-draft, non-prerelease release")
    candidates.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return candidate_from_release_json(slug, candidates[0], regex)


def default_downloader(url, dest_path):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        with open(dest_path, "wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)


def download_and_verify_sha256(download_url, expected_sha256, downloader):
    """Same download-to-temp-file-then-hash-then-read shape as
    bump-dynamic-atmosphere.py's own download_and_verify, independently
    implemented (not imported) because the hash algorithm is different
    (GitHub gives sha256, Modrinth gives sha1+sha512) and this module
    must not depend on that one -- see this module's own docstring.
    Returns the verified bytes; raises GithubIdentityError (translated
    to EngineError by the caller) on any download or hash-mismatch
    failure, or if the download exceeds MAX_ASSET_SIZE_BYTES."""
    fd, temp_path = tempfile.mkstemp(prefix="github-release-bump-", suffix=".jar")
    os.close(fd)
    try:
        try:
            downloader(download_url, temp_path)
        except Exception as error:  # noqa: BLE001 - any download failure fails closed
            raise GithubIdentityError(
                "integrity_failure", f"failed to download {download_url}: {error}",
            ) from error

        size = os.path.getsize(temp_path)
        if size > MAX_ASSET_SIZE_BYTES:
            raise GithubIdentityError(
                "integrity_failure",
                f"downloaded file from {download_url} is {size} bytes, over the "
                f"{MAX_ASSET_SIZE_BYTES}-byte sanity cap -- refusing to read it into memory",
            )

        sha256 = hashlib.sha256()
        with open(temp_path, "rb") as handle:
            data = handle.read()
        sha256.update(data)
        got_sha256 = sha256.hexdigest()
        if got_sha256 != expected_sha256:
            raise GithubIdentityError(
                "integrity_failure",
                f"downloaded asset hash mismatch: sha256 expected={expected_sha256} got={got_sha256}",
            )
        return data
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
