"""Configuration loading from environment variables and Docker secrets."""

import logging
import os
import re
from dataclasses import dataclass

from src.trackers import TRACKER_CLASSES

logger = logging.getLogger(__name__)

VALID_DOWNLOAD_FORMATS = {"FIT", "GPX", "TCX"}

# Characters that would let a folder name escape the output directory or confuse
# the filesystem. `/` is legal in a destination (it may be several levels deep),
# so it is checked per path component instead.
_ILLEGAL_FOLDER_CHARS = ("\\", "\0")

# Placeholders a destination template may use. Both are known at load time --
# the format from the entry, the tracker from the variable the entry came from --
# so a destination is a plain path by the time the downloader sees it.
_PLACEHOLDERS = ("format", "tracker")
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")

# Separators of the DOWNLOAD_TARGETS grammar: `folder=FMT+FMT, folder=FMT`.
_ENTRY_SEPARATOR = ","
_FOLDER_SEPARATOR = "="
_FORMAT_SEPARATOR = "+"

_TARGETS_VAR = "DOWNLOAD_TARGETS"
_TARGETS_SUFFIX = f"_{_TARGETS_VAR}"
_LEGACY_VAR = "DOWNLOAD_FORMATS"

# Dedup markers live beside the activity files by default, so the existing data
# volume carries them with no extra mount. It is hidden so that a consumer
# listing `output_dir` does not mistake it for a format folder.
STATE_SUBDIR = ".state"


def default_state_dir(output_dir: str) -> str:
    """Marker directory used when `STATE_DIR` is unset."""
    return os.path.join(output_dir, STATE_SUBDIR)


@dataclass(frozen=True)
class DownloadTarget:
    """A single download destination: a format, and the folder that receives it.

    `folder` is a concrete path relative to `output_dir`, with any template
    placeholders already resolved. It defaults to the format name, which is the
    layout a bare `FIT` entry produces.
    """

    format: str
    folder: str | None = None

    def __post_init__(self) -> None:
        # Canonical form, so `DownloadTarget("FIT")` and `DownloadTarget("FIT",
        # "FIT")` compare equal and deduplicate against each other.
        if self.folder is None:
            object.__setattr__(self, "folder", self.format)

    @property
    def path(self) -> str:
        """Destination path relative to `output_dir`."""
        return self.folder


@dataclass
class Config:
    """Application configuration.

    Credentials are absent on purpose: each tracker reads its own in
    `Tracker.from_env`, so adding a tracker does not change this dataclass.
    """

    trackers: list[str]
    tokens_dir: str
    output_dir: str
    state_dir: str
    days_back: int
    download_targets: dict[str, list[DownloadTarget]]


def _validate_folder(folder: str, entry: str) -> str:
    """Validate a rendered destination as a safe relative path under `output_dir`."""
    if not folder:
        raise ValueError(f"Invalid {_TARGETS_VAR} entry {entry!r}: the destination folder must not be empty")
    if any(char in folder for char in _ILLEGAL_FOLDER_CHARS):
        raise ValueError(
            f"Invalid {_TARGETS_VAR} entry {entry!r}: "
            f"the destination folder must not contain a backslash or a null byte"
        )
    if os.path.isabs(folder):
        raise ValueError(f"Invalid {_TARGETS_VAR} entry {entry!r}: the destination folder must be a relative path")

    for component in folder.split("/"):
        if not component:
            raise ValueError(f"Invalid {_TARGETS_VAR} entry {entry!r}: the destination folder has an empty folder name")
        if component in (".", ".."):
            raise ValueError(
                f"Invalid {_TARGETS_VAR} entry {entry!r}: the destination folder must not contain {component!r}"
            )
    return folder


def _render_folder(template: str, entry: str, tracker: str, fmt: str) -> str:
    """Substitute `{format}` and `{tracker}` in a destination template.

    Done by hand rather than with `str.format` so that an unknown placeholder is
    a configuration error with a useful message, instead of a `KeyError` or a
    literal `{ingesting_app}` directory.
    """
    for name in _PLACEHOLDER_RE.findall(template):
        if name not in _PLACEHOLDERS:
            raise ValueError(
                f"Invalid {_TARGETS_VAR} entry {entry!r}: unknown placeholder {{{name}}}. "
                f"Valid placeholders: {', '.join('{%s}' % p for p in _PLACEHOLDERS)}"
            )

    rendered = template.replace("{format}", fmt).replace("{tracker}", tracker)
    if "{" in rendered or "}" in rendered:
        raise ValueError(f"Invalid {_TARGETS_VAR} entry {entry!r}: unbalanced {{ or }} in the destination folder")
    return rendered


