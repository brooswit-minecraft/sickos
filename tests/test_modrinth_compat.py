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
CompatResolutionError = modrinth_compat.CompatResolutionError
EligiblePin = mod_eligibility.EligiblePin


def version_entry(version_id, version_number, *, game_versions=("1.21.1",), loaders=("neoforge",),
                   filename="mod.jar", sha1="a" * 40, sha512="b" * 128, url="https://cdn.modrinth.com/x.jar"):
    return {
        "id": version_id,
        "version_number": version_number,
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


if __name__ == "__main__":
    unittest.main()
