import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

def _load_by_path(key, filename):
    """Reuse whatever is already registered in sys.modules[key] rather
    than unconditionally loading a fresh module object -- see
    tests/test_bump_mod_github.py's own _load_by_path docstring for why
    this matters once more than one test file (or a production script
    reached via one, e.g. scripts/poll-mod-updates.py's plain `import
    github_release_identity`) can load the same script by path under
    `python3 -m unittest discover`."""
    if key in sys.modules:
        return sys.modules[key]
    path = Path(__file__).parents[1] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


m = _load_by_path("github_release_identity", "github_release_identity.py")

SLUG = "brooswit-minecraft/dynamic-despawn"
REGEX = r"^.+(?<!-api|-dev|-dev-preshadow|-sources)\.jar$"
PLAIN_JAR_REGEX = r"^.+\.jar$"


def _asset(name, *, digest="sha256:" + "a" * 64, size=123):
    return {"name": name, "digest": digest, "browser_download_url": f"https://example.invalid/{name}", "size": size}


def _release(tag, *, assets=(), created_at="2026-01-01T00:00:00Z", draft=False, prerelease=False,
             target_commitish="main", body=""):
    return {
        "tag_name": tag, "assets": list(assets), "created_at": created_at,
        "draft": draft, "prerelease": prerelease, "target_commitish": target_commitish, "body": body,
    }


class TestMatchAsset(unittest.TestCase):
    def test_lookbehind_pattern_excludes_named_suffixes(self):
        names = ["foo.jar", "foo-api.jar", "foo-dev.jar", "foo-dev-preshadow.jar", "foo-sources.jar", "foo.txt"]
        self.assertEqual(m.match_asset(REGEX, names), ["foo.jar"])

    def test_plain_jar_pattern_matches_every_jar(self):
        names = ["foo.jar", "foo-api.jar", "foo.txt"]
        self.assertEqual(sorted(m.match_asset(PLAIN_JAR_REGEX, names)), ["foo-api.jar", "foo.jar"])

    def test_ambiguous_multiple_matches_is_the_callers_problem_not_this_functions(self):
        # match_asset itself just reports every match; candidate_from_release_json
        # is what fails closed on "not exactly one" -- covered below.
        names = ["foo.jar", "bar.jar"]
        self.assertEqual(sorted(m.match_asset(PLAIN_JAR_REGEX, names)), ["bar.jar", "foo.jar"])

    def test_unsupported_regex_shape_fails_closed(self):
        with self.assertRaises(m.GithubIdentityError) as ctx:
            m.match_asset(r"^some\.exotic\.pattern$", ["foo.jar"])
        self.assertEqual(ctx.exception.outcome, "no_parseable_candidate")

    def test_empty_regex_fails_closed_rather_than_matching_everything(self):
        with self.assertRaises(m.GithubIdentityError):
            m.match_asset("", ["foo.jar"])


class TestCandidateFromReleaseJson(unittest.TestCase):
    def test_builds_candidate_from_single_matching_asset(self):
        release = _release("v0.3.4", assets=[_asset("dynamicdespawn-0.3.4.jar")], target_commitish="b" * 40)
        candidate = m.candidate_from_release_json(SLUG, release, REGEX)
        self.assertEqual(candidate.tag, "v0.3.4")
        self.assertEqual(candidate.version_number, "0.3.4")
        self.assertEqual(candidate.asset_name, "dynamicdespawn-0.3.4.jar")
        self.assertEqual(candidate.sha256, "a" * 64)
        self.assertEqual(candidate.source_commitish, "b" * 40)

    def test_ambiguous_asset_fails_closed(self):
        release = _release("v0.3.4", assets=[_asset("one.jar"), _asset("two.jar")])
        with self.assertRaises(m.GithubIdentityError) as ctx:
            m.candidate_from_release_json(SLUG, release, PLAIN_JAR_REGEX)
        self.assertEqual(ctx.exception.outcome, "no_parseable_candidate")

    def test_zero_matching_assets_fails_closed(self):
        release = _release("v0.3.4", assets=[_asset("readme.txt", digest="")])
        with self.assertRaises(m.GithubIdentityError):
            m.candidate_from_release_json(SLUG, release, PLAIN_JAR_REGEX)

    def test_non_sha256_digest_fails_closed(self):
        release = _release("v0.3.4", assets=[_asset("one.jar", digest="md5:deadbeef")])
        with self.assertRaises(m.GithubIdentityError) as ctx:
            m.candidate_from_release_json(SLUG, release, PLAIN_JAR_REGEX)
        self.assertEqual(ctx.exception.outcome, "no_parseable_candidate")


