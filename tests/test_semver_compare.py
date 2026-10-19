import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "semver_compare.py"
SPEC = importlib.util.spec_from_file_location("semver_compare", MODULE_PATH)
semver_compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(semver_compare)

compare_semver = semver_compare.compare_semver
parse_semver = semver_compare.parse_semver


class TestCompareSemver(unittest.TestCase):
    def test_equal_versions(self):
        self.assertEqual(compare_semver("1.2.3", "1.2.3"), 0)

    def test_core_numeric_comparison(self):
        self.assertEqual(compare_semver("1.2.3", "1.3.0"), -1)
        self.assertEqual(compare_semver("2.0.0", "1.9.9"), 1)

    def test_prerelease_is_older_than_release(self):
        self.assertEqual(compare_semver("1.0.0-alpha.1", "1.0.0"), -1)
        self.assertEqual(compare_semver("1.0.0", "1.0.0-alpha.1"), 1)

    def test_prerelease_identifiers_compared_left_to_right(self):
        self.assertEqual(compare_semver("1.0.0-alpha.1", "1.0.0-alpha.2"), -1)
        self.assertEqual(compare_semver("1.0.0-alpha.9", "1.0.0-beta.1"), -1)

    def test_numeric_identifier_compares_numerically_not_lexically(self):
        self.assertEqual(compare_semver("1.0.0-alpha.9", "1.0.0-alpha.10"), -1)

    def test_numeric_identifier_always_older_than_alphanumeric(self):
        self.assertEqual(compare_semver("1.0.0-1", "1.0.0-alpha"), -1)

    def test_more_identifiers_is_newer_when_shared_prefix_equal(self):
        self.assertEqual(compare_semver("1.0.0-alpha", "1.0.0-alpha.1"), -1)

    def test_build_metadata_ignored(self):
        self.assertEqual(compare_semver("1.0.0+build1", "1.0.0+build2"), 0)

    def test_unparsable_raises(self):
        with self.assertRaises(ValueError):
            compare_semver("not-a-version", "1.0.0")
        with self.assertRaises(ValueError):
            compare_semver("1.0.0", "also-not")

    def test_parse_semver_returns_none_for_garbage(self):
        self.assertIsNone(parse_semver("not-a-version"))
        self.assertIsNone(parse_semver(""))
        # A bare trailing letter suffix with no separator (Create: Easy
        # Structures' real pinned "0.2a") is deliberately left
        # unparseable -- MINECRAFT-421 review, point 2: no single,
        # source-backed meaning this module can assign to the suffix
        # without guessing. See semver_compare.
        # normalize_native_version_scheme's own docstring.
        self.assertIsNone(parse_semver("0.2a"))


class TestNormalizeNativeVersionScheme(unittest.TestCase):
    """MINECRAFT-421 review, point 2: parse_semver now falls back to
    `normalize_native_version_scheme` for a handful of confirmed-live
    non-strict-SemVer shapes real enrolled mods publish, rather than
    treating every one of them as a permanent, unparseable gap. Each
    case below cites the real mod/version this shape was confirmed
    against."""

    def test_leading_v_before_digit_is_stripped(self):
        # Dungeons and Taverns' real candidate "v4.4.4+mod".
        self.assertEqual(parse_semver("v4.4.4+mod"), parse_semver("4.4.4+mod"))
        self.assertIsNotNone(parse_semver("v4.4.4+mod"))

    def test_two_component_version_gets_a_zero_patch(self):
        # Create: Dynamic Village "0.5", Create: Pillager Arise "132.36",
        # Geophilic "3.7".
        self.assertEqual(parse_semver("0.5"), parse_semver("0.5.0"))
        self.assertEqual(parse_semver("132.36"), parse_semver("132.36.0"))
        self.assertEqual(parse_semver("3.7"), parse_semver("3.7.0"))

    def test_two_component_version_with_prerelease_gets_a_zero_patch(self):
        # IceAndFire Community Edition's real pinned "2.1-beta.1".
        self.assertEqual(parse_semver("2.1-beta.1"), parse_semver("2.1.0-beta.1"))

    def test_four_component_version_folds_the_fourth_into_prerelease(self):
        # Biomes O' Plenty "21.1.0.14", GlitchCore "2.1.0.2" -- both
        # always publish all four components, so every real comparison
        # for either pin has this mapped prerelease on BOTH sides.
        self.assertEqual(parse_semver("21.1.0.14"), parse_semver("21.1.0-14"))
        self.assertGreater(compare_semver("21.1.0.14", "21.1.0.2"), 0)
        self.assertLess(compare_semver("2.0.0.9", "2.0.0.10"), 0)

    def test_four_component_with_its_own_prerelease_is_refused_not_guessed(self):
        # No confirmed-live pin has both a 4th numeric component and its
        # own "-prerelease" -- refuse to guess which one wins rather than
        # silently picking one.
        self.assertIsNone(parse_semver("1.2.3.4-rc.1"))

    def test_already_valid_semver_is_untouched(self):
        self.assertEqual(
            semver_compare.normalize_native_version_scheme("1.2.3-beta.1+build"),
            "1.2.3-beta.1+build",
        )

    def test_bare_trailing_letter_suffix_is_not_recognised(self):
        # Create: Easy Structures' real pinned "0.2a" -- deliberately
        # left as a documented, remaining ambiguity, not guessed.
        self.assertEqual(semver_compare.normalize_native_version_scheme("0.2a"), "0.2a")


