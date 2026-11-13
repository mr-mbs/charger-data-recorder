"""Path helpers for monthly charger session CSV files."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


_WINDOWS_DRIVE_PATTERN = re.compile(r"^[a-zA-Z]:")
_RELATIVE_PATH_ERROR = "CSV path must be relative and end in .csv"
_CONFIG_DIRECTORY_ERROR = "CSV path must remain inside the HA config directory"


class CsvPathError(ValueError):
    """Raised when a configured CSV path is invalid."""

    def __init__(self, problem: str) -> None:
        """Store the path validation problem."""
        self.problem = problem

    def __str__(self) -> str:
        """Describe the path validation problem."""
        return self.problem


def normalize_csv_path(value: str) -> str:
    """Normalize a relative CSV path and reject paths outside the HA config dir."""
    normalized = value.strip().replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or not normalized.lower().endswith(".csv")
        or candidate.is_absolute()
        or _WINDOWS_DRIVE_PATTERN.match(normalized)
        or ".." in candidate.parts
        or "\x00" in normalized
    ):
        raise CsvPathError(_RELATIVE_PATH_ERROR)
    return candidate.as_posix()


def resolve_csv_path(config_dir: str | Path, relative_path: str) -> Path:
    """Resolve a normalized CSV path inside the Home Assistant config directory."""
    root = Path(config_dir).resolve()
    target = (root / Path(*PurePosixPath(relative_path).parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as err:
        raise CsvPathError(_CONFIG_DIRECTORY_ERROR) from err
    return target


def monthly_csv_path(base_path: Path, at: datetime) -> Path:
    """Add the local calendar year and month before the CSV extension."""
    return base_path.with_name(f"{base_path.stem}_{at:%Y%m}{base_path.suffix}")
