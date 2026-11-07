"""Sensors for Charger Data Recorder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .csv_logger import CdrLogger
    from .data import ChargerDataRecorderConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: ChargerDataRecorderConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the CDR activity sensor."""
    async_add_entities(
        [
            CdrStateSensor(
                logger=entry.runtime_data.logger,
                entry_id=entry.entry_id,
            ),
            CdrActivitySensor(
                logger=entry.runtime_data.logger,
                entry_id=entry.entry_id,
            ),
        ]
    )


class CdrStateSensor(SensorEntity):
    """Expose the current logical state of the CDR workflow."""

    def __init__(self, logger: CdrLogger, entry_id: str) -> None:
        """Initialize the workflow state sensor."""
        self._logger = logger
        self._remove_listener: Callable[[], None] | None = None
        self._attr_has_entity_name = True
        self._attr_translation_key = "cdr_state"
        self._attr_icon = "mdi:state-machine"
        self._attr_unique_id = f"{entry_id}_cdr_state"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Charger Data Recorder",
        )

    @property
    def native_value(self) -> str:
        """Return the current workflow state."""
        return self._logger.cdr_state

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Expose current session and charging flags."""
        return {
            "session_active": self._logger.session_active,
            "active_charging": self._logger.active_charging,
        }

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
        """Refresh entity state after the workflow changes."""
        self.async_write_ha_state()


class CdrActivitySensor(SensorEntity):
    """Expose the time and type of the latest CDR activity."""

    def __init__(
        self,
        logger: CdrLogger,
        entry_id: str,
    ) -> None:
        """Initialize the activity sensor."""
        self._logger = logger
        self._remove_listener: Callable[[], None] | None = None
        self._attr_has_entity_name = True
        self._attr_translation_key = "last_cdr_activity"
        self._attr_icon = "mdi:ev-station"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_unique_id = f"{entry_id}_last_cdr_activity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Charger Data Recorder",
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp of the latest created or updated CDR."""
        return self._logger.last_updated

    @property
    def extra_state_attributes(self) -> dict[str, str | bool]:
        """Return the action and current output file."""
        cable_entity = self._logger.config.get("cable_sensor", "")
        mode_entity = self._logger.config["mode_sensor"]
        cable = self._logger.hass.states.get(cable_entity) if cable_entity else None
        mode = self._logger.hass.states.get(mode_entity)
        return {
            "last_action": self._logger.last_action or "none",
            "session_active": self._logger.session_active,
            "csv_file": str(self._logger.csv_path),
            "cable_entity": cable_entity,
            "cable_state": cable.state if cable is not None else "not found",
            "cable_active_state": self._logger.config.get(
                "cable_active_state",
                "not configured",
            ),
            "mode_entity": mode_entity,
            "mode_state": mode.state if mode is not None else "not found",
            "mode_active_state": self._logger.config["mode_active_state"],
        }

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
        """Refresh entity state after a CDR is written."""
        self.async_write_ha_state()
