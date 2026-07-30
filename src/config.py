"""Configuration loading from environment variables and Docker secrets."""

import os
from dataclasses import dataclass

VALID_DOWNLOAD_FORMATS = {"FIT", "GPX", "TCX"}

# Characters that would let a folder name escape output_dir or confuse the filesystem.
_ILLEGAL_FOLDER_CHARS = ("/", "\\", "\0")


@dataclass(frozen=True)
class DownloadTarget:
    """A single format-to-folder download destination."""

    format: str
    folder: str


@dataclass
class Config:
    """Application configuration."""

    email: str | None
    password: str | None
    tokenstore: str
    output_dir: str
    days_back: int
    download_targets: list[DownloadTarget]


def _read_secret(name: str) -> str | None:
    """Read a value from Docker secret file, falling back to env var."""
    secret_path = f"/run/secrets/{name.lower()}"
    try:
        with open(secret_path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return os.environ.get(name)


def _validate_folder(folder: str, entry: str) -> str:
    """Validate a custom folder name as a single safe path component."""
    if not folder:
        raise ValueError(f"Invalid DOWNLOAD_FORMATS entry {entry!r}: folder name must not be empty")
    if any(char in folder for char in _ILLEGAL_FOLDER_CHARS):
        raise ValueError(
            f"Invalid DOWNLOAD_FORMATS entry {entry!r}: folder name must be a single folder, without path separators"
        )
    if folder in (".", ".."):
        raise ValueError(f"Invalid DOWNLOAD_FORMATS entry {entry!r}: folder name must not be {folder!r}")
    return folder


def _parse_targets(raw: str) -> list[DownloadTarget]:
    """Parse and validate a comma-separated DOWNLOAD_FORMATS value.

    Each entry is either a bare format (`GPX`, saved to a `GPX` folder) or a
    `FORMAT:folder` pair (`FIT:user@example.com`). A format may appear more than
    once as long as each occurrence targets a different folder.
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
        folder = _validate_folder(raw_folder.strip(), entry) if separator else fmt

        target = DownloadTarget(format=fmt, folder=folder)
        if target in targets:
            raise ValueError(f"Duplicate DOWNLOAD_FORMATS entry: {fmt} is already downloaded to folder {folder!r}")
        targets.append(target)

    return targets


def load_config() -> Config:
    """Load configuration from environment variables and Docker secrets."""
    return Config(
        email=_read_secret("GARMIN_EMAIL"),
        password=_read_secret("GARMIN_PASSWORD"),
        tokenstore=os.environ.get("GARMINTOKENS", "/app/tokens"),
        output_dir=os.environ.get("OUTPUT_DIR", "/app/data"),
        days_back=int(os.environ.get("DAYS_BACK", "7")),
        download_targets=_parse_targets(os.environ.get("DOWNLOAD_FORMATS", "FIT")),
    )
