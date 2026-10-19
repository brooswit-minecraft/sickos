"""Generic Modrinth version-file identity/hash verification.

Extracted from bump-dynamic-atmosphere.py's verify_against_modrinth_metadata
(SICKOS-79/MINECRAFT-362) so the Dynamic Atmosphere engine and the
generalized multi-mod resolver (scripts/mod_eligibility.py,
scripts/modrinth_compat.py) verify a Modrinth version-file's identity
against an expected payload through exactly one implementation, instead of
two copies that could silently drift. Stdlib only; makes no network calls
itself -- callers supply the already-fetched metadata dict.
"""

from __future__ import annotations


class MetadataVerificationError(Exception):
    """A human-readable reason a version-file's metadata failed verification.

    Callers decide the exit code / outcome; this module only decides
    whether the metadata actually matches what was expected.
    """


def verify_version_file_metadata(
    metadata,
    *,
    expected_version_id,
    expected_project_id,
    expected_version_number,
    expected_file_name,
    expected_sha512,
    expected_sha1,
    expected_download_url,
    expected_game_version=None,
    expected_loader=None,
):
    """Return the matched Modrinth file entry, or raise MetadataVerificationError.

    Mirrors the checks bump-dynamic-atmosphere.py's AC3 gate has had
    reviewed and tested: version id, project id, version_number equality
    (so valid older bytes can never be represented as a newer version),
    exactly one file entry by filename, and sha512/sha1/url equality
    against that entry -- in that order, fail closed on the first mismatch.

    `expected_game_version`/`expected_loader` are optional (MINECRAFT-362
    review, seventeenth round): when BOTH are given, this metadata's own
    declared `game_versions`/`loaders` must include them, or this refuses
    too. This is a METADATA-level check -- the same fields this function
    already reads -- not a second, divergent compatibility filter: the
    generalized poll path's `modrinth_compat.resolve_latest_compatible_version`
    already filters on these same two fields before a candidate ever
    reaches here, so passing them through here is defense in depth for
    that path; for `bump-dynamic-atmosphere.py`'s dispatch path, which has
    no earlier compatibility filter AT ALL (it trusts an external
    webhook's payload directly), this is the ONLY place such a check can
    happen before a write -- omitting them here (the previous behavior)
    meant a dispatch for a build declaring the wrong Minecraft version or
    loader would still pin and publish successfully.
    """

    def refuse(reason):
        raise MetadataVerificationError(reason)

    if not isinstance(metadata, dict):
        refuse("metadata response was not a JSON object")
    if metadata.get("id") != expected_version_id:
        refuse(f"metadata id {metadata.get('id')!r} does not match the requested version id")
    if metadata.get("project_id") != expected_project_id:
        refuse(f"metadata project_id {metadata.get('project_id')!r} is not {expected_project_id!r}")
    if metadata.get("version_number") != expected_version_number:
        refuse(
            f"metadata version_number {metadata.get('version_number')!r} does not equal "
            f"expected version {expected_version_number!r}"
        )
    if expected_game_version is not None and expected_loader is not None:
        if expected_game_version not in (metadata.get("game_versions") or ()):
            refuse(
                f"metadata game_versions {metadata.get('game_versions')!r} does not include "
                f"this pack's target {expected_game_version!r}"
            )
        if expected_loader not in (metadata.get("loaders") or ()):
            refuse(
                f"metadata loaders {metadata.get('loaders')!r} does not include this pack's "
                f"target {expected_loader!r}"
            )

    files = metadata.get("files")
    if not isinstance(files, list):
        refuse("metadata has no files list")
    matches = [f for f in files if isinstance(f, dict) and f.get("filename") == expected_file_name]
    if len(matches) != 1:
        refuse(f"expected exactly one file named {expected_file_name!r}, found {len(matches)}")
    entry = matches[0]
    hashes = entry.get("hashes") if isinstance(entry.get("hashes"), dict) else {}
    if str(hashes.get("sha512", "")).lower() != expected_sha512:
        refuse("expected sha512 differs from Modrinth's published sha512")
    if str(hashes.get("sha1", "")).lower() != expected_sha1:
        refuse("expected sha1 differs from Modrinth's published sha1")
    if entry.get("url") != expected_download_url:
        refuse("expected download_url differs from Modrinth's published file url")
    return entry
