# Charger Data Recorder

Charger Data Recorder is a Home Assistant custom integration that records charging sessions as charge detail records (CDRs) in a CSV file.

### Why use this? (Dutch ERE System)
In the Netherlands, EV owners can earn money by registering their home charging sessions under the **ERE** (*Emissiereductie-eenheden*) system. Under this European [RED III](https://europa.eu) framework, fuel suppliers buy emission reduction certificates to compensate for CO₂ emissions.

To claim these certificates through an accredited registration service (*inboekdienstverlener*), you must provide verifiable proof of the energy delivered to your vehicle. This integration helps you log the required session data (CDRs). Please note that official registration requires a charger with a built-in MID-certified meter and is capped by your net grid electricity consumption.

**Official Resources (Dutch):**
* [NEa – Inboeken elektriciteit (ERE guidelines & rules)](https://emissieautoriteit.nl)
* [Belastingdienst – Tax exemption for home charging ERE compensation](https://belastingdienst.nl)


## Installation

Copy `custom_components/charger_data_recorder` into the `custom_components`
directory of your Home Assistant configuration, then restart Home Assistant.
Add **Charger Data Recorder** from **Settings > Devices & services > Add
integration**.

## Configuration

The setup form asks for:

- Charge Point ID/ EVSE ID
- Identification / ID Tag
- Vehicle
- Odometer (km) (optional)
- meter reading (kWh) (a total amount with unit of measure kWh; like: 1,178.698 kWh)
- Charger cable state entity and connected state (optional)
   - Charger mode finished state (optional)
- A charger mode entity
   - The charger mode state value that indicates charging
   - The finished mode state value (optional)
- The CSV path and base filename (defaults to `charger_sessions.csv`)

For Charge Point ID, Identification, Vehicle, and Odometer, enter either a
fixed value or an entity ID (for example `sensor.vehicle_odometer`). Entity IDs
in `domain.entity_id` format are read as entity states when a session starts or
ends; other text is treated as a fixed value. 

Important and required entities:

Select the kWh meter entity from the entity picker; it must report the meter reading in kWh.

The charger mode entity is the primary session trigger. A session starts when
its state matches the configured charging state. The cable entity is optional:
while the mode is not charging, a connected cable keeps an open session (for
example while paused or suspended), and a disconnected cable closes it. 
An optional finished mode state closes the session immediately, even if the cable
is still connected. If no cable entity is configured but a finished mode is,
the session remains open until that finished state appears. Unknown or
unavailable trigger states do not close an open session.

To edit these fields later, open the integration under **Settings > Devices &
services**, open its **Configure** (gear) dialog, and save the updated options.
The CSV path is relative to Home Assistant's configuration directory, and
parent-directory (`..`) or absolute paths are not accepted. Use
`custom_components\charger_data_recorder\charger_sessions.csv` to store the
files in that subfolder; do not include a leading `config\`, because the
configuration directory is already the base. Forward slashes also work.

The integration appends the local year and month to the base filename for each
session start. For example, `charger_sessions.csv` becomes
`charger_sessions_202611.csv` in November 2026. A session crossing into a new
month remains in the file for the month it started. Changing the configured
path or base filename sends future sessions to the new location; existing
records are not moved.

## CSV records

The integration creates the monthly CSV, including its header, in the Home
Assistant configuration directory as soon as setup completes. The active
absolute path is shown in the **Last CDR activity** sensor's `csv_file`
attribute. The sensor also shows the live cable/mode states alongside the 
configured active states; if they do not match, no session row is started.

It writes a row as soon as a session starts, recording its start timestamp,
charger ID, identification, vehicle, odometer, and starting meter reading.
When the active cable/mode condition ends, it fills in the end timestamp,
ending meter reading, energy difference, and elapsed duration (`hh:mm:ss`).
Timestamps include their timezone offset. The CSV uses these columns:

| CSV column | Meaning |
| --- | --- |
| Created / Start Time (Aangemaakt) | Session start |
| Finished / End Time (Beëindigd) | Session end |
| Charge Point ID / EVSE ID (Oplaadpunt) | Physical charger |
| Identification / ID Tag (Identificatie) | Authorization token or account |
| Vehicle (Voertuig) | Vehicle or license plate |
| Odometer (km) (Kilometerstand) | Mileage at session start |
| Meter Start (kWh) (Beginwaarde meter) | Meter value at session start |
| Meter End (kWh) (Eindwaarde meter) | Meter value at session end |
| Energy (kWh) (Energie) | End meter minus start meter |
| Duration (Duur) | Elapsed session time |

An unfinished row is kept if Home Assistant restarts during a session and is
completed when the integration next observes the charger leave the active
state. The **CDR state** sensor reports the logical flow decision, while
**CDR active charging** is on when the charger mode equals the configured
charging state. **Refresh data** asks Home Assistant to update the configured
source entities, then evaluates their latest states. **Last CDR activity**
reports the timestamp of the most recently created or updated row.

## Testing in Home Assistant

1. In **Settings > Devices & services > Helpers**, create two Toggle helpers,
   for example `input_boolean.test_cable` and `input_boolean.test_charging`.
   Create a Number helper named `input_number.test_meter`, with minimum `0`,
   maximum `1000`, and step `0.1`.
2. Add the integration. Select the two Toggle helpers for cable and mode and
   use `on` for both active states. Enter `input_number.test_meter` as the
   meter entity. Other fields can be fixed test values such as `TEST-EVSE`,
   `TEST-RFID`, and `Test vehicle`; odometer can be left blank.
3. Find **CDR state**, **CDR active charging**, and **Refresh data** on the
   integration device. The state sensor explains the current decision. Press
   **Refresh data** to request updates from configured entities and evaluate
   their current states. The CSV should exist with a header after setup.
4. Set the meter helper to `100`, then set the mode helper to its charging
   value (`on` in this example). **CDR active charging** should turn on and the
   CSV should contain a start row with meter start `100`.
5. To test pause handling, set the mode helper to a non-charging value while
   keeping the cable helper at its connected value (`on`). The active charging
   indicator should turn off, but the CDR row should remain open.
6. Change the meter to `105.5` and set the cable helper to its disconnected
   value (`off`). The row should be completed with an end meter of `105.5`,
   energy of `5.5`, and a duration. If a finished mode state is configured,
   setting the mode helper to that state ends the session immediately.
7. Repeat with another session to verify that a second row is added. To test
   restart recovery, restart Home Assistant while `session_active` is true,
   then turn off one Toggle helper and confirm the open row is completed.

If a test does not work, compare `cable_state` and `mode_state` on the activity
sensor to their configured states and read the **CDR state** sensor. Also check the
Home Assistant log for the startup message that names both watched entities
and for CSV write errors. The file is in Home Assistant's configuration
directory, not the integration's custom component directory.

The **CDR state** sensor describes the flow decision; **CDR active charging**
is on only while the mode entity equals its configured charging state. The
**Refresh data** button asks Home Assistant to update the configured source
entities and then evaluates the workflow using their latest states. Some
entities (such as helpers) update only when changed by their own controls.

## Debug logging

To follow the integration's setup, trigger checks, and CDR writes, add this to
your Home Assistant `configuration.yaml` (merge it into an existing `logger:`
section if you already have one):

```yaml
logger:
  default: info
  logs:
    custom_components.charger_data_recorder: debug
```

Check the configuration and restart Home Assistant. Then open **Settings >
System > Logs**, select **Home Assistant Core**, and enable **Show raw logs**
if needed. Search for `custom_components.charger_data_recorder`. The trace
reports setup and CSV location, each cable/mode state change, whether the
configured active-state condition matches, and when a CDR row starts or ends.
Warnings and exceptions report unavailable configured entities, invalid meter
readings, invalid existing CSV files, and CSV read/write failures. Debug
logging does not include identification tags or vehicle field values.
