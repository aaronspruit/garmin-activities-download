"""Activity fetching and multi-format file download with deduplication."""

import io
import logging
import os
import time
import zipfile
from datetime import datetime, timedelta

from garminconnect import Garmin

from src.config import DownloadTarget

logger = logging.getLogger(__name__)

FORMAT_SPECS = {
    "FIT": {
        "dl_fmt": Garmin.ActivityDownloadFormat.ORIGINAL,
        "extension": "fit",
        "zipped": True,
    },
    "GPX": {
        "dl_fmt": Garmin.ActivityDownloadFormat.GPX,
        "extension": "gpx",
        "zipped": False,
    },
    "TCX": {
        "dl_fmt": Garmin.ActivityDownloadFormat.TCX,
        "extension": "tcx",
        "zipped": False,
    },
}


def _normalize_targets(targets: list[DownloadTarget | str] | None) -> list[DownloadTarget]:
    """Accept targets as `DownloadTarget`s or bare format tokens (folder = format)."""
    if not targets:
        return [DownloadTarget(format="FIT", folder="FIT")]
    return [DownloadTarget(format=t, folder=t) if isinstance(t, str) else t for t in targets]


def _extract_fit_bytes(zip_bytes: bytes) -> bytes:
    """Extract the first .fit member's bytes from an ORIGINAL-format zip archive."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        fit_names = [name for name in zf.namelist() if name.lower().endswith(".fit")]
        if not fit_names:
            raise ValueError("ORIGINAL download zip contains no .fit file")
        return zf.read(fit_names[0])


def download_new_activities(
    garmin: Garmin,
    output_dir: str,
    targets: list[DownloadTarget | str] | None = None,
    days_back: int = 7,
    download_delay: float = 1.0,
) -> int:
    """Download activity files (one or more format/folder targets) not already saved.

    A format requested by several targets is fetched from Garmin once per activity
    and written to each folder that is still missing it.

    Args:
        garmin: Authenticated Garmin client.
        output_dir: Directory under which the target subfolders are created.
        targets: `DownloadTarget`s, or bare format tokens ("FIT", "GPX", "TCX") whose
            folder matches the format. Defaults to FIT into a "FIT" folder.
        days_back: Number of days to look back for activities.
        download_delay: Seconds to wait between downloads (rate limit protection).

    Returns:
        Number of new activity files downloaded.
    """
    targets = _normalize_targets(targets)

    for target in targets:
        os.makedirs(os.path.join(output_dir, target.folder), exist_ok=True)

    folders_by_format: dict[str, list[str]] = {}
    for target in targets:
        folders_by_format.setdefault(target.format, []).append(target.folder)

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    logger.info("Fetching activities from %s to %s", start_date, end_date)
    activities = garmin.get_activities_by_date(start_date, end_date)
    logger.info("Found %d activities in date range", len(activities))

    downloaded = 0
    skipped = 0

    for activity in activities:
        activity_id = activity["activityId"]
        activity_name = activity.get("activityName", "Unknown")

        for fmt, folders in folders_by_format.items():
            spec = FORMAT_SPECS[fmt]
            filename = f"{activity_id}.{spec['extension']}"
            missing = [
                os.path.join(output_dir, folder, filename)
                for folder in folders
                if not os.path.exists(os.path.join(output_dir, folder, filename))
            ]

            skipped += len(folders) - len(missing)
            if not missing:
                continue

            logger.info(
                "Downloading activity %s (%s -> %s): %s",
                activity_id,
                fmt,
                ", ".join(os.path.dirname(path) for path in missing),
                activity_name,
            )
            data = garmin.download_activity(activity_id, dl_fmt=spec["dl_fmt"])

            if spec["zipped"]:
                try:
                    data = _extract_fit_bytes(data)
                except ValueError as e:
                    logger.error("Skipping %s (%s): %s", activity_id, fmt, e)
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
        "Download complete: %d new, %d skipped (already existed)",
        downloaded,
        skipped,
    )
    return downloaded
