"""Binary sensors for Charger Data Recorder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .csv_logger import CdrLogger
    from .data import ChargerDataRecorderConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: ChargerDataRecorderConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the CDR active charging binary sensor."""
    async_add_entities(
        [
            CdrActiveChargingBinarySensor(
                logger=entry.runtime_data.logger,
                entry_id=entry.entry_id,
            )
        ]
    )


class CdrActiveChargingBinarySensor(BinarySensorEntity):
    """Expose whether the charger is currently in its charging state."""

    def __init__(self, logger: CdrLogger, entry_id: str) -> None:
        """Initialize the active charging binary sensor."""
        self._logger = logger
        self._remove_listener: Callable[[], None] | None = None
        self._attr_has_entity_name = True
        self._attr_translation_key = "cdr_active_charging"
        self._attr_unique_id = f"{entry_id}_cdr_active_charging"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Charger Data Recorder",
        )

    @property
    def is_on(self) -> bool:
        """Return whether the charger is actively charging."""
        return self._logger.active_charging

    async def async_added_to_hass(self) -> None:
        """Subscribe to logger updates."""
        await super().async_added_to_hass()
        self._remove_listener = self._logger.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from logger updates."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_update(self) -> None:
        """Refresh entity state after the charging status changes."""
        self.async_write_ha_state()
