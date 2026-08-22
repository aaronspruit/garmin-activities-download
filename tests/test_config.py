"""Tests for configuration loading."""

import pytest

from src.config import DownloadTarget, _assert_within, load_config


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
        monkeypatch.delenv("DOWNLOAD_TARGETS", raising=False)

        config = load_config()

        assert config.trackers == ["garmin"]
        assert config.days_back == 7
        assert config.tokens_dir == "/app/tokens"
        assert config.output_dir == "/app/data"
        assert config.download_targets == {"garmin": [DownloadTarget("FIT")]}

    def test_targets_are_resolved_for_each_enabled_tracker(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", "garmin,wahoo")

        assert set(load_config().download_targets) == {"garmin", "wahoo"}

    def test_credentials_are_not_config(self, monkeypatch):
        """Each tracker reads its own in from_env, so adding one changes no shared type."""
        config = load_config()

        assert not hasattr(config, "email")
        assert not hasattr(config, "password")


class TestStateDir:
    def test_defaults_to_a_hidden_folder_below_the_output_dir(self, monkeypatch):
        monkeypatch.delenv("STATE_DIR", raising=False)
        monkeypatch.setenv("OUTPUT_DIR", "/custom/output")

        assert load_config().state_dir == "/custom/output/.state"

    def test_state_dir_overrides_the_default(self, monkeypatch):
        monkeypatch.setenv("OUTPUT_DIR", "/custom/output")
        monkeypatch.setenv("STATE_DIR", "/var/lib/activities/state")

        assert load_config().state_dir == "/var/lib/activities/state"

    def test_empty_state_dir_falls_back_to_the_default(self, monkeypatch):
        """An unset variable in compose arrives as "", which must not become the CWD."""
        monkeypatch.setenv("OUTPUT_DIR", "/custom/output")
        monkeypatch.setenv("STATE_DIR", "")

        assert load_config().state_dir == "/custom/output/.state"


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
        monkeypatch.setenv("TRACKERS", "garmin,strava")

        with pytest.raises(ValueError, match="Invalid TRACKERS entry 'strava'"):
            load_config()

    def test_empty_trackers_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", " , ")

        with pytest.raises(ValueError, match="TRACKERS must not be empty"):
            load_config()


class TestDownloadTargetPath:
    """A destination is a folder that receives a format, not a folder below one."""

    def test_path_is_the_format_when_no_folder_is_given(self):
        assert DownloadTarget("FIT").path == "FIT"

    def test_path_is_the_folder_as_written(self):
        assert DownloadTarget("FIT", "app2").path == "app2"

    def test_path_keeps_every_level_of_a_nested_folder(self):
        assert DownloadTarget("GPX", "GPX/user@example.com").path == "GPX/user@example.com"

    def test_an_omitted_folder_equals_one_named_after_the_format(self):
        """Canonical form, so the two spellings deduplicate against each other."""
        assert DownloadTarget("FIT") == DownloadTarget("FIT", "FIT")

    def test_two_formats_can_share_one_folder(self):
        assert DownloadTarget("GPX", "app2") != DownloadTarget("FIT", "app2")
        assert DownloadTarget("GPX", "app2").path == DownloadTarget("FIT", "app2").path


class TestParseDownloadTargets:
    def test_bare_format_writes_to_a_folder_of_the_same_name(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "FIT")

        assert load_config().download_targets["garmin"] == [DownloadTarget("FIT", "FIT")]

    def test_named_destination(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "app2=GPX")

        assert load_config().download_targets["garmin"] == [DownloadTarget("GPX", "app2")]

    def test_one_destination_takes_several_formats(self, monkeypatch):
        """The case the old grammar could not express at all."""
        monkeypatch.setenv("DOWNLOAD_TARGETS", "app2=GPX+FIT")

        assert load_config().download_targets["garmin"] == [
            DownloadTarget("GPX", "app2"),
            DownloadTarget("FIT", "app2"),
        ]

    def test_nested_destination_folder(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "GPX/user@example.com=GPX")

        assert load_config().download_targets["garmin"] == [DownloadTarget("GPX", "GPX/user@example.com")]

    def test_formats_are_case_insensitive_and_folders_are_not(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "MyApp=gpx")

        assert load_config().download_targets["garmin"] == [DownloadTarget("GPX", "MyApp")]

    def test_whitespace_is_trimmed(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "  my folder = gpx + fit  ")

        assert load_config().download_targets["garmin"] == [
            DownloadTarget("GPX", "my folder"),
            DownloadTarget("FIT", "my folder"),
        ]

    def test_several_destinations(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "FIT, strava=GPX, app2=GPX+FIT")

        assert load_config().download_targets["garmin"] == [
            DownloadTarget("FIT", "FIT"),
            DownloadTarget("GPX", "strava"),
            DownloadTarget("GPX", "app2"),
            DownloadTarget("FIT", "app2"),
        ]

    def test_duplicate_pairs_are_deduplicated_keeping_first_occurrence(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "app2=GPX+GPX, app2=GPX, FIT, fit")

        assert load_config().download_targets["garmin"] == [
            DownloadTarget("GPX", "app2"),
            DownloadTarget("FIT", "FIT"),
        ]

    @pytest.mark.parametrize("raw", ["bogus", "app2=bogus", "app2=GPX+bogus"])
    def test_invalid_format_raises_value_error(self, monkeypatch, raw):
        monkeypatch.setenv("DOWNLOAD_TARGETS", raw)

        with pytest.raises(ValueError, match="Invalid DOWNLOAD_TARGETS format"):
            load_config()

    def test_empty_value_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", " , ")

        with pytest.raises(ValueError, match="must not be empty"):
            load_config()

    @pytest.mark.parametrize(
        "folder",
        ["", "..", ".", "../escape", "a/../../escape", "back\\slash", "/absolute", "a//b", "a/./b"],
    )
    def test_unsafe_destination_folder_raises_value_error(self, monkeypatch, folder):
        monkeypatch.setenv("DOWNLOAD_TARGETS", f"{folder}=FIT")

        with pytest.raises(ValueError):
            load_config()

    def test_destination_that_escapes_the_output_dir_is_rejected(self, monkeypatch):
        """Belt and braces: the path check runs again against the resolved output dir."""
        monkeypatch.setenv("OUTPUT_DIR", "/app/data")
        monkeypatch.setenv("DOWNLOAD_TARGETS", "..=FIT")

        with pytest.raises(ValueError):
            load_config()


class TestOutputDirContainment:
    """The path components are checked first; this is the check they cannot make."""

    def test_symlinked_destination_that_leaves_the_output_dir_is_rejected(self, tmp_path):
        output = tmp_path / "data"
        output.mkdir()
        (tmp_path / "outside").mkdir()
        (output / "app2").symlink_to(tmp_path / "outside")

        with pytest.raises(ValueError, match="resolves outside"):
            _assert_within(str(output), {"garmin": [DownloadTarget("FIT", "app2")]})

    def test_a_destination_inside_the_output_dir_passes(self, tmp_path):
        _assert_within(str(tmp_path), {"garmin": [DownloadTarget("FIT", "app2/raw")]})


class TestDestinationPlaceholders:
    def test_format_placeholder(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "app2/{format}=GPX+FIT")

        assert load_config().download_targets["garmin"] == [
            DownloadTarget("GPX", "app2/GPX"),
            DownloadTarget("FIT", "app2/FIT"),
        ]

    def test_tracker_placeholder(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", "garmin,wahoo")
        monkeypatch.setenv("DOWNLOAD_TARGETS", "archive/{tracker}=FIT")

        config = load_config()

        assert config.download_targets["garmin"] == [DownloadTarget("FIT", "archive/garmin")]
        assert config.download_targets["wahoo"] == [DownloadTarget("FIT", "archive/wahoo")]

    def test_both_placeholders(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "{tracker}/{format}=GPX")

        assert load_config().download_targets["garmin"] == [DownloadTarget("GPX", "garmin/GPX")]

    def test_unknown_placeholder_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "{ingesting_app}=FIT")

        with pytest.raises(ValueError, match="unknown placeholder"):
            load_config()

    def test_unbalanced_brace_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "app2/{format=FIT")

        with pytest.raises(ValueError, match="unbalanced"):
            load_config()

    def test_a_placeholder_cannot_smuggle_in_a_traversal(self, monkeypatch):
        """The path check runs on the rendered folder, not on the template."""
        monkeypatch.setenv("DOWNLOAD_TARGETS", "{format}/../../escape=FIT")

        with pytest.raises(ValueError):
            load_config()


