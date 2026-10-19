import hashlib
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

# Load this engine's own dependencies into sys.modules UNDER THE SAME
# KEYS bump_mod_github.py's plain `import mod_eligibility` / `import
# github_release_identity` statements resolve to, and load them BEFORE
# bump_mod_github itself. Python's import statement checks sys.modules
# first and reuses whatever object is already registered there -- so
# loading them first means bump_mod_github.py's own module-level
# `github_release_identity` name binds to THIS SAME object the test
# below patches. Loading bump_mod_github FIRST (as an earlier version of
# this file did) let its `import github_release_identity` run before
# the test had registered anything, triggering a real import that
# created a SECOND, different module object under the same key; the
# test's later spec_from_file_location load then silently replaced the
# sys.modules entry without updating bump_mod_github's already-bound
# reference. mock.patch.object(github_release_identity, ...) in that
# ordering patched the test's own (orphaned) object while bump_mod_
# github.run_engine kept calling the first, unpatched one -- so several
# "passing" tests were silently making real network calls to the live
# GitHub API instead of being isolated, catching real-world agreement
# rather than genuine test isolation.
def _load_by_path(key, filename):
    """Load scripts/<filename> under sys.modules[key], reusing whatever
    is ALREADY registered there instead of unconditionally overwriting
    it with a fresh module object. Without this guard, running this file
    together with another test module that also spec-loads the same
    script by path (tests/test_github_release_identity.py does, and so
    does any test module reached via scripts/poll-mod-updates.py's own
    plain `import github_release_identity`/`import bump_mod_github`
    under `python3 -m unittest discover`) is a LOAD-ORDER race: whichever
    file's module-level code runs last wins the sys.modules slot, and
    this file's own `bump.github_release_identity` reference was bound
    at IMPORT time to whichever object held that slot back then -- so a
    later test file's mock.patch.object on the object now sitting in
    sys.modules can silently miss the object this file's own import
    already captured. Confirmed live: this exact test file passed every
    time run alone, then failed with a real (unmocked) `integrity_
    failure` only under `python3 -m unittest discover`, once this
    ticket added a second test file loading the same script by path."""
    if key in sys.modules:
        return sys.modules[key]
    path = Path(__file__).parents[1] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


# Load this engine's own dependencies BEFORE bump_mod_github itself, and
# under the SAME KEYS its plain `import mod_eligibility` / `import
# github_release_identity` statements resolve to (see _load_by_path's
# own docstring for the full load-order story, both within this file and
# across test files sharing the same sys.modules keys under
# `discover`).
mod_eligibility = _load_by_path("mod_eligibility", "mod_eligibility.py")
github_release_identity = _load_by_path("github_release_identity", "github_release_identity.py")
bump = _load_by_path("bump_mod_github", "bump_mod_github.py")

GithubPin = mod_eligibility.GithubPin
GithubReleaseCandidate = github_release_identity.GithubReleaseCandidate

STUB_GATES = {"refresh": ["true"], "check": ["true"], "build": ["true"], "dependency_closure": ["true"]}
FAILING_CHECK_GATES = {"refresh": ["true"], "check": ["false"], "build": ["true"], "dependency_closure": ["true"]}

SLUG = "brooswit-minecraft/dynamic-despawn"
OLD_TAG = "v0.3.3"
OLD_SOURCE_SHA = "a" * 40
REGEX = r"^.+(?<!-api|-dev|-dev-preshadow|-sources)\.jar$"
OLD_SHA256 = "a" * 64


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(repo), check=check, capture_output=True, text=True)


