"""Tests for charger session CSV logging."""

# ruff: noqa: S101

from __future__ import annotations

import asyncio
import csv
import importlib
import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Keep the CSV logger tests runnable without Home Assistant's native dependencies.
homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
core = types.ModuleType("homeassistant.core")


class Event:
    """Minimal Home Assistant event used by the state-change callback."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Store event data."""
        self.data = data


core.Event = Event
core.State = type("State", (), {})
core.HomeAssistant = type("HomeAssistant", (), {})
core.callback = lambda function: function
helpers = types.ModuleType("homeassistant.helpers")
helpers.__path__ = []
event = types.ModuleType("homeassistant.helpers.event")
event.async_track_state_change_event = lambda *_args: lambda: None
exceptions = types.ModuleType("homeassistant.exceptions")
util = types.ModuleType("homeassistant.util")
util.__path__ = []
dt_util = types.ModuleType("homeassistant.util.dt")
dt_util.now = lambda: datetime.now(UTC)
dt_util.as_local = lambda value: value.astimezone(UTC)


class HomeAssistantError(Exception):
    """Minimal Home Assistant service error."""


exceptions.HomeAssistantError = HomeAssistantError
sys.modules.update(
    {
        "homeassistant": homeassistant,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.event": event,
        "homeassistant.exceptions": exceptions,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt_util,
    }
)

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(Path(__file__).parents[1] / "custom_components")]
integration = types.ModuleType("custom_components.charger_data_recorder")
integration.__path__ = [
    str(Path(__file__).parents[1] / "custom_components" / "charger_data_recorder")
]
sys.modules["custom_components"] = custom_components
sys.modules["custom_components.charger_data_recorder"] = integration

csv_logger = importlib.import_module(
    "custom_components.charger_data_recorder.csv_logger"
)
csv_paths = importlib.import_module("custom_components.charger_data_recorder.csv_path")
CSV_COLUMNS = importlib.import_module(
    "custom_components.charger_data_recorder.const"
).CSV_COLUMNS


class FakeState:
    """Minimal Home Assistant state for logger tests."""

    def __init__(self, state: str, last_changed: datetime) -> None:
        """Create a fake state."""
        self.state = state
        self.last_changed = last_changed


class FakeStates:
    """Minimal state machine for logger tests."""

    def __init__(self, states: dict[str, FakeState]) -> None:
        """Store entity states."""
        self._states = states

    def get(self, entity_id: str) -> FakeState | None:
        """Return the requested entity state."""
        return self._states.get(entity_id)


class FakeServices:
    """Record Home Assistant service calls."""

    def __init__(self) -> None:
        """Initialize the call list."""
        self.calls: list[tuple[str, str, dict[str, Any], bool]] = []
        self.failure: Exception | None = None

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        *,
        blocking: bool,
    ) -> None:
        """Record a service call."""
        self.calls.append((domain, service, data, blocking))
        if self.failure is not None:
            raise self.failure


class FakeHass:
    """Minimal Home Assistant object for logger tests."""

    def __init__(self, states: dict[str, FakeState]) -> None:
        """Create a fake Home Assistant object."""
        self.states = FakeStates(states)
        self.services = FakeServices()

    async def async_add_executor_job(
        self,
        function: Callable[..., Any],
        *args: Any,
    ) -> Any:
        """Run file operations off the event loop."""
        return await asyncio.to_thread(function, *args)


