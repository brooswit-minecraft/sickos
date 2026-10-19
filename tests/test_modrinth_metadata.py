import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "modrinth_metadata.py"
SPEC = importlib.util.spec_from_file_location("modrinth_metadata", MODULE_PATH)
modrinth_metadata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(modrinth_metadata)

verify_version_file_metadata = modrinth_metadata.verify_version_file_metadata
MetadataVerificationError = modrinth_metadata.MetadataVerificationError


def make_metadata(**overrides):
    metadata = {
        "id": "VID1",
        "project_id": "PROJECT1",
        "version_number": "1.2.3",
        "files": [
            {
                "filename": "mod-1.2.3.jar",
                "hashes": {"sha512": "b" * 128, "sha1": "a" * 40},
                "url": "https://cdn.modrinth.com/data/PROJECT1/versions/VID1/mod-1.2.3.jar",
            }
        ],
    }
    metadata.update(overrides)
    return metadata


EXPECTED_KWARGS = dict(
    expected_version_id="VID1",
    expected_project_id="PROJECT1",
    expected_version_number="1.2.3",
    expected_file_name="mod-1.2.3.jar",
    expected_sha512="b" * 128,
    expected_sha1="a" * 40,
    expected_download_url="https://cdn.modrinth.com/data/PROJECT1/versions/VID1/mod-1.2.3.jar",
)


class TestVerifyVersionFileMetadata(unittest.TestCase):
    def test_matching_metadata_returns_the_file_entry(self):
        entry = verify_version_file_metadata(make_metadata(), **EXPECTED_KWARGS)
        self.assertEqual(entry["filename"], "mod-1.2.3.jar")

    def test_non_dict_metadata_refused(self):
        with self.assertRaisesRegex(MetadataVerificationError, "JSON object"):
            verify_version_file_metadata(["not", "a", "dict"], **EXPECTED_KWARGS)

    def test_version_id_mismatch_refused(self):
        with self.assertRaisesRegex(MetadataVerificationError, "does not match"):
            verify_version_file_metadata(make_metadata(id="OTHER"), **EXPECTED_KWARGS)

    def test_project_id_mismatch_refused(self):
        with self.assertRaisesRegex(MetadataVerificationError, "project_id"):
            verify_version_file_metadata(make_metadata(project_id="OTHER"), **EXPECTED_KWARGS)

    def test_version_number_mismatch_refused(self):
        # A genuinely older release's bytes/hashes presented under a newer
        # expected version string must never pass -- this is the exact
        # regression PR104's review required (version_number must equal
        # the expected version, not just the modrinth_version_id).
        with self.assertRaisesRegex(MetadataVerificationError, "version_number"):
            verify_version_file_metadata(make_metadata(version_number="1.0.0"), **EXPECTED_KWARGS)

    def test_missing_files_list_refused(self):
        with self.assertRaisesRegex(MetadataVerificationError, "no files list"):
            verify_version_file_metadata(make_metadata(files=None), **EXPECTED_KWARGS)

    def test_no_matching_filename_refused(self):
        metadata = make_metadata()
        metadata["files"][0]["filename"] = "other.jar"
        with self.assertRaisesRegex(MetadataVerificationError, "exactly one file"):
            verify_version_file_metadata(metadata, **EXPECTED_KWARGS)

    def test_duplicate_matching_filename_refused(self):
        metadata = make_metadata()
        metadata["files"].append(dict(metadata["files"][0]))
        with self.assertRaisesRegex(MetadataVerificationError, "exactly one file"):
            verify_version_file_metadata(metadata, **EXPECTED_KWARGS)

    def test_sha512_mismatch_refused(self):
        metadata = make_metadata()
        metadata["files"][0]["hashes"]["sha512"] = "e" * 128
        with self.assertRaisesRegex(MetadataVerificationError, "sha512"):
            verify_version_file_metadata(metadata, **EXPECTED_KWARGS)

    def test_sha1_mismatch_refused(self):
        metadata = make_metadata()
        metadata["files"][0]["hashes"]["sha1"] = "e" * 40
        with self.assertRaisesRegex(MetadataVerificationError, "sha1"):
            verify_version_file_metadata(metadata, **EXPECTED_KWARGS)

    def test_download_url_mismatch_refused(self):
        metadata = make_metadata()
        metadata["files"][0]["url"] = "https://evil.example/x.jar"
        with self.assertRaisesRegex(MetadataVerificationError, "url"):
            verify_version_file_metadata(metadata, **EXPECTED_KWARGS)

    def test_hash_comparison_is_case_insensitive(self):
        metadata = make_metadata()
        metadata["files"][0]["hashes"]["sha512"] = ("B" * 128)
        metadata["files"][0]["hashes"]["sha1"] = ("A" * 40)
        verify_version_file_metadata(metadata, **EXPECTED_KWARGS)  # does not raise


class TestVerifyVersionFileMetadataCompatibility(unittest.TestCase):
    """MINECRAFT-362 review, seventeenth round: expected_game_version/
    expected_loader are optional and, when omitted (both None, the
    default), skip this check entirely -- existing callers (and
    TestVerifyVersionFileMetadata above, via EXPECTED_KWARGS) are
    unaffected. When BOTH are given, the metadata's own declared
    game_versions/loaders must include them."""

    def test_omitted_compatibility_args_skip_the_check_entirely(self):
        metadata = make_metadata()  # no game_versions/loaders keys at all
        verify_version_file_metadata(metadata, **EXPECTED_KWARGS)  # does not raise

    def test_matching_game_version_and_loader_passes(self):
        metadata = make_metadata(game_versions=["1.21.1"], loaders=["neoforge"])
        verify_version_file_metadata(
            metadata, **EXPECTED_KWARGS, expected_game_version="1.21.1", expected_loader="neoforge",
        )  # does not raise

    def test_wrong_game_version_refused(self):
        metadata = make_metadata(game_versions=["1.20.1"], loaders=["neoforge"])
        with self.assertRaisesRegex(MetadataVerificationError, "game_versions"):
            verify_version_file_metadata(
                metadata, **EXPECTED_KWARGS, expected_game_version="1.21.1", expected_loader="neoforge",
            )

    def test_wrong_loader_refused(self):
        metadata = make_metadata(game_versions=["1.21.1"], loaders=["fabric"])
        with self.assertRaisesRegex(MetadataVerificationError, "loaders"):
            verify_version_file_metadata(
                metadata, **EXPECTED_KWARGS, expected_game_version="1.21.1", expected_loader="neoforge",
            )

    def test_missing_game_versions_field_refused_when_checked(self):
        metadata = make_metadata(loaders=["neoforge"])  # no game_versions key at all
        with self.assertRaisesRegex(MetadataVerificationError, "game_versions"):
            verify_version_file_metadata(
                metadata, **EXPECTED_KWARGS, expected_game_version="1.21.1", expected_loader="neoforge",
            )

    def test_only_game_version_given_without_loader_skips_the_check(self):
        # Both or neither -- a caller passing only one is treated the
        # same as passing neither, not as "check only this one field."
        metadata = make_metadata(game_versions=["1.20.1"], loaders=["fabric"])
        verify_version_file_metadata(
            metadata, **EXPECTED_KWARGS, expected_game_version="1.21.1",
        )  # does not raise -- expected_loader was not also given


if __name__ == "__main__":
    unittest.main()
