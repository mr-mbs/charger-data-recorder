"""Runtime data for Charger Data Recorder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.loader import Integration

    from .csv_logger import CdrLogger

type ChargerDataRecorderConfigEntry = ConfigEntry[ChargerDataRecorderData]


@dataclass
class ChargerDataRecorderData:
    """Runtime data for the configured charger."""

    logger: CdrLogger
    integration: Integration