class CdrLoggerTest(unittest.IsolatedAsyncioTestCase):
    """Test CSV creation and charging-state transitions."""

    def test_csv_paths_normalize_windows_separators_and_append_month(self) -> None:
        """Nested Windows-style paths map to monthly files."""
        normalized = csv_paths.normalize_csv_path(
            r"custom_components\charger_data_recorder\charger_sessions.csv"
        )
        assert normalized == (
            "custom_components/charger_data_recorder/charger_sessions.csv"
        )
        assert csv_paths.monthly_csv_path(
            Path(normalized),
            datetime(2026, 11, 7, tzinfo=UTC),
        ) == Path("custom_components/charger_data_recorder/charger_sessions_202611.csv")

    def test_csv_paths_reject_absolute_and_parent_paths(self) -> None:
        """CSV paths cannot escape the Home Assistant config directory."""
        for invalid_path in (
            "",
            "../charger_sessions.csv",
            r"custom_components\..\charger_sessions.csv",
            r"C:\temp\charger_sessions.csv",
            r"\server\share\charger_sessions.csv",
            "charger_sessions.txt",
        ):
            with self.subTest(path=invalid_path), self.assertRaises(ValueError):  # noqa: PT027
                csv_paths.normalize_csv_path(invalid_path)

    def test_resolved_csv_path_stays_inside_config_directory(self) -> None:
        """Normalized folder paths resolve beneath the HA configuration root."""
        with tempfile.TemporaryDirectory() as config_dir:
            path = csv_paths.resolve_csv_path(
                config_dir,
                "custom_components/charger_data_recorder/charger_sessions.csv",
            )
            assert path == (
                Path(config_dir)
                / "custom_components"
                / "charger_data_recorder"
                / "charger_sessions.csv"
            )

    def setUp(self) -> None:
        """Set up temporary CSV storage and initial entity states."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.csv_path = Path(self.temp_dir.name) / "charger_sessions.csv"
        self.now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
        self.states = {
            "input_boolean.test_cable": FakeState("off", self.now),
            "input_boolean.test_charging": FakeState("off", self.now),
            "input_number.test_meter": FakeState("100", self.now),
        }
        self.hass = FakeHass(self.states)
        self.config = {
            "charge_point_id": "TEST-EVSE",
            "identification": "TEST-RFID",
            "vehicle_source": "Test vehicle",
            "odometer_source": "",
            "meter_source": "input_number.test_meter",
            "cable_sensor": "input_boolean.test_cable",
            "cable_active_state": "on",
            "mode_sensor": "input_boolean.test_charging",
            "mode_active_state": "on",
        }
        self.logger = csv_logger.CdrLogger(
            self.hass,
            self.config,
            str(self.csv_path),
        )
        self.state_change: Callable[[object], Awaitable[None]] | None = None
        self.watched_entities: set[str] = set()

    async def asyncSetUp(self) -> None:
        """Mock event registration and start the logger."""
        self.listener_patcher = patch.object(
            csv_logger,
            "async_track_state_change_event",
            side_effect=self._track_state_changes,
        )
        self.listener_patcher.start()
        self.addCleanup(self.listener_patcher.stop)
        await self.logger.async_load()

    def _track_state_changes(
        self,
        _hass: object,
        entity_ids: set[str],
        action: Callable[[object], Awaitable[None]],
    ) -> Callable[[], None]:
        """Capture the state-change callback."""
        self.watched_entities = entity_ids
        self.state_change = action
        return lambda: None

    async def _change_state(
        self,
        entity_id: str,
        state: str,
        changed_at: datetime,
    ) -> None:
        """Update a fake state and invoke the registered listener."""
        old_state = self.states.get(entity_id)
        self.states[entity_id] = FakeState(state, changed_at)
        if self.state_change is not None:
            await self.state_change(
                Event(
                    {
                        "entity_id": entity_id,
                        "old_state": old_state,
                        "new_state": self.states[entity_id],
                    }
                )
            )

    def _read_rows(self, path: Path | None = None) -> list[dict[str, str]]:
        """Read all CDR rows from the test CSV."""
        with (path or self.logger.csv_path).open(
            encoding="utf-8-sig",
            newline="",
        ) as file:
            return list(csv.DictReader(file))

    async def test_setup_creates_header_and_mode_starts_charging(self) -> None:
        """The CSV exists immediately and the mode state starts the CDR."""
        assert self.logger.csv_path.exists()
        with self.logger.csv_path.open(encoding="utf-8-sig", newline="") as file:
            assert tuple(next(iter(csv.reader(file)))) == CSV_COLUMNS

        await self.logger.async_start()
        await self._change_state(
            "input_boolean.test_cable",
            "on",
            self.now + timedelta(minutes=1),
        )
        assert not self.logger.session_active

        await self._change_state(
            "input_boolean.test_charging",
            "on",
            self.now + timedelta(minutes=2),
        )
        assert self.logger.session_active
        assert self.logger.active_charging
        assert len(self._read_rows()) == 1

    async def test_connected_cable_keeps_paused_session_open(self) -> None:
        """A non-charging mode does not close a session while cable is connected."""
        await self.logger.async_start()
        started = self.now + timedelta(minutes=1)
        await self._change_state("input_boolean.test_charging", "on", started)
        await self._change_state("input_boolean.test_cable", "on", started)

        paused = started + timedelta(minutes=5)
        await self._change_state("input_boolean.test_charging", "off", paused)

        assert self.logger.session_active
        assert not self.logger.active_charging
        assert "paused or suspended" in self.logger.cdr_state
        assert self._read_rows()[0]["Finished / End Time"] == ""

        disconnected = paused + timedelta(minutes=2)
        await self._change_state(
            "input_boolean.test_cable",
            "off",
            disconnected,
        )
        assert not self.logger.session_active
        assert self._read_rows()[0]["Finished / End Time"] == disconnected.isoformat()

    async def test_finished_mode_closes_session_even_with_connected_cable(self) -> None:
        """An explicitly configured finished mode ends the CDR immediately."""
        self.config["mode_finished_state"] = "finished"
        await self.logger.async_start()
        started = self.now + timedelta(minutes=1)
        await self._change_state("input_boolean.test_cable", "on", started)
        await self._change_state("input_boolean.test_charging", "on", started)

        finished = started + timedelta(minutes=5)
        await self._change_state("input_boolean.test_charging", "finished", finished)

        assert not self.logger.session_active
        assert self._read_rows()[0]["Finished / End Time"] == finished.isoformat()

    async def test_no_cable_or_finished_state_ends_on_non_charging_mode(self) -> None:
        """A missing cable entity is ignored and mode transitions finish rows."""
        self.config.pop("cable_sensor")
        self.config.pop("cable_active_state")
        await self.logger.async_start()
        assert self.watched_entities == {"input_boolean.test_charging"}
        started = self.now + timedelta(minutes=1)
        await self._change_state("input_boolean.test_charging", "on", started)
        assert self.logger.session_active

        finished = started + timedelta(minutes=5)
        await self._change_state("input_boolean.test_charging", "off", finished)

        assert not self.logger.session_active
        assert self._read_rows()[0]["Finished / End Time"] == finished.isoformat()

        self.config["cable_sensor"] = ""
        self.config["cable_active_state"] = ""
        started_without_cable = finished + timedelta(minutes=1)
        await self._change_state(
            "input_boolean.test_charging",
            "on",
            started_without_cable,
        )
        finished_without_cable = started_without_cable + timedelta(minutes=5)
        await self._change_state(
            "input_boolean.test_charging",
            "off",
            finished_without_cable,
        )

        assert not self.logger.session_active
        assert (
            self._read_rows()[1]["Finished / End Time"]
            == finished_without_cable.isoformat()
        )

    async def test_monthly_file_rotation_keeps_cross_month_session_together(
        self,
    ) -> None:
        """A session stays in its start-month file; later sessions rotate."""
        await self.logger.async_start()
        started = datetime(2026, 11, 30, 23, 55, tzinfo=UTC)
        await self._change_state("input_boolean.test_cable", "on", started)
        await self._change_state("input_boolean.test_charging", "on", started)
        november_path = self.csv_path.with_name("charger_sessions_202611.csv")
        assert self.logger.csv_path == november_path

        finished = datetime(2026, 12, 1, 0, 5, tzinfo=UTC)
        self.states["input_number.test_meter"] = FakeState("105", finished)
        await self._change_state("input_boolean.test_charging", "off", finished)
        await self._change_state("input_boolean.test_cable", "off", finished)
        assert self.logger.csv_path == november_path
        assert self._read_rows(november_path)[0]["Finished / End Time"] == (
            finished.isoformat()
        )

        next_started = finished + timedelta(minutes=5)
        await self._change_state("input_boolean.test_charging", "on", next_started)
        december_path = self.csv_path.with_name("charger_sessions_202612.csv")
        assert self.logger.csv_path == december_path
        assert december_path.exists()

    async def test_nested_csv_folder_is_created(self) -> None:
        """A missing configured subfolder is created when the CSV is initialized."""
        nested_base_path = (
            Path(self.temp_dir.name)
            / "custom_components"
            / "charger_data_recorder"
            / "charger_sessions.csv"
        )
        nested_logger = csv_logger.CdrLogger(
            self.hass,
            self.config,
            str(nested_base_path),
        )

        await nested_logger.async_load()

        assert nested_logger.csv_path.parent.is_dir()
        assert nested_logger.csv_path.exists()

    async def test_restart_recovers_open_session_from_previous_month_file(
        self,
    ) -> None:
        """Recovery finds an unfinished row in its original monthly file."""
        await self.logger.async_start()
        started = datetime(2026, 11, 30, 23, 55, tzinfo=UTC)
        await self._change_state("input_boolean.test_cable", "on", started)
        await self._change_state("input_boolean.test_charging", "on", started)
        november_path = self.csv_path.with_name("charger_sessions_202611.csv")

        replacement = csv_logger.CdrLogger(
            self.hass,
            self.config,
            str(self.csv_path),
        )
        await replacement.async_load()

        assert replacement.session_active
        assert replacement.csv_path == november_path

    async def test_unknown_mode_does_not_close_open_session(self) -> None:
        """Unavailable and unknown modes preserve the open CSV row."""
        self.config["cable_sensor"] = ""
        self.config["cable_active_state"] = ""
        self.config["mode_finished_state"] = "finished"
        await self.logger.async_start()
        started = self.now + timedelta(minutes=1)
        await self._change_state("input_boolean.test_charging", "on", started)

        unavailable = started + timedelta(minutes=2)
        await self._change_state(
            "input_boolean.test_charging",
            "unavailable",
            unavailable,
        )

        assert self.logger.session_active
        assert not self.logger.active_charging
        assert "session unchanged" in self.logger.cdr_state
        assert self._read_rows()[0]["Finished / End Time"] == ""

    async def test_finished_state_waits_when_cable_sensor_is_not_configured(
        self,
    ) -> None:
        """When configured, the finished mode disambiguates paused states."""
        self.config["cable_sensor"] = ""
        self.config["cable_active_state"] = ""
        self.config["mode_finished_state"] = "finished"
        await self.logger.async_start()
        started = self.now + timedelta(minutes=1)
        await self._change_state("input_boolean.test_charging", "on", started)

        paused = started + timedelta(minutes=2)
        await self._change_state("input_boolean.test_charging", "paused", paused)
        assert self.logger.session_active
        assert "Waiting for finished mode" in self.logger.cdr_state

        finished = paused + timedelta(minutes=3)
        await self._change_state("input_boolean.test_charging", "finished", finished)
        assert not self.logger.session_active
        assert self._read_rows()[0]["Finished / End Time"] == finished.isoformat()

    async def test_manual_refresh_updates_configured_entities_and_runs_flow(
        self,
    ) -> None:
        """Refresh calls update_entity and then evaluates current HA states."""
        await self.logger.async_start()
        await self.logger.async_refresh()

        assert self.hass.services.calls == [
            (
                "homeassistant",
                "update_entity",
                {
                    "entity_id": [
                        "input_boolean.test_cable",
                        "input_boolean.test_charging",
                        "input_number.test_meter",
                    ]
                },
                True,
            )
        ]
        assert "Cable disconnected" in self.logger.cdr_state

    async def test_manual_refresh_reconciles_after_update_error(self) -> None:
        """A failed refresh is surfaced after evaluating current entity states."""
        await self.logger.async_start()
        self.hass.services.failure = HomeAssistantError("entity update failed")

        with self.assertRaises(HomeAssistantError):  # noqa: PT027
            await self.logger.async_refresh()

        assert "Cable disconnected" in self.logger.cdr_state

    async def test_session_is_completed_with_meter_energy_and_duration(self) -> None:
        """An active session writes start fields and later completes its row."""
        with self.assertLogs(
            "custom_components.charger_data_recorder",
            level="DEBUG",
        ) as captured_logs:
            await self.logger.async_start()
            started_at = self.now + timedelta(minutes=2)
            await self._change_state(
                "input_boolean.test_cable",
                "on",
                self.now + timedelta(minutes=1),
            )
            await self._change_state(
                "input_boolean.test_charging",
                "on",
                started_at,
            )
            finished_at = started_at + timedelta(minutes=10)
            self.states["input_number.test_meter"] = FakeState("105.5", finished_at)
            await self._change_state(
                "input_boolean.test_charging",
                "off",
                finished_at,
            )
            await self._change_state(
                "input_boolean.test_cable",
                "off",
                finished_at,
            )

        assert any("Evaluating charger mode" in line for line in captured_logs.output)
        assert any("Started CDR row" in line for line in captured_logs.output)
        assert any("Completed CDR row" in line for line in captured_logs.output)
        assert self._read_rows()[0]["Meter Start (kWh)"] == "100"

        row = self._read_rows()[0]
        assert row["Created / Start Time"] == started_at.isoformat()
        assert row["Finished / End Time"] == finished_at.isoformat()
        assert row["Charge Point ID / EVSE ID"] == "TEST-EVSE"
        assert row["Identification / ID Tag"] == "TEST-RFID"
        assert row["Vehicle"] == "Test vehicle"
        assert row["Meter End (kWh)"] == "105.5"
        assert row["Energy (kWh)"] == "5.5"
        assert row["Duration"] == "00:10:00"
        assert not self.logger.session_active
        assert self.logger.last_action == "updated"

    async def test_open_session_is_recovered_after_restart(self) -> None:
        """The next logger instance resumes an unfinished CDR row."""
        await self.logger.async_start()
        await self._change_state(
            "input_boolean.test_cable",
            "on",
            self.now + timedelta(minutes=1),
        )
        await self._change_state(
            "input_boolean.test_charging",
            "on",
            self.now + timedelta(minutes=2),
        )

        restarted_logger = csv_logger.CdrLogger(
            self.hass,
            self.config,
            str(self.csv_path),
        )
        await restarted_logger.async_load()

        assert restarted_logger.session_active
        assert restarted_logger.last_action == "restored"


if __name__ == "__main__":
    unittest.main()
