"""Track charging sessions and persist their CDRs to CSV."""

from __future__ import annotations

import asyncio
import csv
import glob
import os
import re
import tempfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CABLE_ACTIVE_STATE,
    CONF_CABLE_SENSOR,
    CONF_CHARGE_POINT_ID,
    CONF_IDENTIFICATION,
    CONF_METER_SOURCE,
    CONF_MODE_ACTIVE_STATE,
    CONF_MODE_FINISHED_STATE,
    CONF_MODE_SENSOR,
    CONF_ODOMETER_SOURCE,
    CONF_VEHICLE_SOURCE,
    CSV_COLUMNS,
    LOGGER,
)
from .csv_path import monthly_csv_path

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


_ENTITY_ID_PATTERN = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+\Z")
_UNAVAILABLE_STATES = {"unknown", "unavailable"}


class CdrDataError(ValueError):
    """Raised when an existing CDR file cannot be safely resumed."""

    def __init__(self, path: Path, problem: str) -> None:
        """Store the file and validation problem."""
        self.path = path
        self.problem = problem

    def __str__(self) -> str:
        """Describe the invalid CDR file."""
        return f"{self.path} {self.problem}"


class CdrSessionError(RuntimeError):
    """Raised when session state is internally inconsistent."""

    def __str__(self) -> str:
        """Describe the invalid session transition."""
        return "Cannot finish a session without an open CDR row"


