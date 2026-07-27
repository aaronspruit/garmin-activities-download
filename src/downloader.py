"""Activity fetching and multi-format file download with deduplication."""

import io
import logging
import os
import time
import zipfile
from datetime import datetime, timedelta

from garminconnect import Garmin

logger = logging.getLogger(__name__)

FORMAT_SPECS = {
    "FIT": {
        "dl_fmt": Garmin.ActivityDownloadFormat.ORIGINAL,
        "folder": "FIT",
        "extension": "fit",
        "zipped": True,
    },
    "GPX": {
        "dl_fmt": Garmin.ActivityDownloadFormat.GPX,
        "folder": "GPX",
        "extension": "gpx",
        "zipped": False,
    },
    "TCX": {
        "dl_fmt": Garmin.ActivityDownloadFormat.TCX,
        "folder": "TCX",
        "extension": "tcx",
        "zipped": False,
    },
}


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
    formats: list[str] | None = None,
    days_back: int = 7,
    download_delay: float = 1.0,
) -> int:
    """Download activity files (one or more formats) not already saved.

    Args:
        garmin: Authenticated Garmin client.
        output_dir: Directory under which per-format subfolders are created.
        formats: List of format tokens ("FIT", "GPX", "TCX") to download. Defaults to ["FIT"].
        days_back: Number of days to look back for activities.
        download_delay: Seconds to wait between downloads (rate limit protection).

    Returns:
        Number of new activity files downloaded.
    """
    formats = formats or ["FIT"]
    specs = [FORMAT_SPECS[fmt] for fmt in formats]

    for spec in specs:
        os.makedirs(os.path.join(output_dir, spec["folder"]), exist_ok=True)

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

        for fmt, spec in zip(formats, specs):
            filepath = os.path.join(output_dir, spec["folder"], f"{activity_id}.{spec['extension']}")

            if os.path.exists(filepath):
                skipped += 1
                continue

            logger.info("Downloading activity %s (%s): %s", activity_id, fmt, activity_name)
            data = garmin.download_activity(activity_id, dl_fmt=spec["dl_fmt"])

            if spec["zipped"]:
                try:
                    data = _extract_fit_bytes(data)
                except ValueError as e:
                    logger.error("Skipping %s (%s): %s", activity_id, fmt, e)
                    if download_delay > 0:
                        time.sleep(download_delay)
                    continue

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