class TestResolveLatestMatchingRelease(unittest.TestCase):
    def test_picks_newest_by_created_at_even_when_api_returns_reversed_order(self):
        """GitHub's docs say the list endpoint returns newest-first, but
        this module explicitly re-sorts rather than trusting that
        (MINECRAFT-362 review, comment 48415's 'reversed sources/API
        asset ordering' test ask) -- verified here by feeding it the
        OPPOSITE of what the real ordering would be."""
        older = _release("v0.3.3", assets=[_asset("dynamicdespawn-0.3.3.jar")], created_at="2026-01-01T00:00:00Z")
        newer = _release("v0.3.4", assets=[_asset("dynamicdespawn-0.3.4.jar")], created_at="2026-02-01T00:00:00Z")
        with mock.patch.object(m, "fetch_releases_json", return_value=[older, newer]):
            candidate = m.resolve_latest_matching_release(SLUG, PLAIN_JAR_REGEX)
        self.assertEqual(candidate.tag, "v0.3.4")

    def test_excludes_drafts_and_prereleases(self):
        draft = _release("v0.4.0", assets=[_asset("x-0.4.0.jar")], created_at="2026-03-01T00:00:00Z", draft=True)
        prerelease = _release(
            "v0.3.9", assets=[_asset("x-0.3.9.jar")], created_at="2026-02-15T00:00:00Z", prerelease=True,
        )
        real = _release("v0.3.4", assets=[_asset("dynamicdespawn-0.3.4.jar")], created_at="2026-01-01T00:00:00Z")
        with mock.patch.object(m, "fetch_releases_json", return_value=[draft, prerelease, real]):
            candidate = m.resolve_latest_matching_release(SLUG, PLAIN_JAR_REGEX)
        self.assertEqual(candidate.tag, "v0.3.4")

    def test_no_qualifying_release_is_no_candidate(self):
        with mock.patch.object(m, "fetch_releases_json", return_value=[]):
            with self.assertRaises(m.GithubIdentityError) as ctx:
                m.resolve_latest_matching_release(SLUG, PLAIN_JAR_REGEX)
        self.assertEqual(ctx.exception.outcome, "no_candidate")

    def test_never_filters_by_branch_the_permanent_freeze_bug(self):
        """The whole point of this function (MINECRAFT-362 review,
        comment 48415): packwiz's own ghUpdater.CheckUpdate filters
        releases by an exact target_commitish match against the pin's
        frozen `branch`, which can never match a NEW release's own
        target_commitish again. resolve_latest_matching_release takes no
        `branch`/`pinned_source` argument at all -- this test simply
        confirms a release whose target_commitish is completely
        unrelated to anything still resolves as the latest candidate."""
        release = _release(
            "v0.3.4", assets=[_asset("dynamicdespawn-0.3.4.jar")], target_commitish="totally-unrelated-ref",
        )
        with mock.patch.object(m, "fetch_releases_json", return_value=[release]):
            candidate = m.resolve_latest_matching_release(SLUG, PLAIN_JAR_REGEX)
        self.assertEqual(candidate.tag, "v0.3.4")


class TestResolveSourceSha(unittest.TestCase):
    def test_already_a_sha_is_returned_as_is_no_extra_call(self):
        sha = "a" * 40
        with mock.patch.object(m, "_api_get") as mocked:
            result = m.resolve_source_sha(SLUG, sha)
        self.assertEqual(result, sha)
        mocked.assert_not_called()

    def test_branch_name_is_resolved_via_commits_api(self):
        """The real magicavoxel-loader pin's target_commitish is the
        branch name 'MINECRAFT-284', not a SHA -- confirmed live,
        read anonymously (MINECRAFT-362 review, comment 48415's
        'resolve tag to immutable source')."""
        with mock.patch.object(m, "_api_get", return_value={"sha": "c" * 40}) as mocked:
            result = m.resolve_source_sha(SLUG, "MINECRAFT-284")
        self.assertEqual(result, "c" * 40)
        (path,), kwargs = mocked.call_args
        self.assertIn("/commits/MINECRAFT-284", path)

    def test_commits_api_failure_fails_closed(self):
        with mock.patch.object(m, "_api_get", side_effect=OSError("boom")):
            with self.assertRaises(Exception):
                m.resolve_source_sha(SLUG, "some-branch")

    def test_commits_api_returning_no_sha_fails_closed(self):
        with mock.patch.object(m, "_api_get", return_value={}):
            with self.assertRaises(m.GithubIdentityError) as ctx:
                m.resolve_source_sha(SLUG, "some-branch")
        self.assertEqual(ctx.exception.outcome, "integrity_failure")


class TestStaleReplayedEvents(unittest.TestCase):
    """'Stale/replayed events' (MINECRAFT-362 review, comment 48415's
    test ask): resolving the SAME release twice (e.g. a replayed
    webhook, or two overlapping poll runs) must produce the identical
    candidate, not drift -- resolve_latest_matching_release is a pure
    read of whatever fetch_releases_json returns, with no hidden state
    of its own, so this is really a test that re-running it is a no-op
    at this layer (the no-downgrade/already_pinned decision that makes a
    REPLAYED bump itself a safe no-op lives in bump_mod_github.run_engine
    and is covered by test_bump_mod_github.py's
    TestAlreadyPinnedAndNoCandidate/TestDowngradeRefused classes)."""

    def test_resolving_the_same_release_twice_is_idempotent(self):
        release = _release("v0.3.4", assets=[_asset("dynamicdespawn-0.3.4.jar")])
        with mock.patch.object(m, "fetch_releases_json", return_value=[release]):
            first = m.resolve_latest_matching_release(SLUG, PLAIN_JAR_REGEX)
            second = m.resolve_latest_matching_release(SLUG, PLAIN_JAR_REGEX)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