class CdrLogger:
    """Create and complete CDR rows when the configured charger is active."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: Mapping[str, str],
        csv_path: str,
    ) -> None:
        """Initialize the session logger."""
        self.hass = hass
        self.config = config
        self.base_csv_path = Path(csv_path)
        self.csv_path = monthly_csv_path(
            self.base_csv_path,
            dt_util.now(),
        )
        self.last_updated: datetime | None = None
        self.last_action: str | None = None
        self.cdr_state = "Initialising"
        self.active_charging = False
        self._rows: list[dict[str, str]] = []
        self._active_row: int | None = None
        self._start_time: datetime | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load existing rows and recover a session left open by a restart."""
        LOGGER.debug("Loading CDR history from %s", self.csv_path)
        current_path = self.csv_path
        candidates = await self.hass.async_add_executor_job(self._monthly_files)
        if await self.hass.async_add_executor_job(self.base_csv_path.exists):
            candidates.append(self.base_csv_path)

        open_sessions: list[tuple[Path, list[dict[str, str]], int]] = []
        for path in candidates:
            rows = await self._async_read_rows(path)
            open_rows = [
                index
                for index, row in enumerate(rows)
                if not row["Finished / End Time"]
            ]
            open_sessions.extend((path, rows, index) for index in open_rows)

        if len(open_sessions) > 1:
            LOGGER.error(
                "Found %d unfinished CDR rows across monthly CDR files; "
                "expected at most one",
                len(open_sessions),
            )
            raise CdrDataError(current_path, "contains multiple unfinished CDR rows")
        if open_sessions:
            self.csv_path, self._rows, self._active_row = open_sessions[0]
            self._start_time = datetime.fromisoformat(
                self._rows[self._active_row]["Created / Start Time"]
            )
            LOGGER.info(
                "Recovered unfinished CDR row %d from %s (started %s)",
                self._active_row + 1,
                self.csv_path,
                self._start_time.isoformat(),
            )
        else:
            self.csv_path = current_path
            self._rows = await self._async_read_rows(self.csv_path)
            if not await self.hass.async_add_executor_job(self.csv_path.exists):
                LOGGER.info("Creating new CDR CSV file at %s", self.csv_path)
                await self._async_write_rows(self._rows)
            else:
                LOGGER.debug(
                    "Loaded %d CDR row(s) from %s",
                    len(self._rows),
                    self.csv_path,
                )

        if self._rows:
            latest = self._rows[-1]
            self.last_updated = datetime.fromisoformat(
                latest["Finished / End Time"] or latest["Created / Start Time"]
            )
            self.last_action = "restored"

    async def async_start(self) -> None:
        """Listen for state changes and reconcile the current charger state."""
        entity_ids = {self.config[CONF_MODE_SENSOR]}
        if self.config.get(CONF_CABLE_SENSOR):
            entity_ids.add(self.config[CONF_CABLE_SENSOR])
        self._unsubscribe = async_track_state_change_event(
            self.hass,
            entity_ids,
            self._async_handle_state_change,
        )
        LOGGER.debug(
            "Listening for charger trigger entities: %s",
            ", ".join(sorted(entity_ids)),
        )
        await self._async_reconcile()

    async def async_refresh(self) -> None:
        """Refresh configured entities and manually evaluate the CDR flow."""
        entity_ids = self._entity_ids_to_refresh()
        LOGGER.info("Manual refresh requested for entities: %s", entity_ids)
        if entity_ids:
            try:
                await self.hass.services.async_call(
                    "homeassistant",
                    "update_entity",
                    {"entity_id": entity_ids},
                    blocking=True,
                )
            except HomeAssistantError:
                LOGGER.exception(
                    "Home Assistant could not refresh configured CDR entities"
                )
                await self._async_reconcile()
                raise
        await self._async_reconcile()

    def _entity_ids_to_refresh(self) -> list[str]:
        """Return configured entity IDs used by the CDR fields and triggers."""
        sources = (
            self.config.get(CONF_CHARGE_POINT_ID, ""),
            self.config.get(CONF_IDENTIFICATION, ""),
            self.config.get(CONF_VEHICLE_SOURCE, ""),
            self.config.get(CONF_ODOMETER_SOURCE, ""),
            self.config.get(CONF_METER_SOURCE, ""),
            self.config.get(CONF_MODE_SENSOR, ""),
            self.config.get(CONF_CABLE_SENSOR, ""),
        )
        return sorted(
            {
                source
                for source in sources
                if source and _ENTITY_ID_PATTERN.fullmatch(source)
            }
        )

    def async_stop(self) -> None:
        """Stop listening for charger state changes."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
            LOGGER.debug("Stopped listening for charger trigger changes")

    @property
    def session_active(self) -> bool:
        """Return whether a CDR row is currently open."""
        return self._active_row is not None

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for CDR activity changes."""
        self._listeners.append(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.remove(listener)

        return remove_listener

    async def _async_handle_state_change(self, event: Event) -> None:
        """Reconcile the session after a cable or mode state change."""
        entity_id = event.data.get("entity_id", "unknown")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        LOGGER.debug(
            "Trigger changed: %s (%s -> %s)",
            entity_id,
            old_state.state if old_state is not None else "not set",
            new_state.state if new_state is not None else "not set",
        )
        await self._async_reconcile()

    async def _async_reconcile(self) -> None:
        """Evaluate mode, optional finished mode, and optional cable state."""
        async with self._lock:
            mode_state = self.hass.states.get(self.config[CONF_MODE_SENSOR])
            if mode_state is None or mode_state.state in _UNAVAILABLE_STATES:
                reason = "not found" if mode_state is None else f"{mode_state.state}"
                LOGGER.debug("Waiting for charger mode state: %s", reason)
                self._set_active_charging(active=False)
                self._set_cdr_state(self._waiting_for_mode_state(reason))
                return

            mode = mode_state.state
            mode_active = mode == self.config[CONF_MODE_ACTIVE_STATE]
            LOGGER.debug(
                "Evaluating charger mode: %s=%r (charging state=%r)",
                self.config[CONF_MODE_SENSOR],
                mode,
                self.config[CONF_MODE_ACTIVE_STATE],
            )

            if mode_active:
                self._set_active_charging(active=True)
                if self._active_row is None:
                    await self._async_begin_session(mode_state.last_changed)
                else:
                    self._set_cdr_state("Charging; CDR session is active")
                return

            self._set_active_charging(active=False)
            await self._async_handle_non_charging_mode(mode_state)

    async def _async_handle_non_charging_mode(self, mode_state: State) -> None:
        """Close a session or preserve it when charging is paused."""
        mode = mode_state.state
        finished_state = self.config.get(CONF_MODE_FINISHED_STATE, "").strip()
        if finished_state and mode == finished_state:
            LOGGER.debug("Finished mode %r detected", finished_state)
            if self._active_row is not None:
                await self._async_finish_session(mode_state.last_changed)
            else:
                self._set_cdr_state("Charger reports finished; no CDR is open")
            return

        cable_entity = self.config.get(CONF_CABLE_SENSOR, "")
        if cable_entity:
            cable_state = self.hass.states.get(cable_entity)
            if cable_state is None or cable_state.state in _UNAVAILABLE_STATES:
                reason = "not found" if cable_state is None else cable_state.state
                self._set_cdr_state(self._waiting_for_cable_state(reason))
                return

            if cable_state.state == self.config[CONF_CABLE_ACTIVE_STATE]:
                self._set_cdr_state(self._connected_cable_state())
                return

            if self._active_row is not None:
                await self._async_finish_session(
                    max(mode_state.last_changed, cable_state.last_changed)
                )
            else:
                self._set_cdr_state(
                    "Cable disconnected while not charging; no CDR is open"
                )
            return

        if finished_state:
            self._set_cdr_state(
                f"Waiting for finished mode {finished_state!r}; "
                "no cable entity configured"
            )
        elif self._active_row is not None:
            await self._async_finish_session(mode_state.last_changed)
        else:
            self._set_cdr_state("Not charging; no CDR session is open")

    def _waiting_for_mode_state(self, reason: str) -> str:
        """Describe unavailable charger mode with the current session status."""
        if self.session_active:
            return f"Waiting for charger mode state ({reason}); session unchanged"
        return f"Waiting for charger mode state ({reason}); no CDR is open"

    def _waiting_for_cable_state(self, reason: str) -> str:
        """Describe unavailable cable state with the current session status."""
        if self.session_active:
            return f"Waiting for cable state ({reason}); session unchanged"
        return f"Waiting for cable state ({reason}); no CDR is open"

    def _connected_cable_state(self) -> str:
        """Describe a connected cable while the charger mode is not active."""
        if self.session_active:
            return (
                "Charging paused or suspended; cable connected; "
                "CDR session remains open"
            )
        return "Not charging; cable connected; no CDR session is open"

    async def _async_begin_session(self, started_at: datetime) -> None:
        """Append a row containing the values available at session start."""
        target_path = monthly_csv_path(
            self.base_csv_path,
            dt_util.as_local(started_at),
        )
        if target_path != self.csv_path:
            self.csv_path = target_path
            self._rows = await self._async_read_rows(target_path)

        row = dict.fromkeys(CSV_COLUMNS, "")
        row.update(
            {
                "Created / Start Time": started_at.isoformat(),
                "Charge Point ID / EVSE ID": self._source_value(
                    self.config[CONF_CHARGE_POINT_ID]
                ),
                "Identification / ID Tag": self._source_value(
                    self.config[CONF_IDENTIFICATION]
                ),
                "Vehicle": self._source_value(self.config[CONF_VEHICLE_SOURCE]),
                "Odometer (km)": self._source_value(self.config[CONF_ODOMETER_SOURCE]),
                "Meter Start (kWh)": self._source_value(self.config[CONF_METER_SOURCE]),
            }
        )
        new_rows = [*self._rows, row]
        await self._async_write_rows(new_rows)
        self._rows = new_rows
        self._active_row = len(new_rows) - 1
        self._start_time = started_at
        self._set_last_activity("created", started_at)
        self._set_active_charging(active=True)
        self._set_cdr_state("Charging; CDR session started")
        LOGGER.info(
            "Started CDR row %d at %s; meter start=%r",
            self._active_row + 1,
            started_at.isoformat(),
            row["Meter Start (kWh)"],
        )

    async def _async_finish_session(self, finished_at: datetime) -> None:
        """Complete the open row with its end meter, energy, and duration."""
        if self._active_row is None or self._start_time is None:
            raise CdrSessionError

        row_number = self._active_row + 1
        row = self._rows[self._active_row].copy()
        meter_start = self._decimal_value(row["Meter Start (kWh)"])
        meter_end_value = self._source_value(self.config[CONF_METER_SOURCE])
        meter_end = self._decimal_value(meter_end_value)
        energy = (
            self._format_decimal(meter_end - meter_start)
            if meter_start is not None and meter_end is not None
            else ""
        )
        elapsed = max(
            0,
            int((finished_at - self._start_time).total_seconds()),
        )
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        row.update(
            {
                "Finished / End Time": finished_at.isoformat(),
                "Meter End (kWh)": meter_end_value,
                "Energy (kWh)": energy,
                "Duration": f"{hours:02}:{minutes:02}:{seconds:02}",
            }
        )

        new_rows = [*self._rows]
        new_rows[self._active_row] = row
        await self._async_write_rows(new_rows)
        self._rows = new_rows
        self._active_row = None
        self._start_time = None
        self._set_last_activity("updated", finished_at)
        self._set_cdr_state("Charging session finished; CDR row updated")
        LOGGER.info(
            "Completed CDR row %d at %s; meter end=%r, energy=%s kWh, duration=%s",
            row_number,
            finished_at.isoformat(),
            meter_end_value,
            energy or "unavailable",
            row["Duration"],
        )

    def _source_value(self, source: str) -> str:
        """Resolve an entity ID to its state, otherwise use the fixed value."""
        if _ENTITY_ID_PATTERN.fullmatch(source):
            state = self.hass.states.get(source)
            if state is None or state.state in _UNAVAILABLE_STATES:
                LOGGER.warning(
                    "Configured data entity %s is %s",
                    source,
                    "not found" if state is None else state.state,
                )
                return ""
            return state.state
        return source

    @staticmethod
    def _decimal_value(value: str) -> Decimal | None:
        """Parse a finite meter value, returning None when it is not numeric."""
        try:
            result = Decimal(value)
        except InvalidOperation:
            LOGGER.warning("Meter value %r is not a valid number", value)
            return None
        if not result.is_finite():
            LOGGER.warning("Meter value %r is not finite", value)
            return None
        return result

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        """Format energy without unnecessary decimal zeroes or exponents."""
        return format(value.normalize(), "f")

    async def _async_read_rows(self, path: Path) -> list[dict[str, str]]:
        """Read a CSV file in the executor and report parse or I/O errors."""
        LOGGER.debug("Loading CDR history from %s", path)
        try:
            rows = await self.hass.async_add_executor_job(self._read_rows, path)
        except OSError, UnicodeError, csv.Error, ValueError:
            LOGGER.exception("Could not load CDR history from %s", path)
            raise
        return rows

    def _monthly_files(self) -> list[Path]:
        """Return monthly CSV files matching the configured base filename."""
        escaped_stem = glob.escape(self.base_csv_path.stem)
        pattern = (
            f"{escaped_stem}_[0-9][0-9][0-9][0-9][0-9][0-9]"
            f"{glob.escape(self.base_csv_path.suffix)}"
        )
        return sorted(self.base_csv_path.parent.glob(pattern))

    async def _async_write_rows(self, rows: list[dict[str, str]]) -> None:
        """Persist rows atomically so an interrupted update cannot truncate CDRs."""
        try:
            await self.hass.async_add_executor_job(
                self._write_rows,
                rows,
                self.csv_path,
            )
        except OSError:
            LOGGER.exception("Unable to write charger session CSV %s", self.csv_path)
            raise

    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        """Read and validate the existing CSV file."""
        if not path.exists():
            return []

        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
                raise CdrDataError(path, "has unexpected CDR columns")
            return [
                {column: row.get(column) or "" for column in CSV_COLUMNS}
                for row in reader
            ]

    def _write_rows(self, rows: list[dict[str, str]], path: Path) -> None:
        """Replace the CSV atomically using a temporary file in the same folder."""
        temporary_path: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8-sig",
                newline="",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as file:
                temporary_path = file.name
                writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
                file.flush()
                os.fsync(file.fileno())
            Path(temporary_path).replace(path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                Path(temporary_path).unlink(missing_ok=True)

    def _set_last_activity(self, action: str, occurred_at: datetime) -> None:
        """Update activity metadata and notify the status sensor."""
        self.last_action = action
        self.last_updated = occurred_at
        self._notify_listeners()

    def _set_active_charging(self, *, active: bool) -> None:
        """Update the charging indicator and notify entities when it changes."""
        if self.active_charging == active:
            return
        self.active_charging = active
        LOGGER.info("Active charging changed to %s", active)
        self._notify_listeners()

    def _set_cdr_state(self, state: str) -> None:
        """Update the logical-flow state and notify entities when it changes."""
        if self.cdr_state == state:
            return
        self.cdr_state = state
        LOGGER.info("CDR flow state: %s", state)
        self._notify_listeners()

    @callback
    def _notify_listeners(self) -> None:
        """Notify status entities that logger state changed."""
        for listener in tuple(self._listeners):
            listener()
