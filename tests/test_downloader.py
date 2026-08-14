"""Tests for activity download and deduplication logic."""

import dataclasses
from unittest.mock import MagicMock

import pytest
from garminconnect import GarminConnectTooManyRequestsError

from src.config import DownloadTarget
from src.downloader import download_new_activities
from src.trackers.base import ActivityDownloadError, UnsafeActivityIdError
from tests.conftest import (
    ACTIVITY,
    ACTIVITY_2,
    SAMPLE_FIT_CONTENT,
    SAMPLE_GPX,
    SAMPLE_TCX,
    FakeTracker,
)


class TestDownloadNewActivities:
    def test_downloads_new_activity(self, mock_tracker, tmp_path):
        count = download_new_activities(mock_tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 1
        gpx_file = tmp_path / "GPX" / f"fake-{ACTIVITY.id}.gpx"
        assert gpx_file.exists()
        assert gpx_file.read_bytes() == SAMPLE_GPX

    def test_skips_existing_activity(self, mock_tracker, tmp_path):
        (tmp_path / "GPX").mkdir()
        existing = tmp_path / "GPX" / f"fake-{ACTIVITY.id}.gpx"
        existing.write_bytes(b"existing data")

        count = download_new_activities(mock_tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 0
        mock_tracker.download.assert_not_called()
        assert existing.read_bytes() == b"existing data"

    def test_downloads_multiple_activities(self, tmp_path):
        tracker = FakeTracker(activities=[ACTIVITY, ACTIVITY_2])

        count = download_new_activities(tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 2
        assert (tmp_path / "GPX" / f"fake-{ACTIVITY.id}.gpx").exists()
        assert (tmp_path / "GPX" / f"fake-{ACTIVITY_2.id}.gpx").exists()

    def test_skips_existing_downloads_new(self, tmp_path):
        tracker = FakeTracker(activities=[ACTIVITY, ACTIVITY_2])
        (tmp_path / "GPX").mkdir()
        existing = tmp_path / "GPX" / f"fake-{ACTIVITY.id}.gpx"
        existing.write_bytes(b"existing")

        count = download_new_activities(tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 1
        assert tracker.download.call_count == 1

    def test_creates_output_directory(self, mock_tracker, tmp_path):
        output = tmp_path / "nested" / "dir"
        download_new_activities(mock_tracker, str(output), targets=["GPX"], days_back=7, download_delay=0)

        assert output.exists()

    def test_handles_no_activities(self, tmp_path):
        tracker = FakeTracker(activities=[])

        count = download_new_activities(tracker, str(tmp_path), days_back=7, download_delay=0)

        assert count == 0

    def test_propagates_rate_limit_error(self, mock_tracker, tmp_path):
        mock_tracker.download.side_effect = GarminConnectTooManyRequestsError("429")

        with pytest.raises(GarminConnectTooManyRequestsError):
            download_new_activities(mock_tracker, str(tmp_path), days_back=7, download_delay=0)

    def test_passes_requested_format_to_tracker(self, mock_tracker, tmp_path):
        download_new_activities(mock_tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        mock_tracker.download.assert_called_once_with(ACTIVITY, "GPX")

    def test_passes_date_range_to_tracker(self, mock_tracker, tmp_path):
        download_new_activities(mock_tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        start_date, end_date = mock_tracker.list_activities.call_args.args
        assert start_date < end_date
        assert len(start_date) == len(end_date) == len("YYYY-MM-DD")

    def test_skips_activity_when_download_fails(self, mock_tracker, tmp_path):
        mock_tracker.download.side_effect = ActivityDownloadError("no .fit member")

        count = download_new_activities(mock_tracker, str(tmp_path), targets=["FIT"], days_back=7, download_delay=0)

        assert count == 0
        assert not (tmp_path / "FIT" / f"fake-{ACTIVITY.id}.fit").exists()

    def test_downloads_all_requested_formats_for_single_activity(self, mock_tracker, tmp_path):
        payloads = {"FIT": SAMPLE_FIT_CONTENT, "GPX": SAMPLE_GPX, "TCX": SAMPLE_TCX}
        mock_tracker.download.side_effect = lambda activity, fmt: payloads[fmt]

        count = download_new_activities(
            mock_tracker,
            str(tmp_path),
            targets=["FIT", "GPX", "TCX"],
            days_back=7,
            download_delay=0,
        )

        assert count == 3
        assert (tmp_path / "FIT" / f"fake-{ACTIVITY.id}.fit").read_bytes() == SAMPLE_FIT_CONTENT
        assert (tmp_path / "GPX" / f"fake-{ACTIVITY.id}.gpx").read_bytes() == SAMPLE_GPX
        assert (tmp_path / "TCX" / f"fake-{ACTIVITY.id}.tcx").read_bytes() == SAMPLE_TCX

    def test_per_format_dedup_only_downloads_missing_formats(self, mock_tracker, tmp_path):
        payloads = {"GPX": SAMPLE_GPX, "TCX": SAMPLE_TCX}
        mock_tracker.download.side_effect = lambda activity, fmt: payloads[fmt]

        (tmp_path / "GPX").mkdir()
        (tmp_path / "GPX" / f"fake-{ACTIVITY.id}.gpx").write_bytes(b"existing")

        count = download_new_activities(
            mock_tracker,
            str(tmp_path),
            targets=["GPX", "TCX"],
            days_back=7,
            download_delay=0,
        )

        assert count == 1
        assert mock_tracker.download.call_count == 1
        assert (tmp_path / "GPX" / f"fake-{ACTIVITY.id}.gpx").read_bytes() == b"existing"
        assert (tmp_path / "TCX" / f"fake-{ACTIVITY.id}.tcx").read_bytes() == SAMPLE_TCX


class TestTrackerFilenamePrefix:
    """Two trackers can hand out the same id, so the filename must carry the tracker."""

    def test_filename_is_prefixed_with_tracker_name(self, mock_tracker, tmp_path):
        download_new_activities(mock_tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert [p.name for p in (tmp_path / "GPX").iterdir()] == [f"fake-{ACTIVITY.id}.gpx"]

    def test_same_id_from_two_trackers_does_not_collide(self, tmp_path):
        class OtherTracker(FakeTracker):
            name = "other"

        first = FakeTracker(data=b"from-fake")
        second = OtherTracker(data=b"from-other")

        download_new_activities(first, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)
        count = download_new_activities(second, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        # The second tracker sees no file of its own, so it downloads rather
        # than wrongly deduplicating against the first tracker's file.
        assert count == 1
        assert (tmp_path / "GPX" / f"fake-{ACTIVITY.id}.gpx").read_bytes() == b"from-fake"
        assert (tmp_path / "GPX" / f"other-{ACTIVITY.id}.gpx").read_bytes() == b"from-other"


class TestUnsupportedFormats:
    """A tracker downloads the formats it can and warns about the rest."""

    def test_skips_formats_the_tracker_does_not_provide(self, tmp_path, caplog):
        class FitOnlyTracker(FakeTracker):
            name = "fitonly"
            supported_formats = frozenset({"FIT"})

        tracker = FitOnlyTracker(data=SAMPLE_FIT_CONTENT)

        count = download_new_activities(tracker, str(tmp_path), targets=["FIT", "GPX"], days_back=7, download_delay=0)

        assert count == 1
        assert (tmp_path / "FIT" / f"fitonly-{ACTIVITY.id}.fit").exists()
        # The unsupported format gets no folder and no download attempt.
        assert not (tmp_path / "GPX").exists()
        assert tracker.download.call_count == 1
        assert "does not provide GPX" in caplog.text

    def test_returns_zero_without_listing_when_no_format_is_supported(self, tmp_path, caplog):
        class FitOnlyTracker(FakeTracker):
            name = "fitonly"
            supported_formats = frozenset({"FIT"})

        tracker = FitOnlyTracker()

        count = download_new_activities(tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 0
        # Nothing is fetched at all. Wahoo relies on this: refreshing a token it
        # never uses would leak an unrevoked token against its per-user cap.
        tracker.list_activities.assert_not_called()
        tracker.download.assert_not_called()
        assert list(tmp_path.iterdir()) == []
        assert "provides none of the requested formats" in caplog.text


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
    def test_rejects_unsafe_id(self, tmp_path, activity_id):
        tracker = FakeTracker(activities=[dataclasses.replace(ACTIVITY, id=activity_id)])
        output = tmp_path / "data"

        with pytest.raises(UnsafeActivityIdError, match="non-alphanumeric activity id"):
            download_new_activities(tracker, str(output), targets=["GPX"], days_back=7, download_delay=0)

        tracker.download.assert_not_called()
        # Nothing was written anywhere -- inside the output dir or outside it.
        assert list(tmp_path.rglob("*.gpx")) == []

    def test_aborts_run_without_processing_later_activities(self, tmp_path):
        tracker = FakeTracker(activities=[dataclasses.replace(ACTIVITY, id="../../escaped"), ACTIVITY_2])

        with pytest.raises(UnsafeActivityIdError):
            download_new_activities(tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        tracker.download.assert_not_called()
        assert not (tmp_path / "GPX" / f"fake-{ACTIVITY_2.id}.gpx").exists()

    @pytest.mark.parametrize("activity_id", ["19876543210", "a1b2c3", "ACT42"])
    def test_accepts_alphanumeric_id(self, tmp_path, activity_id):
        tracker = FakeTracker(activities=[dataclasses.replace(ACTIVITY, id=activity_id)])

        count = download_new_activities(tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=0)

        assert count == 1
        assert (tmp_path / "GPX" / f"fake-{activity_id}.gpx").read_bytes() == SAMPLE_GPX


class TestCustomSubfolderTargets:
    def test_saves_format_to_custom_subfolder_under_the_format_folder(self, mock_tracker, tmp_path):
        count = download_new_activities(
            mock_tracker,
            str(tmp_path),
            targets=[DownloadTarget("GPX", "user@example.com")],
            days_back=7,
            download_delay=0,
        )

        filename = f"fake-{ACTIVITY.id}.gpx"
        assert count == 1
        assert (tmp_path / "GPX" / "user@example.com" / filename).read_bytes() == SAMPLE_GPX
        # Nothing lands at the root, nor directly in the format folder.
        assert not (tmp_path / "user@example.com").exists()
        assert not (tmp_path / "GPX" / filename).exists()

    def test_bare_and_subfoldered_same_format_download_once(self, mock_tracker, tmp_path):
        mock_tracker.download.return_value = SAMPLE_FIT_CONTENT

        count = download_new_activities(
            mock_tracker,
            str(tmp_path),
            targets=[DownloadTarget("FIT"), DownloadTarget("FIT", "folderA")],
            days_back=7,
            download_delay=0,
        )

        filename = f"fake-{ACTIVITY.id}.fit"
        assert count == 2
        assert mock_tracker.download.call_count == 1
        assert (tmp_path / "FIT" / filename).read_bytes() == SAMPLE_FIT_CONTENT
        assert (tmp_path / "FIT" / "folderA" / filename).read_bytes() == SAMPLE_FIT_CONTENT

    def test_same_format_to_multiple_subfolders_downloads_once(self, mock_tracker, tmp_path):
        count = download_new_activities(
            mock_tracker,
            str(tmp_path),
            targets=[DownloadTarget("GPX", "inbox-a"), DownloadTarget("GPX", "inbox-b")],
            days_back=7,
            download_delay=0,
        )

        filename = f"fake-{ACTIVITY.id}.gpx"
        assert count == 2
        assert mock_tracker.download.call_count == 1
        assert (tmp_path / "GPX" / "inbox-a" / filename).read_bytes() == SAMPLE_GPX
        assert (tmp_path / "GPX" / "inbox-b" / filename).read_bytes() == SAMPLE_GPX

    def test_refills_only_the_subfolder_that_was_emptied(self, mock_tracker, tmp_path):
        """A consumer that deletes files on import gets them again; the other folder is untouched."""
        filename = f"fake-{ACTIVITY.id}.gpx"
        (tmp_path / "GPX" / "keeps-files").mkdir(parents=True)
        (tmp_path / "GPX" / "keeps-files" / filename).write_bytes(b"existing")

        count = download_new_activities(
            mock_tracker,
            str(tmp_path),
            targets=[DownloadTarget("GPX", "keeps-files"), DownloadTarget("GPX", "deletes-on-import")],
            days_back=7,
            download_delay=0,
        )

        assert count == 1
        assert mock_tracker.download.call_count == 1
        assert (tmp_path / "GPX" / "keeps-files" / filename).read_bytes() == b"existing"
        assert (tmp_path / "GPX" / "deletes-on-import" / filename).read_bytes() == SAMPLE_GPX

    def test_failed_download_writes_to_no_subfolder(self, mock_tracker, tmp_path):
        mock_tracker.download.side_effect = ActivityDownloadError("no .fit member")

        count = download_new_activities(
            mock_tracker,
            str(tmp_path),
            targets=[DownloadTarget("FIT", "system-one"), DownloadTarget("FIT", "system-two")],
            days_back=7,
            download_delay=0,
        )

        filename = f"fake-{ACTIVITY.id}.fit"
        assert count == 0
        assert not (tmp_path / "FIT" / "system-one" / filename).exists()
        assert not (tmp_path / "FIT" / "system-two" / filename).exists()

    def test_same_subfolder_name_stays_separate_per_format(self, mock_tracker, tmp_path):
        """A shared subfolder name never mixes formats: each format keeps its own tree."""
        payloads = {"GPX": SAMPLE_GPX, "TCX": SAMPLE_TCX}
        mock_tracker.download.side_effect = lambda activity, fmt: payloads[fmt]

        count = download_new_activities(
            mock_tracker,
            str(tmp_path),
            targets=[DownloadTarget("GPX", "shared"), DownloadTarget("TCX", "shared")],
            days_back=7,
            download_delay=0,
        )

        assert count == 2
        assert (tmp_path / "GPX" / "shared" / f"fake-{ACTIVITY.id}.gpx").read_bytes() == SAMPLE_GPX
        assert (tmp_path / "TCX" / "shared" / f"fake-{ACTIVITY.id}.tcx").read_bytes() == SAMPLE_TCX
        assert not (tmp_path / "shared").exists()
        assert list((tmp_path / "GPX" / "shared").glob("*.tcx")) == []

    def test_defaults_to_fit_when_no_targets_given(self, mock_tracker, tmp_path):
        mock_tracker.download.return_value = SAMPLE_FIT_CONTENT

        count = download_new_activities(mock_tracker, str(tmp_path), days_back=7, download_delay=0)

        assert count == 1
        assert (tmp_path / "FIT" / f"fake-{ACTIVITY.id}.fit").exists()


class TestRateLimitDelay:
    def test_sleeps_between_downloads(self, tmp_path, monkeypatch):
        tracker = FakeTracker(activities=[ACTIVITY, ACTIVITY_2])
        sleep = MagicMock()
        monkeypatch.setattr("src.downloader.time.sleep", sleep)

        download_new_activities(tracker, str(tmp_path), targets=["GPX"], days_back=7, download_delay=1.0)

        assert sleep.call_args_list == [((1.0,), {}), ((1.0,), {})]
