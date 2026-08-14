"""Tests for configuration loading."""

import pytest

from src.config import DownloadTarget, load_config


class TestLoadConfig:
    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", "garmin,wahoo")
        monkeypatch.setenv("DAYS_BACK", "14")
        monkeypatch.setenv("TOKENS_DIR", "/custom/tokens")
        monkeypatch.setenv("OUTPUT_DIR", "/custom/output")

        config = load_config()

        assert config.trackers == ["garmin", "wahoo"]
        assert config.days_back == 14
        assert config.tokens_dir == "/custom/tokens"
        assert config.output_dir == "/custom/output"

    def test_default_values(self, monkeypatch):
        monkeypatch.delenv("TRACKERS", raising=False)
        monkeypatch.delenv("DAYS_BACK", raising=False)
        monkeypatch.delenv("TOKENS_DIR", raising=False)
        monkeypatch.delenv("OUTPUT_DIR", raising=False)
        monkeypatch.delenv("DOWNLOAD_FORMATS", raising=False)

        config = load_config()

        assert config.trackers == ["garmin"]
        assert config.days_back == 7
        assert config.tokens_dir == "/app/tokens"
        assert config.output_dir == "/app/data"
        assert config.download_targets == [DownloadTarget("FIT")]

    def test_credentials_are_not_config(self, monkeypatch):
        """Each tracker reads its own in from_env, so adding one changes no shared type."""
        config = load_config()

        assert not hasattr(config, "email")
        assert not hasattr(config, "password")

    def test_reads_download_formats_multi_value_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "gpx, tcx")

        config = load_config()

        assert config.download_targets == [DownloadTarget("GPX"), DownloadTarget("TCX")]

    def test_invalid_download_formats_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "bogus")

        with pytest.raises(ValueError):
            load_config()

    def test_empty_download_formats_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", " , ")

        with pytest.raises(ValueError, match="must not be empty"):
            load_config()


class TestParseTrackers:
    def test_defaults_to_garmin(self, monkeypatch):
        monkeypatch.delenv("TRACKERS", raising=False)

        assert load_config().trackers == ["garmin"]

    def test_reads_multiple_trackers_case_insensitively(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", " Garmin , WAHOO ")

        assert load_config().trackers == ["garmin", "wahoo"]

    def test_preserves_the_configured_order(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", "wahoo,garmin")

        assert load_config().trackers == ["wahoo", "garmin"]

    def test_duplicates_are_removed_keeping_first_occurrence(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", "wahoo,garmin,WAHOO")

        assert load_config().trackers == ["wahoo", "garmin"]

    def test_unknown_tracker_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", "garmin,polar")

        with pytest.raises(ValueError, match="Invalid TRACKERS entry 'polar'"):
            load_config()

    def test_empty_trackers_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", " , ")

        with pytest.raises(ValueError, match="TRACKERS must not be empty"):
            load_config()


class TestDownloadTargetPath:
    """Every target lives under its format's folder, so formats never mix in one folder."""

    def test_path_is_the_format_when_no_subfolder(self):
        assert DownloadTarget("FIT").path == "FIT"

    def test_path_nests_the_subfolder_under_the_format(self):
        assert DownloadTarget("FIT", "folderA").path == "FIT/folderA"

    def test_same_subfolder_name_under_different_formats_gives_distinct_paths(self):
        assert DownloadTarget("GPX", "shared").path == "GPX/shared"
        assert DownloadTarget("TCX", "shared").path == "TCX/shared"


class TestParseDownloadTargets:
    def test_custom_subfolder_per_format(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT:user@example.com")

        config = load_config()

        assert config.download_targets == [DownloadTarget("FIT", "user@example.com")]
        assert config.download_targets[0].path == "FIT/user@example.com"

    def test_bare_and_subfoldered_same_format_coexist(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT, FIT:folderA")

        config = load_config()

        assert config.download_targets == [DownloadTarget("FIT"), DownloadTarget("FIT", "folderA")]
        assert [t.path for t in config.download_targets] == ["FIT", "FIT/folderA"]

    def test_same_format_multiple_subfolders(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT:strava-inbox, FIT:archive, GPX")

        config = load_config()

        assert config.download_targets == [
            DownloadTarget("FIT", "strava-inbox"),
            DownloadTarget("FIT", "archive"),
            DownloadTarget("GPX"),
        ]

    def test_subfolder_name_case_is_preserved(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "fit:MyFolder")

        config = load_config()

        assert config.download_targets == [DownloadTarget("FIT", "MyFolder")]

    def test_whitespace_around_format_and_subfolder_is_trimmed(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "  gpx : my folder  ")

        config = load_config()

        assert config.download_targets == [DownloadTarget("GPX", "my folder")]

    def test_duplicate_format_and_subfolder_is_deduplicated(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT:inbox,FIT:inbox")

        config = load_config()

        assert config.download_targets == [DownloadTarget("FIT", "inbox")]

    def test_bare_duplicate_format_is_deduplicated(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "GPX,gpx")

        config = load_config()

        assert config.download_targets == [DownloadTarget("GPX")]

    def test_same_subfolder_under_two_formats_is_not_deduplicated(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "GPX:inbox,TCX:inbox")

        config = load_config()

        assert config.download_targets == [DownloadTarget("GPX", "inbox"), DownloadTarget("TCX", "inbox")]
        assert [t.path for t in config.download_targets] == ["GPX/inbox", "TCX/inbox"]

    def test_deduplication_keeps_first_occurrence_order(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "GPX:a,FIT:b,GPX:a,TCX:c")

        config = load_config()

        assert config.download_targets == [
            DownloadTarget("GPX", "a"),
            DownloadTarget("FIT", "b"),
            DownloadTarget("TCX", "c"),
        ]

    def test_invalid_format_with_custom_subfolder_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "bogus:inbox")

        with pytest.raises(ValueError, match="Invalid DOWNLOAD_FORMATS format"):
            load_config()

    def test_empty_subfolder_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT:")

        with pytest.raises(ValueError, match="must not be empty"):
            load_config()

    @pytest.mark.parametrize("folder", ["nested/inbox", "..", ".", "../escape", "back\\slash", "/absolute"])
    def test_unsafe_subfolder_names_raise_value_error(self, monkeypatch, folder):
        monkeypatch.setenv("DOWNLOAD_FORMATS", f"FIT:{folder}")

        with pytest.raises(ValueError):
            load_config()