def build_fixture_repo():
    repo = Path(tempfile.mkdtemp(prefix="bump-mod-github-fixture-"))
    (repo / "mods").mkdir()
    (repo / "docs").mkdir()

    (repo / "pack.toml").write_text(
        'name = "Sickos"\n'
        'author = "brooswit"\n'
        'version = "0.22.0"\n'
        'pack-format = "packwiz:1.1.0"\n'
        "\n"
        "[index]\n"
        'file = "index.toml"\n'
        'hash-format = "sha256"\n'
        'hash = "deadbeef"\n'
        "\n"
        "[versions]\n"
        'minecraft = "1.21.1"\n'
        'neoforge = "21.1.250"\n',
        encoding="utf-8",
    )
    (repo / "index.toml").write_text('hash-format = "sha256"\n', encoding="utf-8")

    (repo / "mods" / "dynamic-despawn.pw.toml").write_text(
        'name = "Dynamic Despawn"\n'
        'filename = "dynamicdespawn-0.3.3.jar"\n'
        'side = "both"\n'
        "\n"
        "[download]\n"
        f'url = "https://github.com/{SLUG}/releases/download/{OLD_TAG}/dynamicdespawn-0.3.3.jar"\n'
        'hash-format = "sha256"\n'
        f'hash = "{OLD_SHA256}"\n'
        "\n"
        "[update]\n"
        "[update.github]\n"
        f'slug = "{SLUG}"\n'
        f'tag = "{OLD_TAG}"\n'
        f'branch = "{OLD_SOURCE_SHA}"\n'
        f'regex = "{REGEX}"\n',
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("/build/\n", encoding="utf-8")

    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def make_downloader(content):
    def downloader(url, dest_path):
        Path(dest_path).write_bytes(content)
    return downloader


OLD_URL = f"https://github.com/{SLUG}/releases/download/{OLD_TAG}/dynamicdespawn-0.3.3.jar"


def make_pin(**overrides):
    defaults = dict(
        pin_path="mods/dynamic-despawn.pw.toml", name="Dynamic Despawn", side="both",
        slug=SLUG, pinned_tag=OLD_TAG, pinned_source=OLD_SOURCE_SHA, regex=REGEX,
        pinned_url=OLD_URL, pinned_hash=OLD_SHA256, pinned_hash_format="sha256",
    )
    defaults.update(overrides)
    return GithubPin(**defaults)


def make_candidate(*, tag="v0.3.4", version_number="0.3.4", content=b"new despawn jar",
                    prerelease=False, **overrides):
    defaults = dict(
        slug=SLUG, tag=tag, version_number=version_number,
        asset_name=f"dynamicdespawn-{version_number}.jar",
        download_url=f"https://github.com/{SLUG}/releases/download/{tag}/dynamicdespawn-{version_number}.jar",
        sha256=hashlib.sha256(content).hexdigest(), size=len(content), body="",
        prerelease=prerelease,
    )
    defaults.update(overrides)
    return GithubReleaseCandidate(**defaults)


def release_json_for(tag, version_number, *, sha256=OLD_SHA256, filename=None, prerelease=False):
    """A release-by-tag JSON response that `candidate_from_release_json`
    can actually parse (an asset matching REGEX, with a usable sha256
    digest) -- MINECRAFT-362 review, comment 48456, item 2: run_engine
    now builds a real candidate for the currently-pinned tag too (to
    verify identity before any already_pinned/no-op return), not just
    read `tag_name` off a bare dict."""
    filename = filename or f"dynamicdespawn-{version_number}.jar"
    return {
        "tag_name": tag,
        "assets": [{
            "name": filename, "digest": f"sha256:{sha256}",
            "browser_download_url": f"https://github.com/{SLUG}/releases/download/{tag}/{filename}",
            "size": 1,
        }],
        "prerelease": prerelease,
    }


def _build_neoforge_jar(mod_id, version, requires=()):
    """Same minimal, real, valid NeoForge jar builder as
    tests/test_bump_mod.py's own copy -- duplicated rather than
    cross-imported so this file's test discovery does not depend on
    another test module's import side effects."""
    lines = ['modLoader="javafml"', 'loaderVersion="[1,)"', 'license="Test"', "",
             "[[mods]]", f'modId="{mod_id}"', f'version="{version}"', f'displayName="{mod_id}"']
    for dep_mod_id, version_range in requires:
        lines += ["", f"[[dependencies.{mod_id}]]", f'modId="{dep_mod_id}"',
                  'type="required"', f'versionRange="{version_range}"',
                  'ordering="NONE"', 'side="BOTH"']
    toml_text = "\n".join(lines) + "\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/neoforge.mods.toml", toml_text)
    return buf.getvalue()


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = build_fixture_repo()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def porcelain(self):
        return _git(self.repo, "status", "--porcelain").stdout

    def pin_text(self):
        return (self.repo / "mods" / "dynamic-despawn.pw.toml").read_text(encoding="utf-8")

    def run_engine(self, pin, candidate, *, content=b"new despawn jar", gate_commands=None,
                   previous_release_json=None, source_sha=None, resolve_tag_source_sha=None):
        """`source_sha` is what a NEW tag (anything other than `pin.
        pinned_tag`) resolves to; defaults to `pin.pinned_source` itself,
        so neither the already-pinned fast path nor an equal-version
        retag hits a source conflict unless a test explicitly asks for
        one (via `source_sha` or a full override via
        `resolve_tag_source_sha`). `pin.pinned_tag` itself always
        resolves to `pin.pinned_source` by default, matching the on-disk
        record -- MINECRAFT-362 review, comment 48492."""
        previous_release_json = previous_release_json or release_json_for(pin.pinned_tag, "0.3.3")
        if resolve_tag_source_sha is None:
            effective_source_sha = pin.pinned_source if source_sha is None else source_sha

            def resolve_tag_source_sha(slug, tag):
                return pin.pinned_source if tag == pin.pinned_tag else effective_source_sha

        with mock.patch.object(
            github_release_identity, "fetch_release_by_tag_json", return_value=previous_release_json,
        ), mock.patch.object(
            github_release_identity, "resolve_tag_source_sha", side_effect=resolve_tag_source_sha,
        ):
            return bump.run_engine(
                self.repo, pin, candidate,
                downloader=make_downloader(content), run_command=subprocess.run,
                gate_commands=gate_commands or STUB_GATES,
            )


