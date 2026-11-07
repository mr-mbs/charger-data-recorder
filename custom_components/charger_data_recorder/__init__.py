"""Home Assistant integration for recording charger sessions to CSV."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.loader import async_get_loaded_integration

from .const import (
    CONF_CABLE_ACTIVE_STATE,
    CONF_CABLE_SENSOR,
    CONF_CSV_FILENAME,
    CONF_MODE_ACTIVE_STATE,
    CONF_MODE_FINISHED_STATE,
    CONF_MODE_SENSOR,
    DOMAIN,
    LOGGER,
)
from .csv_logger import CdrLogger
from .csv_path import resolve_csv_path
from .data import ChargerDataRecorderData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import ChargerDataRecorderConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChargerDataRecorderConfigEntry,
) -> bool:
    """Set up the configured charger logger."""
    config = {**entry.data, **entry.options}
    csv_path = resolve_csv_path(
        hass.config.path(),
        config[CONF_CSV_FILENAME],
    )
    logger = CdrLogger(
        hass=hass,
        config=config,
        csv_path=str(csv_path),
    )
    LOGGER.info("Setting up Charger Data Recorder")
    try:
        await logger.async_load()
        await logger.async_start()
    except OSError, ValueError, RuntimeError:
        LOGGER.exception("Failed to initialize charger session CSV logger")
        raise

    entry.runtime_data = ChargerDataRecorderData(
        logger=logger,
        integration=async_get_loaded_integration(hass, DOMAIN),
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    LOGGER.info(
        "Charger Data Recorder is ready; CSV=%s; mode=%s (charging=%r, "
        "finished=%r); cable=%s (connected=%r)",
        logger.csv_path,
        config[CONF_MODE_SENSOR],
        config[CONF_MODE_ACTIVE_STATE],
        config.get(CONF_MODE_FINISHED_STATE, ""),
        config.get(CONF_CABLE_SENSOR, ""),
        config.get(CONF_CABLE_ACTIVE_STATE, ""),
    )
    return True


async def async_reload_entry(
    hass: HomeAssistant,
    entry: ChargerDataRecorderConfigEntry,
) -> None:
    """Reload the integration after its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ChargerDataRecorderConfigEntry,
) -> bool:
    """Unload the logger and its entities."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.logger.async_stop()
    return unloaded