def _parse_formats(raw: str, entry: str) -> list[str]:
    """Parse the `FMT+FMT` side of an entry, keeping first-occurrence order."""
    formats: list[str] = []
    for token in raw.split(_FORMAT_SEPARATOR):
        fmt = token.strip().upper()
        if fmt not in VALID_DOWNLOAD_FORMATS:
            raise ValueError(
                f"Invalid {_TARGETS_VAR} format {fmt!r} in entry {entry!r}. "
                f"Valid options: {sorted(VALID_DOWNLOAD_FORMATS)}"
            )
        if fmt not in formats:
            formats.append(fmt)
    return formats


def _parse_targets(raw: str, tracker: str, variable: str = _TARGETS_VAR) -> list[DownloadTarget]:
    """Parse and validate a DOWNLOAD_TARGETS value for one tracker.

    Each entry is either a bare format (`FIT`, saved to `FIT/`) or a
    `folder=FORMAT[+FORMAT]` pair naming a destination that receives those
    formats. The folder may be several levels deep and may use the `{format}`
    and `{tracker}` placeholders. Repeated format/folder pairs collapse into one
    target, so a destination is only ever filled once.
    """
    entries = [entry.strip() for entry in raw.split(_ENTRY_SEPARATOR) if entry.strip()]
    if not entries:
        raise ValueError(f"{variable} must not be empty")

    targets: list[DownloadTarget] = []
    for entry in entries:
        left, separator, right = entry.partition(_FOLDER_SEPARATOR)
        # A bare format is shorthand for `{format}=FORMAT`, which is the layout
        # every configuration used before destinations existed.
        template = left.strip() if separator else "{format}"
        formats = _parse_formats(right if separator else left, entry)

        for fmt in formats:
            folder = _validate_folder(_render_folder(template, entry, tracker, fmt), entry)
            target = DownloadTarget(format=fmt, folder=folder)
            if target not in targets:
                targets.append(target)

    return targets


def _parse_legacy_formats(raw: str) -> list[DownloadTarget]:
    """Parse a deprecated DOWNLOAD_FORMATS value into targets.

    The old grammar nests a subfolder under its format (`FIT:archive` ->
    `FIT/archive`), which is the opposite of a destination that may hold several
    formats. The two are never mixed, so this stays a separate parser rather
    than a special case in the new one.
    """
    entries = [entry.strip() for entry in raw.split(_ENTRY_SEPARATOR) if entry.strip()]
    if not entries:
        raise ValueError(f"{_LEGACY_VAR} must not be empty")

    targets: list[DownloadTarget] = []
    for entry in entries:
        fmt, separator, raw_folder = entry.partition(":")
        fmt = fmt.strip().upper()
        if fmt not in VALID_DOWNLOAD_FORMATS:
            raise ValueError(
                f"Invalid {_LEGACY_VAR} format {fmt!r} in entry {entry!r}. "
                f"Valid options: {sorted(VALID_DOWNLOAD_FORMATS)}"
            )

        folder = fmt
        if separator:
            subfolder = raw_folder.strip()
            if not subfolder:
                raise ValueError(f"Invalid {_LEGACY_VAR} entry {entry!r}: subfolder name must not be empty")
            if "/" in subfolder or "\\" in subfolder or "\0" in subfolder:
                raise ValueError(
                    f"Invalid {_LEGACY_VAR} entry {entry!r}: "
                    f"subfolder name must be a single folder, without path separators"
                )
            if subfolder in (".", ".."):
                raise ValueError(f"Invalid {_LEGACY_VAR} entry {entry!r}: subfolder name must not be {subfolder!r}")
            folder = f"{fmt}/{subfolder}"

        target = DownloadTarget(format=fmt, folder=folder)
        if target not in targets:
            targets.append(target)

    return targets


def _parse_trackers(raw: str) -> list[str]:
    """Parse and validate a comma-separated TRACKERS value.

    Each entry names a tracker in the registry. Names are case-insensitive and a
    repeated name collapses into one, keeping first-occurrence order.
    """
    entries = [entry.strip().lower() for entry in raw.split(_ENTRY_SEPARATOR) if entry.strip()]
    if not entries:
        raise ValueError("TRACKERS must not be empty")

    trackers: list[str] = []
    for entry in entries:
        if entry not in TRACKER_CLASSES:
            raise ValueError(f"Invalid TRACKERS entry {entry!r}. Valid options: {sorted(TRACKER_CLASSES)}")
        if entry not in trackers:
            trackers.append(entry)

    return trackers


