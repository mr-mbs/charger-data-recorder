"""Config flow for Charger Data Recorder."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CABLE_ACTIVE_STATE,
    CONF_CABLE_SENSOR,
    CONF_CHARGE_POINT_ID,
    CONF_CSV_FILENAME,
    CONF_IDENTIFICATION,
    CONF_METER_SOURCE,
    CONF_MODE_ACTIVE_STATE,
    CONF_MODE_FINISHED_STATE,
    CONF_MODE_SENSOR,
    CONF_ODOMETER_SOURCE,
    CONF_VEHICLE_SOURCE,
    DEFAULT_CSV_FILENAME,
    DOMAIN,
    LOGGER,
)
from .csv_path import normalize_csv_path


class ChargerDataRecorderConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up a charger session CSV recorder."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,  # noqa: ARG004
    ) -> ChargerDataRecorderOptionsFlow:
        """Return the options flow for an existing integration entry."""
        return ChargerDataRecorderOptionsFlow()

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Configure the charger and session-active states."""
        errors: dict[str, str] = {}
        if user_input is not None:
            validation_error = _validate_config_input(user_input)
            if validation_error is not None:
                LOGGER.warning(
                    "Initial configuration rejected: %s",
                    validation_error,
                )
                errors["base"] = validation_error
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                LOGGER.info(
                    "Integration configured: CSV=%s, cable entity=%s, mode entity=%s",
                    user_input[CONF_CSV_FILENAME],
                    user_input.get(CONF_CABLE_SENSOR, ""),
                    user_input[CONF_MODE_SENSOR],
                )
                return self.async_create_entry(
                    title="Charger Data Recorder",
                    data=user_input,
                )

        LOGGER.debug("Showing initial Charger Data Recorder configuration form")
        return self.async_show_form(
            step_id="user",
            data_schema=_config_schema(user_input or {}),
            errors=errors,
        )


class ChargerDataRecorderOptionsFlow(config_entries.OptionsFlow):
    """Allow editing the charger and CSV settings after initial setup."""

    async def async_step_init(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Show and save the current configuration."""
        errors: dict[str, str] = {}
        if user_input is not None:
            validation_error = _validate_config_input(user_input)
            if validation_error is not None:
                LOGGER.warning("Options rejected: %s", validation_error)
                errors["base"] = validation_error
            else:
                LOGGER.info(
                    "Integration options saved: CSV=%s, cable entity=%s, "
                    "mode entity=%s",
                    user_input[CONF_CSV_FILENAME],
                    user_input.get(CONF_CABLE_SENSOR, ""),
                    user_input[CONF_MODE_SENSOR],
                )
                return self.async_create_entry(title="", data=user_input)

        current_config = {
            **self.config_entry.data,
            **self.config_entry.options,
        }
        LOGGER.debug(
            "Showing options form for config entry %s",
            self.config_entry.entry_id,
        )
        return self.async_show_form(
            step_id="init",
            data_schema=_config_schema(current_config),
            errors=errors,
        )


def _validate_config_input(user_input: dict[str, str]) -> str | None:
    """Normalize optional fields and validate configuration values."""
    try:
        csv_path = normalize_csv_path(user_input[CONF_CSV_FILENAME])
    except ValueError:
        return "invalid_filename"

    cable_entity = user_input.get(CONF_CABLE_SENSOR) or ""
    cable_active_state = (user_input.get(CONF_CABLE_ACTIVE_STATE) or "").strip()
    if cable_entity and not cable_active_state:
        return "cable_active_state_required"

    mode_active_state = user_input[CONF_MODE_ACTIVE_STATE].strip()
    mode_finished_state = (user_input.get(CONF_MODE_FINISHED_STATE) or "").strip()
    if mode_finished_state and mode_finished_state == mode_active_state:
        return "finished_state_must_differ"

    user_input[CONF_CSV_FILENAME] = csv_path
    user_input[CONF_CABLE_SENSOR] = cable_entity
    user_input[CONF_CABLE_ACTIVE_STATE] = cable_active_state if cable_entity else ""
    user_input[CONF_MODE_ACTIVE_STATE] = mode_active_state
    user_input[CONF_MODE_FINISHED_STATE] = mode_finished_state
    return None


def _config_schema(defaults: dict) -> vol.Schema:
    """Build the setup/options form with values from an existing entry."""
    cable_sensor_field = (
        vol.Optional(
            CONF_CABLE_SENSOR,
            default=defaults[CONF_CABLE_SENSOR],
        )
        if defaults.get(CONF_CABLE_SENSOR)
        else vol.Optional(CONF_CABLE_SENSOR)
    )

    return vol.Schema(
        {
            vol.Required(
                CONF_CHARGE_POINT_ID,
                default=defaults.get(CONF_CHARGE_POINT_ID, vol.UNDEFINED),
            ): selector.TextSelector(),
            vol.Required(
                CONF_IDENTIFICATION,
                default=defaults.get(CONF_IDENTIFICATION, vol.UNDEFINED),
            ): selector.TextSelector(),
            vol.Required(
                CONF_VEHICLE_SOURCE,
                default=defaults.get(CONF_VEHICLE_SOURCE, vol.UNDEFINED),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_ODOMETER_SOURCE,
                default=defaults.get(CONF_ODOMETER_SOURCE, ""),
            ): selector.TextSelector(),
            vol.Required(
                CONF_METER_SOURCE,
                default=defaults.get(CONF_METER_SOURCE, vol.UNDEFINED),
            ): selector.EntitySelector(),
            cable_sensor_field: selector.EntitySelector(),
            vol.Optional(
                CONF_CABLE_ACTIVE_STATE,
                default=defaults.get(CONF_CABLE_ACTIVE_STATE, ""),
            ): selector.TextSelector(),
            vol.Required(
                CONF_MODE_SENSOR,
                default=defaults.get(CONF_MODE_SENSOR, vol.UNDEFINED),
            ): selector.EntitySelector(),
            vol.Required(
                CONF_MODE_ACTIVE_STATE,
                default=defaults.get(CONF_MODE_ACTIVE_STATE, vol.UNDEFINED),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_MODE_FINISHED_STATE,
                default=defaults.get(CONF_MODE_FINISHED_STATE, ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_CSV_FILENAME,
                default=defaults.get(CONF_CSV_FILENAME, DEFAULT_CSV_FILENAME),
            ): selector.TextSelector(),
        },
    )