class TestPerTrackerTargets:
    def test_tracker_variable_replaces_the_shared_default(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", "garmin,wahoo")
        monkeypatch.setenv("DOWNLOAD_TARGETS", "FIT")
        monkeypatch.setenv("GARMIN_DOWNLOAD_TARGETS", "FIT, GPX/user@example.com=GPX")

        config = load_config()

        assert config.download_targets["garmin"] == [
            DownloadTarget("FIT", "FIT"),
            DownloadTarget("GPX", "GPX/user@example.com"),
        ]
        assert config.download_targets["wahoo"] == [DownloadTarget("FIT", "FIT")]

    def test_a_tracker_without_its_own_variable_inherits_the_default(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", "garmin,wahoo")
        monkeypatch.setenv("WAHOO_DOWNLOAD_TARGETS", "wahoo-inbox=FIT")
        monkeypatch.delenv("DOWNLOAD_TARGETS", raising=False)

        config = load_config()

        assert config.download_targets["garmin"] == [DownloadTarget("FIT", "FIT")]
        assert config.download_targets["wahoo"] == [DownloadTarget("FIT", "wahoo-inbox")]

    def test_unknown_tracker_prefix_raises_value_error(self, monkeypatch):
        """A typo must not silently do nothing, which is this variable's usual failure."""
        monkeypatch.setenv("GARMN_DOWNLOAD_TARGETS", "FIT")

        with pytest.raises(ValueError, match="Unknown tracker 'garmn'"):
            load_config()

    def test_variable_for_a_disabled_tracker_warns_and_is_ignored(self, monkeypatch, caplog):
        """Turning a tracker off temporarily must not break the whole configuration."""
        monkeypatch.setenv("TRACKERS", "garmin")
        monkeypatch.setenv("WAHOO_DOWNLOAD_TARGETS", "wahoo-inbox=FIT")

        config = load_config()

        assert set(config.download_targets) == {"garmin"}
        assert "not in TRACKERS" in caplog.text

    def test_empty_tracker_variable_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TARGETS", "shared=FIT")
        monkeypatch.setenv("GARMIN_DOWNLOAD_TARGETS", "")

        assert load_config().download_targets["garmin"] == [DownloadTarget("FIT", "shared")]

    def test_format_a_tracker_cannot_supply_is_an_error_when_asked_for_by_name(self, monkeypatch):
        """Wahoo has no GPX. Asserting otherwise is a mistake, not something to warn about."""
        monkeypatch.setenv("TRACKERS", "wahoo")
        monkeypatch.setenv("WAHOO_DOWNLOAD_TARGETS", "app2=GPX")

        with pytest.raises(ValueError, match="which wahoo does not provide"):
            load_config()

    def test_format_a_tracker_cannot_supply_is_allowed_in_the_shared_default(self, monkeypatch):
        """Inherited, not asserted: the downloader warns and skips it at run time."""
        monkeypatch.setenv("TRACKERS", "garmin,wahoo")
        monkeypatch.setenv("DOWNLOAD_TARGETS", "FIT,GPX")

        config = load_config()

        assert DownloadTarget("GPX", "GPX") in config.download_targets["wahoo"]


class TestLegacyDownloadFormats:
    """DOWNLOAD_FORMATS keeps working, with its own grammar, until it is removed."""

    def test_bare_formats(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "gpx, tcx")

        assert load_config().download_targets["garmin"] == [DownloadTarget("GPX"), DownloadTarget("TCX")]

    def test_subfolder_still_nests_under_the_format(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT:user@example.com")

        targets = load_config().download_targets["garmin"]

        assert targets == [DownloadTarget("FIT", "FIT/user@example.com")]
        assert targets[0].path == "FIT/user@example.com"

    def test_same_format_in_several_subfolders(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT, FIT:strava-inbox, FIT:archive, GPX")

        assert [t.path for t in load_config().download_targets["garmin"]] == [
            "FIT",
            "FIT/strava-inbox",
            "FIT/archive",
            "GPX",
        ]

    def test_applies_to_every_tracker(self, monkeypatch):
        monkeypatch.setenv("TRACKERS", "garmin,wahoo")
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT:inbox")

        config = load_config()

        assert config.download_targets["garmin"] == config.download_targets["wahoo"]

    def test_deduplication_keeps_first_occurrence_order(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "GPX:a,FIT:b,GPX:a,TCX:c")

        assert [t.path for t in load_config().download_targets["garmin"]] == ["GPX/a", "FIT/b", "TCX/c"]

    def test_same_subfolder_under_two_formats_stays_separate(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "GPX:inbox,TCX:inbox")

        assert [t.path for t in load_config().download_targets["garmin"]] == ["GPX/inbox", "TCX/inbox"]

    def test_logs_a_deprecation_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT")

        load_config()

        assert "DOWNLOAD_FORMATS is deprecated" in caplog.text

    @pytest.mark.parametrize("folder", ["nested/inbox", "..", ".", "../escape", "back\\slash", "/absolute", ""])
    def test_unsafe_subfolder_names_raise_value_error(self, monkeypatch, folder):
        monkeypatch.setenv("DOWNLOAD_FORMATS", f"FIT:{folder}")

        with pytest.raises(ValueError):
            load_config()

    def test_invalid_format_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "bogus:inbox")

        with pytest.raises(ValueError, match="Invalid DOWNLOAD_FORMATS format"):
            load_config()

    def test_empty_value_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", " , ")

        with pytest.raises(ValueError, match="must not be empty"):
            load_config()


class TestVariableConflicts:
    def test_legacy_and_new_variable_together_raise_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT")
        monkeypatch.setenv("DOWNLOAD_TARGETS", "app2=FIT")

        with pytest.raises(ValueError, match="must not both be set"):
            load_config()

    def test_legacy_and_per_tracker_variable_together_raise_value_error(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_FORMATS", "FIT")
        monkeypatch.setenv("GARMIN_DOWNLOAD_TARGETS", "app2=FIT")

        with pytest.raises(ValueError, match="must not both be set"):
            load_config()

    def test_neither_variable_defaults_to_fit(self, monkeypatch):
        monkeypatch.delenv("DOWNLOAD_FORMATS", raising=False)
        monkeypatch.delenv("DOWNLOAD_TARGETS", raising=False)

        assert load_config().download_targets["garmin"] == [DownloadTarget("FIT")]
