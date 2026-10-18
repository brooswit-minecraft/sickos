import importlib.util
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "modrinth_compat.py"
SPEC = importlib.util.spec_from_file_location("modrinth_compat", MODULE_PATH)
modrinth_compat = importlib.util.module_from_spec(SPEC)
sys.modules["modrinth_compat"] = modrinth_compat  # dataclass() needs this registered first
SPEC.loader.exec_module(modrinth_compat)

ELIGIBILITY_PATH = Path(__file__).parents[1] / "scripts" / "mod_eligibility.py"
ELIGIBILITY_SPEC = importlib.util.spec_from_file_location("mod_eligibility", ELIGIBILITY_PATH)
mod_eligibility = importlib.util.module_from_spec(ELIGIBILITY_SPEC)
sys.modules["mod_eligibility"] = mod_eligibility
ELIGIBILITY_SPEC.loader.exec_module(mod_eligibility)

resolve_latest_compatible_version = modrinth_compat.resolve_latest_compatible_version
decide_update = modrinth_compat.decide_update
find_dependency_conflicts = modrinth_compat.find_dependency_conflicts
CompatResolutionError = modrinth_compat.CompatResolutionError
EligiblePin = mod_eligibility.EligiblePin


def version_entry(version_id, version_number, *, game_versions=("1.21.1",), loaders=("neoforge",),
                   filename="mod.jar", sha1="a" * 40, sha512="b" * 128, url="https://cdn.modrinth.com/x.jar",
                   version_type="release", changelog=""):
    return {
        "id": version_id,
        "version_number": version_number,
        "version_type": version_type,
        "changelog": changelog,
        "game_versions": list(game_versions),
        "loaders": list(loaders),
        "dependencies": [],
        "files": [{
            "primary": True,
            "filename": filename,
            "hashes": {"sha1": sha1, "sha512": sha512},
            "url": url,
        }],
    }


def make_pin(**overrides):
    defaults = dict(pin_path="mods/example.pw.toml", name="Example", side="both",
                     mod_id="EXAMPLE", pinned_version_id="OLDID")
    defaults.update(overrides)
    return EligiblePin(**defaults)


