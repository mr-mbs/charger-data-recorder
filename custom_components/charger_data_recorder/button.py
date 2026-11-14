"""Buttons for Charger Data Recorder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .csv_logger import CdrLogger
    from .data import ChargerDataRecorderConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: ChargerDataRecorderConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the manual data refresh button."""
    async_add_entities(
        [
            CdrRefreshButton(
                logger=entry.runtime_data.logger,
                entry_id=entry.entry_id,
            )
        ]
    )


class CdrRefreshButton(ButtonEntity):
    """Refresh configured source entities and evaluate the current workflow."""

    def __init__(self, logger: CdrLogger, entry_id: str) -> None:
        """Initialize the refresh button."""
        self._logger = logger
        self._attr_has_entity_name = True
        self._attr_translation_key = "refresh_data"
        self._attr_unique_id = f"{entry_id}_refresh_data"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Charger Data Recorder",
        )

    async def async_press(self) -> None:
        """Refresh configured entities and reconcile their current state."""
        await self._logger.async_refresh()
