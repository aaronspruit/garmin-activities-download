"""Configuration loading from environment variables and Docker secrets."""

import os
from dataclasses import dataclass

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