class TestResolveLatestCompatibleVersion(unittest.TestCase):
    def test_picks_highest_semver_among_compatible_versions(self):
        fetcher = mock.Mock(return_value=[
            version_entry("V1", "1.0.0"),
            version_entry("V2", "1.2.0"),
            version_entry("V3", "1.1.0"),
        ])
        candidate = resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)
        self.assertEqual(candidate.version_id, "V2")
        self.assertEqual(candidate.version_number, "1.2.0")

    def test_filters_out_incompatible_game_version(self):
        fetcher = mock.Mock(return_value=[
            version_entry("V1", "9.0.0", game_versions=("1.20.1",)),
            version_entry("V2", "1.0.0"),
        ])
        candidate = resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)
        self.assertEqual(candidate.version_id, "V2")

    def test_filters_out_incompatible_loader(self):
        fetcher = mock.Mock(return_value=[
            version_entry("V1", "9.0.0", loaders=("fabric",)),
            version_entry("V2", "1.0.0"),
        ])
        candidate = resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)
        self.assertEqual(candidate.version_id, "V2")

    def test_no_compatible_version_returns_none(self):
        fetcher = mock.Mock(return_value=[version_entry("V1", "1.0.0", game_versions=("1.20.1",))])
        candidate = resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)
        self.assertIsNone(candidate)

    def test_empty_version_list_returns_none(self):
        fetcher = mock.Mock(return_value=[])
        candidate = resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)
        self.assertIsNone(candidate)

    def test_unparsable_version_number_skipped_not_crashed(self):
        fetcher = mock.Mock(return_value=[
            version_entry("V1", "not-a-semver"),
            version_entry("V2", "1.0.0"),
        ])
        candidate = resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)
        self.assertEqual(candidate.version_id, "V2")

    def test_non_list_response_raises(self):
        fetcher = mock.Mock(return_value={"not": "a list"})
        with self.assertRaises(CompatResolutionError):
            resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)

    def test_http_error_raises_compat_resolution_error(self):
        fetcher = mock.Mock(side_effect=urllib.error.HTTPError("url", 404, "not found", {}, None))
        with self.assertRaisesRegex(CompatResolutionError, "HTTP 404"):
            resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)

    def test_transport_error_raises_compat_resolution_error(self):
        fetcher = mock.Mock(side_effect=OSError("network down"))
        with self.assertRaisesRegex(CompatResolutionError, "failed"):
            resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)

    def test_real_xaeros_scheme_resolves_to_highest_after_normalization(self):
        # MINECRAFT-362 review, ninth round: Xaero's Minimap/World Map's
        # real scheme is "<loader>-<game_version>-<mod_version>" (e.g.
        # "neoforge-1.21.1-26.6.0"). Before stripping that prefix, every
        # entry failed parse_semver and this permanently returned None
        # (a silent, forever "no_candidate") despite dozens of real
        # compatible releases existing.
        fetcher = mock.Mock(return_value=[
            version_entry("V1", "neoforge-1.21.1-26.5.0"),
            version_entry("V2", "neoforge-1.21.1-26.6.0"),
            version_entry("V3", "neoforge-1.21.1-25.0.0"),
        ])
        candidate = resolve_latest_compatible_version(
            "XAERO", game_version="1.21.1", loader="neoforge", fetcher=fetcher,
        )
        self.assertEqual(candidate.version_id, "V2")

    def test_normalization_prevents_a_coincidental_raw_parse_from_winning(self):
        # If even one entry's raw (un-normalized) string happens to look
        # like valid semver, comparing it against OTHER entries' raw
        # strings could select a genuine downgrade. All entries must be
        # normalized the same way before comparison, not just skipped
        # individually when unparsable.
        fetcher = mock.Mock(return_value=[
            # Raw "3.0.3" parses as semver by coincidence and would sort
            # above the real-but-still-unstripped entries without
            # normalization.
            version_entry("OLD", "3.0.3"),
            version_entry("NEW", "neoforge-1.21.1-3.1.7"),
        ])
        candidate = resolve_latest_compatible_version(
            "CRISTELLIB", game_version="1.21.1", loader="neoforge", fetcher=fetcher,
        )
        self.assertEqual(candidate.version_id, "NEW")

    def test_all_compatible_entries_unparsable_is_a_distinct_error_not_none(self):
        # "Every compatible entry failed to parse" must not be
        # indistinguishable from "upstream has nothing newer" -- it is a
        # permanent, deterministic per-pin property (NoParseableCandidateError),
        # not the TRUE catch-all CompatResolutionError class (which
        # poll-mod-updates.py allowlists by mod_id, not unconditionally --
        # MINECRAFT-362 review, tenth round) and not the silent `None`
        # "nothing newer upstream" case either.
        fetcher = mock.Mock(return_value=[
            version_entry("V1", "not-a-version-at-all"),
        ])
        with self.assertRaises(modrinth_compat.NoParseableCandidateError):
            resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)

    def test_prerelease_that_sorts_higher_is_not_picked(self):
        fetcher = mock.Mock(return_value=[
            version_entry("V1", "1.2.0"),
            version_entry("V2", "1.3.0-beta.1", version_type="beta"),
        ])
        candidate = resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)
        self.assertEqual(candidate.version_id, "V1")

    def test_only_prerelease_available_raises_distinct_error_not_none(self):
        # MINECRAFT-362 review, tenth round: a compatible entry existing
        # but never being release-grade is NOT "nothing newer upstream"
        # (None) -- it is a different, permanent per-pin fact (the
        # project has never published a release for this target) with a
        # different remedy (nothing a human bump fixes; only the author
        # publishing a release does).
        fetcher = mock.Mock(return_value=[version_entry("V1", "1.0.0-alpha.1", version_type="alpha")])
        with self.assertRaises(modrinth_compat.OnlyPrereleaseAvailableError):
            resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)

    def test_genuinely_no_compatible_entry_at_all_returns_none(self):
        # The one case None still means: zero entries declare this
        # (game_version, loader) at all, regardless of version_type.
        fetcher = mock.Mock(return_value=[
            version_entry("V1", "1.0.0", game_versions=("1.20.1",), loaders=("fabric",)),
        ])
        candidate = resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)
        self.assertIsNone(candidate)

    def test_compatible_release_entry_with_no_files_also_returns_none_not_an_error(self):
        # MINECRAFT-362 review, eleventh round: the OTHER route to None --
        # distinct from test_genuinely_no_compatible_entry_at_all_returns_none
        # above. Here a compatible, release-grade entry DOES declare this
        # (game_version, loader), but version_candidate_from_json returns
        # None for it (an empty `files` array -- the same shape Modrinth
        # would send for a version with no uploaded jar). This falls
        # through the loop silently: it increments neither
        # unparsable_count nor prerelease_only_count, so it is
        # indistinguishable, from the caller's side, from "nothing newer
        # upstream" -- confirmed still current behavior, not a regression
        # introduced by this round's other fixes.
        entry = version_entry("V1", "1.0.0")
        entry["files"] = []
        fetcher = mock.Mock(return_value=[entry])
        candidate = resolve_latest_compatible_version("EXAMPLE", game_version="1.21.1", loader="neoforge", fetcher=fetcher)
        self.assertIsNone(candidate)