class TestAlreadyPinnedAndNoCandidate(EngineTestCase):
    def test_already_pinned_same_tag_is_a_clean_noop(self):
        pin = make_pin()
        candidate = make_candidate(tag=OLD_TAG, version_number="0.3.3")
        summary, exit_code, _ = self.run_engine(pin, candidate)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertFalse(summary["changed"])
        self.assertEqual(summary["outcome"], "already_pinned")

    def test_no_pinned_tag_is_no_candidate(self):
        pin = make_pin(pinned_tag="")
        candidate = make_candidate()
        summary, exit_code, _ = self.run_engine(pin, candidate)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertFalse(summary["changed"])
        self.assertEqual(summary["outcome"], "no_candidate")


class TestDowngradeRefused(EngineTestCase):
    def test_older_version_is_refused(self):
        pin = make_pin()
        candidate = make_candidate(tag="v0.3.0", version_number="0.3.0")
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(pin, candidate)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_DOWNGRADE_REFUSED)
        self.assertEqual(self.porcelain(), "")

    def test_same_version_different_tag_spelling_is_a_clean_noop(self):
        # Identical bytes under a re-tag -- a genuine harmless replay,
        # verified (not assumed) by sha256 (MINECRAFT-362 review, comment
        # 48456, item 2). The pin's own on-disk hash must match too, or
        # _verify_identity_matches_pin's same-tag check fails first.
        identical_sha256 = hashlib.sha256(b"identical bytes").hexdigest()
        pin = make_pin(pinned_hash=identical_sha256)
        candidate = make_candidate(
            tag="0.3.3", version_number="0.3.3", content=b"identical bytes", sha256=identical_sha256,
        )
        summary, exit_code, _ = self.run_engine(
            pin, candidate,
            previous_release_json=release_json_for(OLD_TAG, "0.3.3", sha256=identical_sha256),
        )
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertFalse(summary["changed"])
        self.assertEqual(self.porcelain(), "")

    def test_same_version_different_tag_different_bytes_is_rejected(self):
        """MINECRAFT-362 review, comment 48456, item 2: an equal-version
        retag with DIFFERENT bytes is a conflict, not a harmless
        replay -- must not be silently accepted as a no-op."""
        pin = make_pin()
        candidate = make_candidate(
            tag="0.3.3", version_number="0.3.3", content=b"different bytes",
        )
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(
                pin, candidate, content=b"different bytes",
                previous_release_json=release_json_for(OLD_TAG, "0.3.3"),  # still OLD_SHA256
            )
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(ctx.exception.outcome, "integrity_conflict")
        self.assertEqual(self.porcelain(), "")