def _tracker_target_vars() -> dict[str, tuple[str, str]]:
    """Collect the `<TRACKER>_DOWNLOAD_TARGETS` variables, keyed by tracker name.

    An unknown prefix is a typo (`GARMN_DOWNLOAD_TARGETS`), and silently doing
    nothing is the failure mode this variable is most prone to, so it raises.
    """
    found: dict[str, tuple[str, str]] = {}
    for key, value in os.environ.items():
        if not key.endswith(_TARGETS_SUFFIX) or not value.strip():
            continue

        name = key[: -len(_TARGETS_SUFFIX)].lower().replace("_", "-")
        if name not in TRACKER_CLASSES:
            raise ValueError(
                f"Unknown tracker {name!r} in environment variable {key!r}. Valid options: {sorted(TRACKER_CLASSES)}"
            )
        found[name] = (key, value)
    return found


def _assert_within(output_dir: str, targets: dict[str, list[DownloadTarget]]) -> None:
    """Last check that no destination resolves outside `output_dir`."""
    root = os.path.realpath(output_dir)
    for tracker_targets in targets.values():
        for target in tracker_targets:
            resolved = os.path.realpath(os.path.join(output_dir, target.path))
            if resolved != root and not resolved.startswith(root + os.sep):
                raise ValueError(f"Destination folder {target.path!r} resolves outside the output directory")


def _load_download_targets(trackers: list[str], output_dir: str) -> dict[str, list[DownloadTarget]]:
    """Build each tracker's target list from the environment.

    `DOWNLOAD_TARGETS` is the default for every tracker, and
    `<TRACKER>_DOWNLOAD_TARGETS` replaces it for one tracker. A format a tracker
    cannot supply is an error when that tracker asked for it by name, and a
    warning from the downloader when it only inherited the shared default.
    """
    legacy = os.environ.get(_LEGACY_VAR, "").strip()
    shared = os.environ.get(_TARGETS_VAR, "").strip()
    per_tracker = _tracker_target_vars()

    if legacy and (shared or per_tracker):
        raise ValueError(
            f"{_LEGACY_VAR} and {_TARGETS_VAR} must not both be set. "
            f"{_LEGACY_VAR} is deprecated: an entry is now `folder=FORMAT` instead of `FORMAT:subfolder`"
        )

    if legacy:
        logger.warning(
            "%s is deprecated and will be removed. Use %s, where an entry is `folder=FORMAT[+FORMAT]`",
            _LEGACY_VAR,
            _TARGETS_VAR,
        )
        targets = _parse_legacy_formats(legacy)
        return {name: list(targets) for name in trackers}

    for name, (key, _) in per_tracker.items():
        if name not in trackers:
            logger.warning("%s is set, but %r is not in TRACKERS; ignoring it", key, name)

    resolved: dict[str, list[DownloadTarget]] = {}
    for name in trackers:
        variable, raw = per_tracker.get(name, (_TARGETS_VAR, shared or "FIT"))
        targets = _parse_targets(raw, tracker=name, variable=variable)

        if name in per_tracker:
            # Asked for by name, so an impossible pair is a mistake worth
            # stopping for. An inherited one stays the downloader's warning.
            supported = TRACKER_CLASSES[name].supported_formats
            unsupported = sorted({t.format for t in targets} - set(supported))
            if unsupported:
                raise ValueError(
                    f"{variable} asks for {', '.join(unsupported)}, which {name} does not provide. "
                    f"It provides {', '.join(sorted(supported))}"
                )

        resolved[name] = targets

    return resolved


def load_config() -> Config:
    """Load configuration from environment variables and Docker secrets."""
    output_dir = os.environ.get("OUTPUT_DIR", "/app/data")
    trackers = _parse_trackers(os.environ.get("TRACKERS", "garmin"))
    download_targets = _load_download_targets(trackers, output_dir)
    _assert_within(output_dir, download_targets)

    return Config(
        trackers=trackers,
        tokens_dir=os.environ.get("TOKENS_DIR", "/app/tokens"),
        output_dir=output_dir,
        state_dir=os.environ.get("STATE_DIR") or default_state_dir(output_dir),
        days_back=int(os.environ.get("DAYS_BACK", "7")),
        download_targets=download_targets,
    )