class TestMakeFetcher(unittest.TestCase):
    def test_no_token_sends_no_authorization_header(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"[]"

        def fake_urlopen(request, timeout):
            captured["headers"] = dict(request.header_items())
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            modrinth_compat.make_fetcher()("https://api.modrinth.com/v2/project/X/version")
        self.assertNotIn("Authorization", captured["headers"])

    def test_token_is_attached_as_authorization_header(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"[]"

        def fake_urlopen(request, timeout):
            captured["headers"] = dict(request.header_items())
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            modrinth_compat.make_fetcher(token="secret-token")("https://api.modrinth.com/v2/project/X/version")
        self.assertEqual(captured["headers"].get("Authorization"), "secret-token")


class TestDeriveCategoryFromChangelog(unittest.TestCase):
    def test_no_changelog_returns_none(self):
        self.assertIsNone(modrinth_compat.derive_category_from_changelog(""))
        self.assertIsNone(modrinth_compat.derive_category_from_changelog(None))

    def test_fix_heading_is_patch(self):
        self.assertEqual(modrinth_compat.derive_category_from_changelog("## Fixes\n- fixed a crash"), "patch")

    def test_feature_heading_is_minor(self):
        self.assertEqual(modrinth_compat.derive_category_from_changelog("## Features\n- added a thing"), "minor")

    def test_breaking_heading_is_breaking(self):
        self.assertEqual(modrinth_compat.derive_category_from_changelog("## Breaking\n- renamed a config key"), "breaking")

    def test_breaking_phrase_without_heading_is_not_breaking(self):
        # No heading -> None (unknown), not a free-text phrase match: a
        # bare substring search for "breaking change(s)" cannot tell a
        # heading announcing one apart from prose denying one (see the
        # next test), so only the heading form counts.
        self.assertIsNone(
            modrinth_compat.derive_category_from_changelog("This release has breaking changes to the API.")
        )

    def test_denial_of_breaking_changes_without_heading_is_not_breaking(self):
        # Previously matched "breaking" via the removed free-text phrase
        # regex (MINECRAFT-362 review); the actual heading present here
        # ("## Fixes") now correctly wins instead.
        self.assertEqual(
            modrinth_compat.derive_category_from_changelog("## Fixes\n- fixed a crash\n\nNo breaking changes."),
            "patch",
        )

    def test_mixed_fix_and_breaking_is_breaking_not_patch(self):
        changelog = "## Fixes\n- fixed a crash\n\n## Breaking\n- renamed a config key\n"
        self.assertEqual(modrinth_compat.derive_category_from_changelog(changelog), "breaking")

    def test_unrecognised_text_returns_none(self):
        self.assertIsNone(modrinth_compat.derive_category_from_changelog("Assorted improvements."))


class TestDecideUpdate(unittest.TestCase):
    def test_no_candidate(self):
        self.assertEqual(decide_update(make_pin(), None), "no_candidate")

    def test_already_pinned_by_version_id(self):
        pin = make_pin(pinned_version_id="V1")
        candidate = modrinth_compat.VersionCandidate(
            version_id="V1", version_number="1.0.0", file_name="mod.jar",
            sha1="a" * 40, sha512="b" * 128, download_url="https://x",
        )
        self.assertEqual(decide_update(pin, candidate), "already_pinned")

    def test_candidate_without_previous_version_number_is_a_candidate(self):
        pin = make_pin(pinned_version_id="OLDID")
        candidate = modrinth_compat.VersionCandidate(
            version_id="NEWID", version_number="1.0.0", file_name="mod.jar",
            sha1="a" * 40, sha512="b" * 128, download_url="https://x",
        )
        self.assertEqual(decide_update(pin, candidate), "candidate")

    def test_downgrade_refused_with_previous_version_number(self):
        pin = make_pin(pinned_version_id="OLDID")
        candidate = modrinth_compat.VersionCandidate(
            version_id="NEWID", version_number="1.0.0", file_name="mod.jar",
            sha1="a" * 40, sha512="b" * 128, download_url="https://x",
        )
        self.assertEqual(
            decide_update(pin, candidate, previous_version_number="2.0.0"),
            "downgrade_refused",
        )

    def test_same_version_number_refused_as_downgrade(self):
        pin = make_pin(pinned_version_id="OLDID")
        candidate = modrinth_compat.VersionCandidate(
            version_id="NEWID", version_number="1.0.0", file_name="mod.jar",
            sha1="a" * 40, sha512="b" * 128, download_url="https://x",
        )
        self.assertEqual(
            decide_update(pin, candidate, previous_version_number="1.0.0"),
            "downgrade_refused",
        )

    def test_advance_with_previous_version_number_is_a_candidate(self):
        pin = make_pin(pinned_version_id="OLDID")
        candidate = modrinth_compat.VersionCandidate(
            version_id="NEWID", version_number="2.0.0", file_name="mod.jar",
            sha1="a" * 40, sha512="b" * 128, download_url="https://x",
        )
        self.assertEqual(
            decide_update(pin, candidate, previous_version_number="1.0.0"),
            "candidate",
        )

    def test_unparsable_previous_version_fails_closed(self):
        pin = make_pin(pinned_version_id="OLDID")
        candidate = modrinth_compat.VersionCandidate(
            version_id="NEWID", version_number="1.0.0", file_name="mod.jar",
            sha1="a" * 40, sha512="b" * 128, download_url="https://x",
        )
        self.assertEqual(
            decide_update(pin, candidate, previous_version_number="not-a-version"),
            "unparsable_version",
        )

    def test_game_version_loader_normalization_detects_a_real_downgrade(self):
        # MINECRAFT-362 review, ninth round: Cristel Lib's real scheme
        # ("neoforge-1.21.1-3.1.7") compared RAW would read as unparsable
        # (fail closed, but for the wrong reason) or, worse, compare
        # incorrectly against another raw string. Normalized, a genuine
        # downgrade is correctly caught.
        pin = make_pin(pinned_version_id="OLDID")
        candidate = modrinth_compat.VersionCandidate(
            version_id="NEWID", version_number="neoforge-1.21.1-3.0.3", file_name="mod.jar",
            sha1="a" * 40, sha512="b" * 128, download_url="https://x",
        )
        self.assertEqual(
            decide_update(
                pin, candidate, previous_version_number="neoforge-1.21.1-3.1.7",
                game_version="1.21.1", loader="neoforge",
            ),
            "downgrade_refused",
        )

    def test_game_version_loader_normalization_detects_a_real_advance(self):
        pin = make_pin(pinned_version_id="OLDID")
        candidate = modrinth_compat.VersionCandidate(
            version_id="NEWID", version_number="neoforge-1.21.1-3.1.8", file_name="mod.jar",
            sha1="a" * 40, sha512="b" * 128, download_url="https://x",
        )
        self.assertEqual(
            decide_update(
                pin, candidate, previous_version_number="neoforge-1.21.1-3.1.7",
                game_version="1.21.1", loader="neoforge",
            ),
            "candidate",
        )


def make_candidate_with_deps(dependencies, **overrides):
    defaults = dict(
        version_id="NEWID", version_number="1.0.0", file_name="mod.jar",
        sha1="a" * 40, sha512="b" * 128, download_url="https://x",
        dependencies=tuple(dependencies),
    )
    defaults.update(overrides)
    return modrinth_compat.VersionCandidate(**defaults)


class TestFindDependencyConflicts(unittest.TestCase):
    def test_no_dependencies_no_conflicts(self):
        candidate = make_candidate_with_deps([])
        self.assertEqual(find_dependency_conflicts(candidate, {"OTHER1", "OTHER2"}), [])

    def test_incompatible_dependency_not_pinned_is_fine(self):
        candidate = make_candidate_with_deps(
            [{"project_id": "NOTPINNED", "dependency_type": "incompatible"}]
        )
        self.assertEqual(find_dependency_conflicts(candidate, {"OTHER1"}), [])

    def test_incompatible_dependency_that_is_pinned_is_a_conflict(self):
        candidate = make_candidate_with_deps(
            [{"project_id": "OTHER1", "dependency_type": "incompatible"}]
        )
        conflicts = find_dependency_conflicts(candidate, {"OTHER1", "OTHER2"})
        self.assertEqual(len(conflicts), 1)
        self.assertIn("OTHER1", conflicts[0])
        self.assertIn("incompatible", conflicts[0])

    def test_required_dependency_not_pinned_at_all_is_now_a_conflict(self):
        # MINECRAFT-362 review, eighteenth round, required item: a prior
        # round's stance ("Modrinth cannot tell us whether a missing
        # required dep is local-only/out-of-pack or genuinely missing,
        # so don't guess") produced an unsafe default -- unverifiable
        # was treated as fine, so a candidate requiring a project this
        # pack has never heard of would still bump. The correct default
        # is the opposite: refuse, with the only exception being a
        # project id on the confirmed KNOWN_LOCAL_ONLY_MODRINTH_PROJECT_IDS
        # allowlist (currently empty -- see the next test).
        candidate = make_candidate_with_deps(
            [{"project_id": "NOTPINNED", "dependency_type": "required"}]
        )
        conflicts = find_dependency_conflicts(candidate, {"OTHER1": "V1"})
        self.assertEqual(len(conflicts), 1)
        self.assertIn("NOTPINNED", conflicts[0])

    def test_required_dependency_on_a_known_local_only_project_is_not_reported(self):
        # The one carved-out exception: a project id explicitly confirmed
        # as one of mod_eligibility.EXCLUDED_MOD_NAMES' local-only addons.
        # Patches the module-level allowlist directly since it is empty
        # by default (no real Modrinth id has been confirmed for either
        # excluded mod in this codebase).
        candidate = make_candidate_with_deps(
            [{"project_id": "LOCAL_ONLY_PROJECT", "dependency_type": "required"}]
        )
        with mock.patch.object(modrinth_compat, "KNOWN_LOCAL_ONLY_MODRINTH_PROJECT_IDS", frozenset({"LOCAL_ONLY_PROJECT"})):
            self.assertEqual(find_dependency_conflicts(candidate, {"OTHER1": "V1"}), [])

    def test_required_dependency_with_no_exact_version_and_pinned_is_not_reported(self):
        # A `required` entry naming only a project (no version_id) is a
        # semver-RANGE requirement this pass cannot evaluate without a
        # full solve -- still out of scope when the project IS pinned
        # (MINECRAFT-362 review, seventeenth round: only an EXACT
        # version_id mismatch against an already-pinned project is
        # unambiguous enough to report). Being PINNED at all is what
        # makes this fine, unlike the not-pinned-at-all case above.
        candidate = make_candidate_with_deps(
            [{"project_id": "OTHER1", "dependency_type": "required"}]
        )
        self.assertEqual(find_dependency_conflicts(candidate, {"OTHER1": "V1"}), [])

    def test_required_dependency_at_exact_version_matching_pin_is_fine(self):
        candidate = make_candidate_with_deps(
            [{"project_id": "OTHER1", "dependency_type": "required", "version_id": "V1"}]
        )
        self.assertEqual(find_dependency_conflicts(candidate, {"OTHER1": "V1"}), [])

    def test_required_dependency_at_exact_version_not_matching_pin_is_a_conflict(self):
        # MINECRAFT-362 review, seventeenth round, required item: this is
        # the one case `find_dependency_conflicts` previously missed
        # entirely -- Modrinth already told this pack exactly which
        # version it needs, and this pack already has that project
        # pinned to a known, DIFFERENT one. No transitive solve needed,
        # unlike a semver-range `required` entry.
        candidate = make_candidate_with_deps(
            [{"project_id": "OTHER1", "dependency_type": "required", "version_id": "V2"}]
        )
        conflicts = find_dependency_conflicts(candidate, {"OTHER1": "V1"})
        self.assertEqual(len(conflicts), 1)
        self.assertIn("OTHER1", conflicts[0])
        self.assertIn("V1", conflicts[0])
        self.assertIn("V2", conflicts[0])

    def test_required_dependency_at_exact_version_not_pinned_at_all_is_a_conflict(self):
        # MINECRAFT-362 review, eighteenth round: same reversal as the
        # no-version-id case above -- not being pinned at all is itself
        # now a conflict, regardless of whether an exact version_id was
        # also given.
        candidate = make_candidate_with_deps(
            [{"project_id": "NOTPINNED", "dependency_type": "required", "version_id": "V2"}]
        )
        conflicts = find_dependency_conflicts(candidate, {"OTHER1": "V1"})
        self.assertEqual(len(conflicts), 1)
        self.assertIn("NOTPINNED", conflicts[0])
        self.assertIn("V2", conflicts[0])

    def test_optional_dependency_never_a_conflict(self):
        candidate = make_candidate_with_deps(
            [{"project_id": "OTHER1", "dependency_type": "optional"}]
        )
        self.assertEqual(find_dependency_conflicts(candidate, {"OTHER1"}), [])

    def test_multiple_incompatible_conflicts_all_reported(self):
        candidate = make_candidate_with_deps([
            {"project_id": "OTHER1", "dependency_type": "incompatible"},
            {"project_id": "OTHER2", "dependency_type": "incompatible"},
        ])
        conflicts = find_dependency_conflicts(candidate, {"OTHER1", "OTHER2"})
        self.assertEqual(len(conflicts), 2)

    def test_non_dict_dependency_entries_are_skipped_not_crashed(self):
        candidate = make_candidate_with_deps(["not-a-dict", 123, None])
        self.assertEqual(find_dependency_conflicts(candidate, {"OTHER1"}), [])


if __name__ == "__main__":
    unittest.main()