class TestIntegrityFailures(EngineTestCase):
    def test_already_pinned_with_changed_digest_is_rejected_not_a_replay(self):
        """MINECRAFT-362 review, comment 48456, item 2: candidate.tag ==
        pin.pinned_tag used to return a bare, unverified 'already_pinned'
        -- a replaced asset under an unchanged tag must now be rejected,
        not silently accepted as a harmless no-op."""
        pin = make_pin()
        candidate = make_candidate(tag=OLD_TAG, version_number="0.3.3")
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(
                pin, candidate,
                previous_release_json=release_json_for(OLD_TAG, "0.3.3", sha256="f" * 64),
            )
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(ctx.exception.outcome, "integrity_conflict")
        self.assertEqual(self.porcelain(), "")

    def test_already_pinned_with_changed_download_url_is_rejected(self):
        pin = make_pin()
        candidate = make_candidate(tag=OLD_TAG, version_number="0.3.3")
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(
                pin, candidate,
                previous_release_json=release_json_for(
                    OLD_TAG, "0.3.3", filename="dynamicdespawn-moved.jar",
                ),
            )
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(ctx.exception.outcome, "integrity_conflict")
        self.assertEqual(self.porcelain(), "")

    def test_already_pinned_with_moved_tag_same_artifact_is_rejected(self):
        """MINECRAFT-362 review, comment 48492: a tag silently re-pointed
        to a DIFFERENT source commit, with its URL and digest unchanged,
        must be rejected -- resolve_tag_source_sha was never even called
        on either no-op path before this fix, so this previously passed
        as already_pinned/exit 0."""
        pin = make_pin()
        candidate = make_candidate(tag=OLD_TAG, version_number="0.3.3")
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(
                pin, candidate, resolve_tag_source_sha=lambda slug, tag: "moved" + "0" * 36,
            )
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(ctx.exception.outcome, "integrity_conflict")
        self.assertEqual(self.porcelain(), "")

    def test_same_version_different_tag_same_digest_different_source_is_rejected(self):
        """MINECRAFT-362 review, comment 48492: an equal-version retag
        with identical bytes but a DIFFERENT source commit must be
        rejected, not accepted as a replay on digest alone."""
        pin = make_pin()

        def resolver(slug, tag):
            if tag == OLD_TAG:
                return pin.pinned_source
            return "different-source" + "1" * 23  # 40 chars total

        candidate = make_candidate(tag="0.3.3", version_number="0.3.3")
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(
                pin, candidate,
                previous_release_json=release_json_for(OLD_TAG, "0.3.3"),
                resolve_tag_source_sha=resolver,
            )
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(ctx.exception.outcome, "integrity_conflict")
        self.assertEqual(self.porcelain(), "")

    def test_sha512_pinned_mod_with_replaced_bytes_is_rejected(self):
        """MINECRAFT-362 review, comment 48492: the real dynamic-despawn
        pin's own on-disk hash-format is sha512 (predates this engine).
        _verify_identity_matches_pin must actually download and re-hash
        the asset in that format, not skip content verification just
        because it isn't sha256 -- a replaced asset at the same URL,
        with a DIFFERENT sha512 but a matching GitHub sha256 digest for
        ITS OWN (different) bytes, must still be rejected."""
        real_bytes = b"the genuinely pinned bytes"
        replaced_bytes = b"a replacement with a different sha512"
        pin = make_pin(
            pinned_hash_format="sha512", pinned_hash=hashlib.sha512(real_bytes).hexdigest(),
        )
        candidate = make_candidate(tag=OLD_TAG, version_number="0.3.3")
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(
                pin, candidate, content=replaced_bytes,
                previous_release_json=release_json_for(
                    OLD_TAG, "0.3.3", sha256=hashlib.sha256(replaced_bytes).hexdigest(),
                ),
            )
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(ctx.exception.outcome, "integrity_conflict")
        self.assertEqual(self.porcelain(), "")

    def test_sha512_pinned_mod_with_matching_bytes_is_a_clean_noop(self):
        """Positive control for the above: identical bytes under a
        sha512-format pin must still verify as a clean no-op, not fail
        just because the format isn't sha256."""
        real_bytes = b"the genuinely pinned bytes"
        pin = make_pin(
            pinned_hash_format="sha512", pinned_hash=hashlib.sha512(real_bytes).hexdigest(),
        )
        candidate = make_candidate(tag=OLD_TAG, version_number="0.3.3")
        summary, exit_code, _ = self.run_engine(
            pin, candidate, content=real_bytes,
            previous_release_json=release_json_for(
                OLD_TAG, "0.3.3", sha256=hashlib.sha256(real_bytes).hexdigest(),
            ),
        )
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertFalse(summary["changed"])
        self.assertEqual(self.porcelain(), "")

    def test_sha256_mismatch_fails_closed(self):
        content = b"real bytes"
        wrong_sha256 = hashlib.sha256(b"different bytes").hexdigest()
        pin = make_pin()
        candidate = make_candidate(content=content, sha256=wrong_sha256)
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(pin, candidate, content=content)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(self.porcelain(), "")

    def test_previous_tag_lookup_failure_fails_closed(self):
        pin = make_pin()
        candidate = make_candidate()
        with mock.patch.object(
            github_release_identity, "fetch_release_by_tag_json",
            side_effect=github_release_identity.GithubIdentityError("integrity_failure", "boom"),
        ):
            with self.assertRaises(bump.EngineError) as ctx:
                bump.run_engine(
                    self.repo, pin, candidate,
                    downloader=make_downloader(b"new despawn jar"), run_command=subprocess.run,
                    gate_commands=STUB_GATES,
                )
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(self.porcelain(), "")

    def test_resolve_tag_source_sha_failure_fails_closed_before_any_write(self):
        """MINECRAFT-362 review, comments 48415/48456: resolving the new
        release's TAG to an immutable commit must happen BEFORE anything
        is written -- a failure here is exactly as fatal as a hash
        mismatch, not a best-effort annotation."""
        pin = make_pin()
        candidate = make_candidate()
        with mock.patch.object(
            github_release_identity, "fetch_release_by_tag_json",
            return_value=release_json_for(OLD_TAG, "0.3.3"),
        ), mock.patch.object(
            github_release_identity, "resolve_tag_source_sha",
            side_effect=github_release_identity.GithubIdentityError("integrity_failure", "could not resolve"),
        ):
            with self.assertRaises(bump.EngineError) as ctx:
                bump.run_engine(
                    self.repo, pin, candidate,
                    downloader=make_downloader(b"new despawn jar"), run_command=subprocess.run,
                    gate_commands=STUB_GATES,
                )
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_INTEGRITY_FAILURE)
        self.assertEqual(self.porcelain(), "")