class TestCompareSemverCrossSchemeGuard(unittest.TestCase):
    """MINECRAFT-421 review, point 3: comparing a raw four-numeric-
    component native version (Biomes O' Plenty/GlitchCore's own scheme)
    against a DIFFERENTLY-shaped string must refuse, not silently invent
    precedence by folding only one side into SemVer's prerelease slot.
    Confirmed-live exploit: "2.1.0.2" folds to "2.1.0-2", which SemVer's
    "no prerelease beats any prerelease" rule then ranks OLDER than a
    bare "2.1.0" -- even though nothing establishes the two schemes
    compare that way at all."""

    def test_four_component_against_plain_three_component_is_refused(self):
        with self.assertRaises(ValueError):
            compare_semver("2.1.0.2", "2.1.0")
        with self.assertRaises(ValueError):
            compare_semver("2.1.0", "2.1.0.2")

    def test_four_component_against_four_component_compares_normally(self):
        self.assertGreater(compare_semver("2.1.0.3", "2.1.0.2"), 0)

    def test_four_component_against_a_real_prerelease_is_refused(self):
        # The right side here is ordinary SemVer (not four-component),
        # so this is the same cross-scheme mismatch, not a legitimate
        # prerelease comparison.
        with self.assertRaises(ValueError):
            compare_semver("2.1.0.2", "2.1.0-beta.1")

    def test_neither_side_four_component_is_unaffected(self):
        self.assertEqual(compare_semver("1.2.3", "1.2.3"), 0)

    def test_v_prefixed_four_component_against_plain_three_component_is_refused(self):
        # MINECRAFT-421 review, point 3, second round (comment 48554,
        # P1): a leading "v"/"V" still folds via normalize_native_
        # version_scheme, so the SAME cross-scheme mismatch applies here
        # too -- a prior round's guard used a bare-digits-only regex and
        # missed this exact shape, still permitting a real fixture write.
        with self.assertRaises(ValueError):
            compare_semver("v2.1.0.2", "2.1.0")
        with self.assertRaises(ValueError):
            compare_semver("2.1.0", "v2.1.0.2")

    def test_build_suffixed_four_component_against_plain_three_component_is_refused(self):
        with self.assertRaises(ValueError):
            compare_semver("2.1.0.2+build", "2.1.0")
        with self.assertRaises(ValueError):
            compare_semver("2.1.0", "2.1.0.2+build")

    def test_v_prefixed_and_build_suffixed_four_component_compare_normally_against_each_other(self):
        self.assertGreater(compare_semver("v2.1.0.3+build", "2.1.0.2"), 0)


