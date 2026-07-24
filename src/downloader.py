"""Activity fetching and GPX file download with deduplication."""

import logging
import os
import time
from datetime import datetime, timedelta

from garminconnect import Garmin

logger = logging.getLogger(__name__)


def download_new_activities(
    garmin: Garmin,
    output_dir: str,
    days_back: int = 7,
    download_delay: float = 1.0,
) -> int:
    """Download GPX files for activities not already saved.

    Args:
        garmin: Authenticated Garmin client.
        output_dir: Directory to write GPX files.
        days_back: Number of days to look back for activities.
        download_delay: Seconds to wait between downloads (rate limit protection).

    Returns:
        Number of new activities downloaded.
    """
    os.makedirs(output_dir, exist_ok=True)

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
        filename = f"{activity_id}.gpx"
        filepath = os.path.join(output_dir, filename)

        if os.path.exists(filepath):
            skipped += 1
            continue

        logger.info("Downloading activity %s: %s", activity_id, activity_name)
        gpx_data = garmin.download_activity(
            activity_id,
            dl_fmt=Garmin.ActivityDownloadFormat.GPX,
        )

        with open(filepath, "wb") as f:
            f.write(gpx_data)

        downloaded += 1

        if download_delay > 0:
            time.sleep(download_delay)

    logger.info(
        "Download complete: %d new, %d skipped (already existed)",
        downloaded,
        skipped,
    )
    return downloaded