class TestGateFailureRestoresRepo(EngineTestCase):
    def test_failing_check_gate_restores_repo(self):
        pin = make_pin()
        candidate = make_candidate()
        before = self.porcelain()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(pin, candidate, gate_commands=FAILING_CHECK_GATES)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_GATE_FAILURE)
        self.assertEqual(self.porcelain(), before)
        self.assertFalse((self.repo / "docs" / "releases").exists() and
                          any((self.repo / "docs" / "releases").iterdir()))


class TestSuccessfulBump(EngineTestCase):
    def test_patch_bump_writes_pin_pack_version_and_notes(self):
        pin = make_pin()
        content = b"new despawn jar bytes"
        candidate = make_candidate(tag="v0.3.4", version_number="0.3.4", content=content)
        summary, exit_code, message = self.run_engine(pin, candidate, content=content, source_sha="c" * 40)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertTrue(summary["changed"])
        self.assertEqual(summary["outcome"], "bumped")
        self.assertEqual(summary["mapping_rule"], "patch")
        self.assertEqual(summary["new_pack_version"], "0.22.1")
        self.assertIn("Bumped Dynamic Despawn to 0.3.4", message)

        pin_text = self.pin_text()
        self.assertIn('tag = "v0.3.4"', pin_text)
        self.assertIn(f'hash = "{hashlib.sha256(content).hexdigest()}"', pin_text)
        self.assertIn('hash-format = "sha256"', pin_text)
        # The new, resolved source SHA is written to `branch` -- NOT the
        # old pin's own pinned_source, and never read back for discovery
        # (MINECRAFT-362 review, comment 48415).
        self.assertIn(f'branch = "{"c" * 40}"', pin_text)
        self.assertNotIn(OLD_SOURCE_SHA, pin_text)
        self.assertIn(f'slug = "{SLUG}"', pin_text)
        self.assertIn(f'regex = "{REGEX}"', pin_text.replace("\\\\", "\\"))

        notes_text = (self.repo / "docs" / "releases" / "0.22.1.md").read_text(encoding="utf-8")
        self.assertIn("Dynamic Despawn: 0.3.4", notes_text)

    def test_minor_bump_category_from_semver_delta(self):
        pin = make_pin()
        content = b"minor bump jar"
        candidate = make_candidate(tag="v0.4.0", version_number="0.4.0", content=content)
        summary, exit_code, _ = self.run_engine(pin, candidate, content=content)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertEqual(summary["mapping_rule"], "minor")

    def test_breaking_bump_requires_human_review(self):
        pin = make_pin()
        candidate = make_candidate(tag="v1.0.0", version_number="1.0.0")
        before = self.porcelain()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(pin, candidate)
        self.assertEqual(ctx.exception.outcome, "breaking_change_requires_human_review")
        self.assertEqual(self.porcelain(), before)

    def test_client_only_side_is_preserved_not_flipped_to_both(self):
        """MINECRAFT-362 review, comment 48415's explicit test ask:
        a client-only pin (e.g. the real isometric-camera pin) must stay
        client-only through a bump, same as bump_mod.py's own CLI
        --side guard for the Modrinth path."""
        pin = make_pin(side="client")
        content = b"client only jar"
        candidate = make_candidate(tag="v0.3.4", version_number="0.3.4", content=content)
        self.run_engine(pin, candidate, content=content)
        self.assertIn('side = "client"', self.pin_text())


