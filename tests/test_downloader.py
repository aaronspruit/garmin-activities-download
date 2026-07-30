"""Tests for activity download and deduplication logic."""

import pytest
from garminconnect import GarminConnectTooManyRequestsError

from src.config import DownloadTarget
from src.downloader import download_new_activities
from tests.conftest import (
    SAMPLE_ACTIVITY,
    SAMPLE_ACTIVITY_2,
    SAMPLE_EMPTY_ZIP,
    SAMPLE_FIT_CONTENT,
    SAMPLE_FIT_ZIP,
    SAMPLE_GPX,
    SAMPLE_TCX,
)


class TestDownloadNewActivities:
    def test_downloads_new_activity(self, mock_garmin, tmp_path):
        count = download_new_activities(mock_garmin, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 1
        gpx_file = tmp_path / "GPX" / f"{SAMPLE_ACTIVITY['activityId']}.gpx"
        assert gpx_file.exists()
        assert gpx_file.read_bytes() == SAMPLE_GPX

    def test_skips_existing_activity(self, mock_garmin, tmp_path):
        (tmp_path / "GPX").mkdir()
        existing = tmp_path / "GPX" / f"{SAMPLE_ACTIVITY['activityId']}.gpx"
        existing.write_bytes(b"existing data")

        count = download_new_activities(mock_garmin, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 0
        mock_garmin.download_activity.assert_not_called()
        assert existing.read_bytes() == b"existing data"

    def test_downloads_multiple_activities(self, mock_garmin, tmp_path):
        mock_garmin.get_activities_by_date.return_value = [
            SAMPLE_ACTIVITY,
            SAMPLE_ACTIVITY_2,
        ]

        count = download_new_activities(mock_garmin, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 2
        assert (tmp_path / "GPX" / f"{SAMPLE_ACTIVITY['activityId']}.gpx").exists()
        assert (tmp_path / "GPX" / f"{SAMPLE_ACTIVITY_2['activityId']}.gpx").exists()

    def test_skips_existing_downloads_new(self, mock_garmin, tmp_path):
        mock_garmin.get_activities_by_date.return_value = [
            SAMPLE_ACTIVITY,
            SAMPLE_ACTIVITY_2,
        ]
        (tmp_path / "GPX").mkdir()
        existing = tmp_path / "GPX" / f"{SAMPLE_ACTIVITY['activityId']}.gpx"
        existing.write_bytes(b"existing")

        count = download_new_activities(mock_garmin, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 1
        assert mock_garmin.download_activity.call_count == 1

    def test_creates_output_directory(self, mock_garmin, tmp_path):
        output = tmp_path / "nested" / "dir"
        download_new_activities(mock_garmin, str(output), targets=["GPX"], days_back=7, download_delay=0)

        assert output.exists()

    def test_handles_no_activities(self, mock_garmin, tmp_path):
        mock_garmin.get_activities_by_date.return_value = []

        count = download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

        assert count == 0

    def test_propagates_rate_limit_error(self, mock_garmin, tmp_path):
        mock_garmin.download_activity.side_effect = GarminConnectTooManyRequestsError("429")

        with pytest.raises(GarminConnectTooManyRequestsError):
            download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

    def test_calls_download_with_gpx_format(self, mock_garmin, tmp_path):
        download_new_activities(mock_garmin, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        mock_garmin.download_activity.assert_called_once_with(
            SAMPLE_ACTIVITY["activityId"],
            dl_fmt=mock_garmin.ActivityDownloadFormat.GPX,
        )

    def test_extracts_fit_from_zip(self, mock_garmin, tmp_path):
        mock_garmin.download_activity.return_value = SAMPLE_FIT_ZIP

        count = download_new_activities(mock_garmin, str(tmp_path), targets=["FIT"], days_back=7, download_delay=0)

        assert count == 1
        fit_file = tmp_path / "FIT" / f"{SAMPLE_ACTIVITY['activityId']}.fit"
        assert fit_file.exists()
        assert fit_file.read_bytes() == SAMPLE_FIT_CONTENT

    def test_skips_activity_when_no_fit_member_in_zip(self, mock_garmin, tmp_path):
        mock_garmin.download_activity.return_value = SAMPLE_EMPTY_ZIP

        count = download_new_activities(mock_garmin, str(tmp_path), targets=["FIT"], days_back=7, download_delay=0)

        assert count == 0
        assert not (tmp_path / "FIT" / f"{SAMPLE_ACTIVITY['activityId']}.fit").exists()

    def test_downloads_all_requested_formats_for_single_activity(self, mock_garmin, tmp_path):
        def fake_download(activity_id, dl_fmt):
            if dl_fmt == mock_garmin.ActivityDownloadFormat.ORIGINAL:
                return SAMPLE_FIT_ZIP
            if dl_fmt == mock_garmin.ActivityDownloadFormat.GPX:
                return SAMPLE_GPX
            if dl_fmt == mock_garmin.ActivityDownloadFormat.TCX:
                return SAMPLE_TCX
            raise AssertionError(f"unexpected dl_fmt {dl_fmt}")

        mock_garmin.download_activity.side_effect = fake_download

        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=["FIT", "GPX", "TCX"],
            days_back=7,
            download_delay=0,
        )

        activity_id = SAMPLE_ACTIVITY["activityId"]
        assert count == 3
        assert (tmp_path / "FIT" / f"{activity_id}.fit").read_bytes() == SAMPLE_FIT_CONTENT
        assert (tmp_path / "GPX" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX
        assert (tmp_path / "TCX" / f"{activity_id}.tcx").read_bytes() == SAMPLE_TCX

    def test_per_format_dedup_only_downloads_missing_formats(self, mock_garmin, tmp_path):
        def fake_download(activity_id, dl_fmt):
            if dl_fmt == mock_garmin.ActivityDownloadFormat.GPX:
                return SAMPLE_GPX
            if dl_fmt == mock_garmin.ActivityDownloadFormat.TCX:
                return SAMPLE_TCX
            raise AssertionError(f"unexpected dl_fmt {dl_fmt}")

        mock_garmin.download_activity.side_effect = fake_download

        activity_id = SAMPLE_ACTIVITY["activityId"]
        (tmp_path / "GPX").mkdir()
        (tmp_path / "GPX" / f"{activity_id}.gpx").write_bytes(b"existing")

        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=["GPX", "TCX"],
            days_back=7,
            download_delay=0,
        )

        assert count == 1
        assert mock_garmin.download_activity.call_count == 1
        assert (tmp_path / "GPX" / f"{activity_id}.gpx").read_bytes() == b"existing"
        assert (tmp_path / "TCX" / f"{activity_id}.tcx").read_bytes() == SAMPLE_TCX


class TestCustomFolderTargets:
    def test_saves_format_to_custom_folder(self, mock_garmin, tmp_path):
        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=[DownloadTarget("GPX", "user@example.com")],
            days_back=7,
            download_delay=0,
        )

        assert count == 1
        saved = tmp_path / "user@example.com" / f"{SAMPLE_ACTIVITY['activityId']}.gpx"
        assert saved.read_bytes() == SAMPLE_GPX
        assert not (tmp_path / "GPX").exists()

    def test_same_format_to_multiple_folders_downloads_once(self, mock_garmin, tmp_path):
        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=[DownloadTarget("GPX", "inbox-a"), DownloadTarget("GPX", "inbox-b")],
            days_back=7,
            download_delay=0,
        )

        activity_id = SAMPLE_ACTIVITY["activityId"]
        assert count == 2
        assert mock_garmin.download_activity.call_count == 1
        assert (tmp_path / "inbox-a" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX
        assert (tmp_path / "inbox-b" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX

    def test_fit_extracted_once_and_written_to_each_folder(self, mock_garmin, tmp_path):
        mock_garmin.download_activity.return_value = SAMPLE_FIT_ZIP

        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=[DownloadTarget("FIT", "system-one"), DownloadTarget("FIT", "system-two")],
            days_back=7,
            download_delay=0,
        )

        activity_id = SAMPLE_ACTIVITY["activityId"]
        assert count == 2
        assert mock_garmin.download_activity.call_count == 1
        assert (tmp_path / "system-one" / f"{activity_id}.fit").read_bytes() == SAMPLE_FIT_CONTENT
        assert (tmp_path / "system-two" / f"{activity_id}.fit").read_bytes() == SAMPLE_FIT_CONTENT

    def test_refills_only_the_folder_that_was_emptied(self, mock_garmin, tmp_path):
        """A consumer that deletes files on import gets them again; the other folder is untouched."""
        activity_id = SAMPLE_ACTIVITY["activityId"]
        (tmp_path / "keeps-files").mkdir()
        (tmp_path / "keeps-files" / f"{activity_id}.gpx").write_bytes(b"existing")

        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=[DownloadTarget("GPX", "keeps-files"), DownloadTarget("GPX", "deletes-on-import")],
            days_back=7,
            download_delay=0,
        )

        assert count == 1
        assert mock_garmin.download_activity.call_count == 1
        assert (tmp_path / "keeps-files" / f"{activity_id}.gpx").read_bytes() == b"existing"
        assert (tmp_path / "deletes-on-import" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX

    def test_failed_fit_extraction_writes_to_no_folder(self, mock_garmin, tmp_path):
        mock_garmin.download_activity.return_value = SAMPLE_EMPTY_ZIP

        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=[DownloadTarget("FIT", "system-one"), DownloadTarget("FIT", "system-two")],
            days_back=7,
            download_delay=0,
        )

        activity_id = SAMPLE_ACTIVITY["activityId"]
        assert count == 0
        assert not (tmp_path / "system-one" / f"{activity_id}.fit").exists()
        assert not (tmp_path / "system-two" / f"{activity_id}.fit").exists()

    def test_mixed_formats_share_one_folder(self, mock_garmin, tmp_path):
        def fake_download(activity_id, dl_fmt):
            return SAMPLE_GPX if dl_fmt == mock_garmin.ActivityDownloadFormat.GPX else SAMPLE_TCX

        mock_garmin.download_activity.side_effect = fake_download

        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=[DownloadTarget("GPX", "shared"), DownloadTarget("TCX", "shared")],
            days_back=7,
            download_delay=0,
        )

        activity_id = SAMPLE_ACTIVITY["activityId"]
        assert count == 2
        assert (tmp_path / "shared" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX
        assert (tmp_path / "shared" / f"{activity_id}.tcx").read_bytes() == SAMPLE_TCX

    def test_defaults_to_fit_when_no_targets_given(self, mock_garmin, tmp_path):
        mock_garmin.download_activity.return_value = SAMPLE_FIT_ZIP

        count = download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

        assert count == 1
        assert (tmp_path / "FIT" / f"{SAMPLE_ACTIVITY['activityId']}.fit").exists()
