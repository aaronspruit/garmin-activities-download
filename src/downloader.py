"""Activity fetching and multi-format file download with deduplication."""

import logging
import os
import time
from datetime import datetime, timedelta

from src.config import DownloadTarget
from src.trackers import FORMAT_EXTENSIONS, ActivityDownloadError, Tracker, UnsafeActivityIdError

logger = logging.getLogger(__name__)


def _is_safe_activity_id(activity_id: object) -> bool:
    """Check that an activity id is a plain run of ASCII letters and digits.

    The id comes from the remote tracker's response and is interpolated into the
    output filename, so anything outside that set could escape `output_dir` --
    either through `..` segments or through an absolute path, which
    `os.path.join` would let override the preceding components entirely. Both
    trackers return numbers today, but letters are accepted so a future id
    scheme does not need a code change to keep working.
    """
    text = str(activity_id)
    return text.isascii() and text.isalnum()


def _normalize_targets(targets: list[DownloadTarget | str] | None) -> list[DownloadTarget]:
    """Accept targets as `DownloadTarget`s or bare format tokens (no subfolder)."""
    if not targets:
        return [DownloadTarget(format="FIT")]
    return [DownloadTarget(format=t) if isinstance(t, str) else t for t in targets]


def _supported_targets(tracker: Tracker, targets: list[DownloadTarget]) -> list[DownloadTarget]:
    """Drop targets whose format this tracker cannot supply, and say which."""
    supported = [t for t in targets if t.format in tracker.supported_formats]

    dropped = sorted({t.format for t in targets} - set(tracker.supported_formats))
    if dropped:
        logger.warning(
            "Tracker %s does not provide %s; skipping. It provides %s",
            tracker.name,
            ", ".join(dropped),
            ", ".join(sorted(tracker.supported_formats)),
        )
    return supported


def download_new_activities(
    tracker: Tracker,
    output_dir: str,
    targets: list[DownloadTarget | str] | None = None,
    days_back: int = 7,
    download_delay: float = 1.0,
) -> int:
    """Download activity files (one or more format/subfolder targets) not already saved.

    Every target lives under its format's folder, so a target is written to
    `<output_dir>/<FORMAT>` or `<output_dir>/<FORMAT>/<subfolder>` and a folder only
    ever holds files of one format. A format requested by several targets is fetched
    from the tracker once per activity and written to each folder that is still
    missing it. Files are named `<tracker>-<activityId>.<ext>`, so two trackers
    that hand out the same id do not collide.

    Args:
        tracker: Authenticated tracker.
        output_dir: Directory under which the target folders are created.
        targets: `DownloadTarget`s, or bare format tokens ("FIT", "GPX", "TCX") that
            save directly into the format folder. Defaults to FIT into "FIT".
            Formats the tracker does not provide are skipped with a warning.
        days_back: Number of days to look back for activities.
        download_delay: Seconds to wait between downloads (rate limit protection).

    Returns:
        Number of new activity files downloaded.

    Raises:
        UnsafeActivityIdError: If the tracker returns an activity id that is not
            alphanumeric, which could otherwise escape `output_dir`.
    """
    targets = _supported_targets(tracker, _normalize_targets(targets))
    if not targets:
        logger.warning("Tracker %s provides none of the requested formats; nothing to do", tracker.name)
        return 0

    for target in targets:
        os.makedirs(os.path.join(output_dir, target.path), exist_ok=True)

    paths_by_format: dict[str, list[str]] = {}
    for target in targets:
        paths_by_format.setdefault(target.format, []).append(target.path)

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    logger.info("[%s] Fetching activities from %s to %s", tracker.name, start_date, end_date)
    activities = tracker.list_activities(start_date, end_date)
    logger.info("[%s] Found %d activities in date range", tracker.name, len(activities))

    downloaded = 0
    skipped = 0

    for activity in activities:
        if not _is_safe_activity_id(activity.id):
            raise UnsafeActivityIdError(
                f"Tracker {tracker.name} returned a non-alphanumeric activity id {activity.id!r}; "
                f"refusing to build an output path from it"
            )

        for fmt, folders in paths_by_format.items():
            filename = f"{tracker.name}-{activity.id}.{FORMAT_EXTENSIONS[fmt]}"
            missing = [
                os.path.join(output_dir, folder, filename)
                for folder in folders
                if not os.path.exists(os.path.join(output_dir, folder, filename))
            ]

            skipped += len(folders) - len(missing)
            if not missing:
                continue

            logger.info(
                "[%s] Downloading activity %s (%s -> %s): %s",
                tracker.name,
                activity.id,
                fmt,
                ", ".join(os.path.dirname(path) for path in missing),
                activity.name,
            )
            try:
                data = tracker.download(activity, fmt)
            except ActivityDownloadError as e:
                logger.error("[%s] Skipping %s (%s): %s", tracker.name, activity.id, fmt, e)
                if download_delay > 0:
                    time.sleep(download_delay)
                continue

            for filepath in missing:
                with open(filepath, "wb") as f:
                    f.write(data)
                downloaded += 1

            if download_delay > 0:
                time.sleep(download_delay)

    logger.info(
        "[%s] Download complete: %d new, %d skipped (already existed)",
        tracker.name,
        downloaded,
        skipped,
    )
    return downloaded
