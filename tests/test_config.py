"""Tests for configuration loading."""

import pytest

from src.config import DownloadTarget, load_config


class TestLoadConfig:
    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "test@example.com")
        monkeypatch.setenv("GARMIN_PASSWORD", "secret")
        monkeypatch.setenv("DAYS_BACK", "14")
        monkeypatch.setenv("GARMINTOKENS", "/custom/tokens")
        monkeypatch.setenv("OUTPUT_DIR", "/custom/output")

        config = load_config()

        assert config.email == "test@example.com"
        assert config.password == "secret"
        assert config.days_back == 14
        assert config.tokenstore == "/custom/tokens"
        assert config.output_dir == "/custom/output"

    def test_default_values(self, monkeypatch):
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)
        monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
        monkeypatch.delenv("DAYS_BACK", raising=False)
        monkeypatch.delenv("GARMINTOKENS", raising=False)
        monkeypatch.delenv("OUTPUT_DIR", raising=False)
        monkeypatch.delenv("DOWNLOAD_FORMATS", raising=False)

        config = load_config()

        assert config.email is None
        assert config.password is None
        assert config.days_back == 7
        assert config.tokenstore == "/app/tokens"
        assert config.output_dir == "/app/data"
        assert config.download_targets == [DownloadTarget("FIT", "FIT")]

    def test_reads_download_formats_multi_value_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "gpx, tcx")

        config = load_config()

        assert config.download_targets == [DownloadTarget("GPX", "GPX"), DownloadTarget("TCX", "TCX")]

    def test_invalid_download_formats_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "bogus")

        with pytest.raises(ValueError):
            load_config()

    def test_empty_download_formats_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", " , ")

        with pytest.raises(ValueError, match="must not be empty"):
            load_config()

    def test_reads_docker_secret(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "garmin_email"
        secret_file.write_text("secret@example.com\n")
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)

        # Patch the secret path for testing
        monkeypatch.setattr(
            "src.config._read_secret", lambda name: "secret@example.com" if name == "GARMIN_EMAIL" else None
        )

        config = load_config()
        assert config.email == "secret@example.com"


class TestParseDownloadTargets:
    def test_custom_folder_per_format(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT:user@example.com")

        config = load_config()

        assert config.download_targets == [DownloadTarget("FIT", "user@example.com")]

    def test_same_format_multiple_folders(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT:strava-inbox, FIT:archive, GPX")

        config = load_config()

        assert config.download_targets == [
            DownloadTarget("FIT", "strava-inbox"),
            DownloadTarget("FIT", "archive"),
            DownloadTarget("GPX", "GPX"),
        ]

    def test_folder_name_case_is_preserved(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "fit:MyFolder")

        config = load_config()

        assert config.download_targets == [DownloadTarget("FIT", "MyFolder")]

    def test_whitespace_around_format_and_folder_is_trimmed(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "  gpx : my folder  ")

        config = load_config()

        assert config.download_targets == [DownloadTarget("GPX", "my folder")]

    def test_duplicate_format_and_folder_is_deduplicated(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT:inbox,FIT:inbox")

        config = load_config()

        assert config.download_targets == [DownloadTarget("FIT", "inbox")]

    def test_bare_duplicate_format_is_deduplicated(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "GPX,gpx")

        config = load_config()

        assert config.download_targets == [DownloadTarget("GPX", "GPX")]

    def test_deduplication_keeps_first_occurrence_order(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "GPX:a,FIT:b,GPX:a,TCX:c")

        config = load_config()

        assert config.download_targets == [
            DownloadTarget("GPX", "a"),
            DownloadTarget("FIT", "b"),
            DownloadTarget("TCX", "c"),
        ]

    def test_invalid_format_with_custom_folder_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "bogus:inbox")

        with pytest.raises(ValueError, match="Invalid DOWNLOAD_FORMATS format"):
            load_config()

    def test_empty_folder_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT:")

        with pytest.raises(ValueError, match="must not be empty"):
            load_config()

    @pytest.mark.parametrize("folder", ["nested/inbox", "..", ".", "../escape", "back\\slash", "/absolute"])
    def test_unsafe_folder_names_raise_value_error(self, monkeypatch, folder):
        monkeypatch.setenv("DOWNLOAD_FORMATS", f"FIT:{folder}")

        with pytest.raises(ValueError):
            load_config()
