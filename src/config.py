"""Configuration loading from environment variables and Docker secrets."""

import os
from dataclasses import dataclass

from src.trackers import TRACKER_CLASSES

VALID_DOWNLOAD_FORMATS = {"FIT", "GPX", "TCX"}

# Characters that would let a subfolder name escape its format folder or confuse the filesystem.
_ILLEGAL_FOLDER_CHARS = ("/", "\\", "\0")


@dataclass(frozen=True)
class DownloadTarget:
    """A single download destination: a format, optionally in a subfolder of it."""

    format: str
    subfolder: str | None = None

    @property
    def path(self) -> str:
        """Destination path relative to `output_dir`: `FORMAT` or `FORMAT/subfolder`."""
        return self.format if self.subfolder is None else os.path.join(self.format, self.subfolder)


@dataclass
class Config:
    """Application configuration.

    Credentials are absent on purpose: each tracker reads its own in
    `Tracker.from_env`, so adding a tracker does not change this dataclass.
    """

    trackers: list[str]
    tokens_dir: str
    output_dir: str
    days_back: int
    download_targets: list[DownloadTarget]


def _validate_subfolder(folder: str, entry: str) -> str:
    """Validate a custom subfolder name as a single safe path component."""
    if not folder:
        raise ValueError(f"Invalid DOWNLOAD_FORMATS entry {entry!r}: subfolder name must not be empty")
    if any(char in folder for char in _ILLEGAL_FOLDER_CHARS):
        raise ValueError(
            f"Invalid DOWNLOAD_FORMATS entry {entry!r}: subfolder name must be a single folder, without path separators"
        )
    if folder in (".", ".."):
        raise ValueError(f"Invalid DOWNLOAD_FORMATS entry {entry!r}: subfolder name must not be {folder!r}")
    return folder


def _parse_targets(raw: str) -> list[DownloadTarget]:
    """Parse and validate a comma-separated DOWNLOAD_FORMATS value.

    Each entry is either a bare format (`GPX`, saved to `GPX/`) or a
    `FORMAT:subfolder` pair (`FIT:folderA`, saved to `FIT/folderA/`).
    A format may appear more than once to target several folders; repeats of the
    same format and subfolder collapse into one target.
    """
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if not entries:
        raise ValueError("DOWNLOAD_FORMATS must not be empty")

    targets: list[DownloadTarget] = []
    for entry in entries:
        fmt, separator, raw_folder = entry.partition(":")
        fmt = fmt.strip().upper()
        if fmt not in VALID_DOWNLOAD_FORMATS:
            raise ValueError(
                f"Invalid DOWNLOAD_FORMATS format {fmt!r} in entry {entry!r}. "
                f"Valid options: {sorted(VALID_DOWNLOAD_FORMATS)}"
            )
        subfolder = _validate_subfolder(raw_folder.strip(), entry) if separator else None

        target = DownloadTarget(format=fmt, subfolder=subfolder)
        if target not in targets:
            targets.append(target)

    return targets


def _parse_trackers(raw: str) -> list[str]:
    """Parse and validate a comma-separated TRACKERS value.

    Each entry names a tracker in the registry. Names are case-insensitive and a
    repeated name collapses into one, keeping first-occurrence order.
    """
    entries = [entry.strip().lower() for entry in raw.split(",") if entry.strip()]
    if not entries:
        raise ValueError("TRACKERS must not be empty")

    trackers: list[str] = []
    for entry in entries:
        if entry not in TRACKER_CLASSES:
            raise ValueError(f"Invalid TRACKERS entry {entry!r}. Valid options: {sorted(TRACKER_CLASSES)}")
        if entry not in trackers:
            trackers.append(entry)

    return trackers


def load_config() -> Config:
    """Load configuration from environment variables and Docker secrets."""
    return Config(
        trackers=_parse_trackers(os.environ.get("TRACKERS", "garmin")),
        tokens_dir=os.environ.get("TOKENS_DIR", "/app/tokens"),
        output_dir=os.environ.get("OUTPUT_DIR", "/app/data"),
        days_back=int(os.environ.get("DAYS_BACK", "7")),
        download_targets=_parse_targets(os.environ.get("DOWNLOAD_FORMATS", "FIT")),
    )