class TestSemverDeltaCategory(unittest.TestCase):
    def test_major_change_is_breaking(self):
        self.assertEqual(semver_compare.semver_delta_category("1.2.3", "2.0.0"), "breaking")

    def test_minor_change_is_minor(self):
        self.assertEqual(semver_compare.semver_delta_category("1.2.3", "1.3.0"), "minor")

    def test_patch_change_is_patch(self):
        self.assertEqual(semver_compare.semver_delta_category("1.2.3", "1.2.4"), "patch")

    def test_only_prerelease_changed_returns_none(self):
        self.assertIsNone(semver_compare.semver_delta_category("2.0.0-beta.1", "2.0.0"))

    def test_unparsable_either_side_returns_none(self):
        self.assertIsNone(semver_compare.semver_delta_category("not-semver", "1.2.3"))
        self.assertIsNone(semver_compare.semver_delta_category("1.2.3", "not-semver"))

    def test_real_fix_only_changelog_pins_still_classify_from_version_numbers(self):
        # The exact kind of real Modrinth changelog that never matches
        # derive_category_from_changelog's heading-only regex -- the
        # version numbers themselves still say "patch".
        self.assertEqual(semver_compare.semver_delta_category("1.4.2", "1.4.3"), "patch")

    def test_game_version_prefixed_scheme_without_game_version_hint_returns_none(self):
        # "1.21.1-4.0.1" parses as core (1,21,1) + prerelease "4.0.1" --
        # the core never changes between releases on this scheme, so
        # without the game_version hint this is the documented gap
        # (MINECRAFT-362 review, seventh round).
        self.assertIsNone(semver_compare.semver_delta_category("1.21.1-4.0.1", "1.21.1-5.0.0"))

    def test_game_version_prefix_stripped_classifies_correctly(self):
        # The four real pins named in the review, each a major-version
        # bump of the mod's own version once the "1.21.1-" prefix is
        # stripped.
        cases = [
            ("1.21.1-4.0.1", "1.21.1-5.0.0"),      # Supplementaries
            ("1.21.1-3.7.2", "1.21.1-4.0.0"),       # Moonlight Lib
            ("1.21.1-7.0.9", "1.21.1-8.0.0"),       # Project: Vibrant Journeys
            ("1.21.1-1.3.15", "1.21.1-2.0.0"),      # Create: Diesel Generators
        ]
        for previous, candidate in cases:
            with self.subTest(previous=previous, candidate=candidate):
                self.assertEqual(
                    semver_compare.semver_delta_category(previous, candidate, game_version="1.21.1"),
                    "breaking",
                )

    def test_game_version_prefix_stripped_patch_level(self):
        self.assertEqual(
            semver_compare.semver_delta_category("1.21.1-4.0.1", "1.21.1-4.0.2", game_version="1.21.1"),
            "patch",
        )

    def test_mismatched_game_version_prefix_is_not_stripped(self):
        # Only a prefix matching the SUPPLIED game_version is stripped --
        # a version pinned under a different game_version string is left
        # alone rather than guessed at.
        self.assertIsNone(
            semver_compare.semver_delta_category("1.20.1-4.0.1", "1.20.1-5.0.0", game_version="1.21.1")
        )

    def test_game_version_then_loader_prefix_stripped(self):
        # The whole YUNG's family: "<game_version>-<loader>-<mod_version>".
        self.assertEqual(
            semver_compare.semver_delta_category(
                "1.21.1-NeoForge-5.1.9", "1.21.1-NeoForge-6.0.0",
                game_version="1.21.1", loader="neoforge",
            ),
            "breaking",
        )

    def test_loader_then_game_version_prefix_stripped(self):
        # Cristel Lib: "<loader>-<game_version>-<mod_version>".
        self.assertEqual(
            semver_compare.semver_delta_category(
                "neoforge-1.21.1-3.1.7", "neoforge-1.21.1-3.1.8",
                game_version="1.21.1", loader="neoforge",
            ),
            "patch",
        )

    def test_real_yungs_family_pins_classify_correctly(self):
        # The 12 real pins named in the review: yungs-api,
        # yungs-better-{caves,desert-temples,dungeons,end-island,
        # jungle-temples,mineshafts,nether-fortresses,ocean-monuments,
        # witch-huts}, yungs-cave-biomes, yungs-extras -- all share
        # YungsApi's exact versioning scheme and version "5.1.9".
        self.assertEqual(
            semver_compare.semver_delta_category(
                "1.21.1-NeoForge-5.1.9", "1.21.1-NeoForge-6.0.0",
                game_version="1.21.1", loader="neoforge",
            ),
            "breaking",
        )
        self.assertEqual(
            semver_compare.semver_delta_category(
                "1.21.1-NeoForge-5.1.9", "1.21.1-NeoForge-5.2.0",
                game_version="1.21.1", loader="neoforge",
            ),
            "minor",
        )
        self.assertEqual(
            semver_compare.semver_delta_category(
                "1.21.1-NeoForge-5.1.9", "1.21.1-NeoForge-5.1.10",
                game_version="1.21.1", loader="neoforge",
            ),
            "patch",
        )

    def test_strip_known_version_prefixes_is_idempotent_with_only_one_matching(self):
        # loader doesn't match here -- only the game_version prefix
        # should come off, and only once.
        self.assertEqual(
            semver_compare.strip_known_version_prefixes("1.21.1-4.0.1", game_version="1.21.1", loader="fabric"),
            "4.0.1",
        )


class TestMoreSevereCategory(unittest.TestCase):
    def test_breaking_beats_minor_and_patch(self):
        self.assertEqual(semver_compare.more_severe_category("minor", "breaking"), "breaking")
        self.assertEqual(semver_compare.more_severe_category("breaking", "patch"), "breaking")

    def test_minor_beats_patch(self):
        self.assertEqual(semver_compare.more_severe_category("patch", "minor"), "minor")

    def test_none_and_unknown_are_ignored(self):
        self.assertEqual(semver_compare.more_severe_category(None, "patch"), "patch")
        self.assertEqual(semver_compare.more_severe_category("bogus", "minor"), "minor")

    def test_all_none_or_unknown_returns_none(self):
        self.assertIsNone(semver_compare.more_severe_category(None, None))
        self.assertIsNone(semver_compare.more_severe_category(None, "bogus"))


if __name__ == "__main__":
    unittest.main()
