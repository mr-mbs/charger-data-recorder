"""Constants for Charger Data Recorder."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "charger_data_recorder"

CONF_CHARGE_POINT_ID = "charge_point_id"
CONF_IDENTIFICATION = "identification"
CONF_VEHICLE_SOURCE = "vehicle_source"
CONF_ODOMETER_SOURCE = "odometer_source"
CONF_METER_SOURCE = "meter_source"
CONF_CABLE_SENSOR = "cable_sensor"
CONF_CABLE_ACTIVE_STATE = "cable_active_state"
CONF_MODE_SENSOR = "mode_sensor"
CONF_MODE_ACTIVE_STATE = "mode_active_state"
CONF_MODE_FINISHED_STATE = "mode_finished_state"
CONF_CSV_FILENAME = "csv_filename"

DEFAULT_CSV_FILENAME = "charger_sessions.csv"

CSV_COLUMNS = (
    "Created / Start Time",
    "Finished / End Time",
    "Charge Point ID / EVSE ID",
    "Identification / ID Tag",
    "Vehicle",
    "Odometer (km)",
    "Meter Start (kWh)",
    "Meter End (kWh)",
    "Energy (kWh)",
    "Duration",
)
