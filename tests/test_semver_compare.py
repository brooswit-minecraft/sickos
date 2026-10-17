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
        self.assertIsNone(parse_semver("v1.0.0"))
        self.assertIsNone(parse_semver(""))


if __name__ == "__main__":
    unittest.main()