class TestRuntimeCompatibilityCheck(EngineTestCase):
    """Same check as tests/test_bump_mod.py's own class of this name,
    exercised through this engine -- check_runtime_compatibility_or_die
    is reused unchanged, not reimplemented, so a GitHub-sourced
    candidate's own jar gets the exact same platform/runtime gate a
    Modrinth-sourced one does."""

    def test_candidate_jar_requiring_a_newer_neoforge_fails_closed(self):
        jar_bytes = _build_neoforge_jar("dynamicdespawn", "0.3.4", requires=[("neoforge", "[21.2,)")])
        pin = make_pin()
        candidate = make_candidate(
            tag="v0.3.4", version_number="0.3.4", content=jar_bytes,
            sha256=hashlib.sha256(jar_bytes).hexdigest(),
        )
        before = self.porcelain()
        with self.assertRaises(bump.EngineError) as ctx:
            self.run_engine(pin, candidate, content=jar_bytes)
        self.assertEqual(ctx.exception.exit_code, bump.EXIT_CONFIG_ERROR)
        self.assertEqual(ctx.exception.outcome, "runtime_incompatible")
        self.assertEqual(self.porcelain(), before)

    def test_candidate_jar_requiring_an_already_satisfied_neoforge_still_bumps(self):
        jar_bytes = _build_neoforge_jar("dynamicdespawn", "0.3.4", requires=[("neoforge", "[21.1,)")])
        pin = make_pin()
        candidate = make_candidate(
            tag="v0.3.4", version_number="0.3.4", content=jar_bytes,
            sha256=hashlib.sha256(jar_bytes).hexdigest(),
        )
        summary, exit_code, _ = self.run_engine(pin, candidate, content=jar_bytes)
        self.assertEqual(exit_code, bump.EXIT_OK)
        self.assertTrue(summary["changed"])


if __name__ == "__main__":
    unittest.main()
