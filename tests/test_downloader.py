"""Tests for activity download and deduplication logic."""

import pytest
from garminconnect import GarminConnectTooManyRequestsError

from src.config import DownloadTarget
from src.downloader import UnsafeActivityIdError, download_new_activities
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


class TestActivityIdValidation:
    """The activity id is remote input that lands in a path, so it must stay alphanumeric."""

    @pytest.mark.parametrize(
        "activity_id",
        [
            "../../escaped",
            "../" * 6 + "tmp/escaped",
            "/tmp/absolute-escape",
            "sub/dir/nested",
            "..",
            "id\0null",
            "id with spaces",
            "١٢٣",
            "",
        ],
    )
    def test_rejects_unsafe_id(self, mock_garmin, tmp_path, activity_id):
        mock_garmin.get_activities_by_date.return_value = [{**SAMPLE_ACTIVITY, "activityId": activity_id}]
        output = tmp_path / "data"

        with pytest.raises(UnsafeActivityIdError, match="non-alphanumeric activity id"):
            download_new_activities(mock_garmin, str(output), targets=["GPX"], days_back=7, download_delay=0)

        mock_garmin.download_activity.assert_not_called()
        # Nothing was written anywhere -- inside the output dir or outside it.
        assert list(tmp_path.rglob("*.gpx")) == []

    def test_aborts_run_without_processing_later_activities(self, mock_garmin, tmp_path):
        mock_garmin.get_activities_by_date.return_value = [
            {**SAMPLE_ACTIVITY, "activityId": "../../escaped"},
            SAMPLE_ACTIVITY_2,
        ]

        with pytest.raises(UnsafeActivityIdError):
            download_new_activities(mock_garmin, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        mock_garmin.download_activity.assert_not_called()
        assert not (tmp_path / "GPX" / f"{SAMPLE_ACTIVITY_2['activityId']}.gpx").exists()

    @pytest.mark.parametrize("activity_id", ["19876543210", "a1b2c3", "ACT42"])
    def test_accepts_alphanumeric_id(self, mock_garmin, tmp_path, activity_id):
        mock_garmin.get_activities_by_date.return_value = [{**SAMPLE_ACTIVITY, "activityId": activity_id}]

        count = download_new_activities(mock_garmin, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 1
        assert (tmp_path / "GPX" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX


class TestCustomSubfolderTargets:
    def test_saves_format_to_custom_subfolder_under_the_format_folder(self, mock_garmin, tmp_path):
        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=[DownloadTarget("GPX", "user@example.com")],
            days_back=7,
            download_delay=0,
        )

        activity_id = SAMPLE_ACTIVITY["activityId"]
        assert count == 1
        assert (tmp_path / "GPX" / "user@example.com" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX
        # Nothing lands at the root, nor directly in the format folder.
        assert not (tmp_path / "user@example.com").exists()
        assert not (tmp_path / "GPX" / f"{activity_id}.gpx").exists()

    def test_bare_and_subfoldered_same_format_download_once(self, mock_garmin, tmp_path):
        mock_garmin.download_activity.return_value = SAMPLE_FIT_ZIP

        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=[DownloadTarget("FIT"), DownloadTarget("FIT", "folderA")],
            days_back=7,
            download_delay=0,
        )

        activity_id = SAMPLE_ACTIVITY["activityId"]
        assert count == 2
        assert mock_garmin.download_activity.call_count == 1
        assert (tmp_path / "FIT" / f"{activity_id}.fit").read_bytes() == SAMPLE_FIT_CONTENT
        assert (tmp_path / "FIT" / "folderA" / f"{activity_id}.fit").read_bytes() == SAMPLE_FIT_CONTENT

    def test_same_format_to_multiple_subfolders_downloads_once(self, mock_garmin, tmp_path):
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
        assert (tmp_path / "GPX" / "inbox-a" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX
        assert (tmp_path / "GPX" / "inbox-b" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX

    def test_fit_extracted_once_and_written_to_each_subfolder(self, mock_garmin, tmp_path):
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
        assert (tmp_path / "FIT" / "system-one" / f"{activity_id}.fit").read_bytes() == SAMPLE_FIT_CONTENT
        assert (tmp_path / "FIT" / "system-two" / f"{activity_id}.fit").read_bytes() == SAMPLE_FIT_CONTENT

    def test_refills_only_the_subfolder_that_was_emptied(self, mock_garmin, tmp_path):
        """A consumer that deletes files on import gets them again; the other folder is untouched."""
        activity_id = SAMPLE_ACTIVITY["activityId"]
        (tmp_path / "GPX" / "keeps-files").mkdir(parents=True)
        (tmp_path / "GPX" / "keeps-files" / f"{activity_id}.gpx").write_bytes(b"existing")

        count = download_new_activities(
            mock_garmin,
            str(tmp_path),
            targets=[DownloadTarget("GPX", "keeps-files"), DownloadTarget("GPX", "deletes-on-import")],
            days_back=7,
            download_delay=0,
        )

        assert count == 1
        assert mock_garmin.download_activity.call_count == 1
        assert (tmp_path / "GPX" / "keeps-files" / f"{activity_id}.gpx").read_bytes() == b"existing"
        assert (tmp_path / "GPX" / "deletes-on-import" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX

    def test_failed_fit_extraction_writes_to_no_subfolder(self, mock_garmin, tmp_path):
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
        assert not (tmp_path / "FIT" / "system-one" / f"{activity_id}.fit").exists()
        assert not (tmp_path / "FIT" / "system-two" / f"{activity_id}.fit").exists()

    def test_same_subfolder_name_stays_separate_per_format(self, mock_garmin, tmp_path):
        """A shared subfolder name never mixes formats: each format keeps its own tree."""

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
        assert (tmp_path / "GPX" / "shared" / f"{activity_id}.gpx").read_bytes() == SAMPLE_GPX
        assert (tmp_path / "TCX" / "shared" / f"{activity_id}.tcx").read_bytes() == SAMPLE_TCX
        assert not (tmp_path / "shared").exists()
        assert list((tmp_path / "GPX" / "shared").glob("*.tcx")) == []

    def test_defaults_to_fit_when_no_targets_given(self, mock_garmin, tmp_path):
        mock_garmin.download_activity.return_value = SAMPLE_FIT_ZIP

        count = download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

        assert count == 1
        assert (tmp_path / "FIT" / f"{SAMPLE_ACTIVITY['activityId']}.fit").exists()
