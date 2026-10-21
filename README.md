# NAVEE Scooter for Home Assistant

![Status: untested on hardware](https://img.shields.io/badge/status-untested%20on%20hardware-orange)

A Home Assistant integration that talks to **NAVEE scooters directly over Bluetooth**, with no
NAVEE account or cloud. It is a Python port of the Bluetooth layer of
[Volt](https://github.com/JJRicks/navee-offline) (the offline Android companion): the same
framing, owner handshake, telemetry decoding and safety rules for controls.

> [!WARNING]
> **Untested on real hardware.** Everything passes automated tests against Volt's protocol
> vectors and a simulated scooter, but it has not yet been run against a physical scooter from
> Home Assistant. Releases are marked as pre-releases until it has.

> [!NOTE]
> Independent community project, not affiliated with or endorsed by NAVEE.

## Compatible scooters

| Scooter | Support |
| :-- | :-- |
| **NAVEE UT5 MAX** (US, model 2538) | Full: all readings, the lock and every control listed below. Volt verified each control on this model; this integration has not yet been run on one. |
| Other NAVEE scooters | Readings and the lock only. **May work, but untested.** They use the same Bluetooth service and owner login, but their settings are encoded differently per model, so no other controls are offered. |
| NAVEE e-bikes and other non-scooter products | Not supported: they use a different Bluetooth transport. |

The model is read from the scooter's serial number when it first connects, and shown on the
device page in Home Assistant. If you try another model, please open an issue with its model
number and what worked.

## What you get

| Platform | Entities |
| :-- | :-- |
| Sensor | Battery %, voltage, current, power, battery temperature, speed, remaining range, trip distance/time, odometer, driving mode; diagnostics: battery health, charge cycles, rated capacity, warning code, firmware versions |
| Binary sensor | Charging, warning active, battery problem, connected |
| Lock | Electronic lock (all models) |
| Switch (UT5 MAX) | Cruise control, tail light, automatic lights, traction control, turn signal sound, low battery mode, walk assist, power assist, hill descent control, line lights, star lights |
| Select (UT5 MAX) | Energy recovery, dashboard units, maximum speed (Sport), charge limit, lock duration |
| Number (UT5 MAX) | Kick-start speed |
| Button (UT5 MAX) | Sync scooter clock to Home Assistant's local time |

Every change is checked against fresh scooter status, sent, acknowledged, and then **confirmed
by reading the setting back**. A change that is rejected or not confirmed raises an error in
Home Assistant rather than silently showing the new value.

**Safety rules carried over from Volt**

- Locking and ride-related settings need the scooter **parked**: a motion report from the
  scooter within the last 6 seconds showing zero speed. If the scooter is off or has not sent a
  motion report yet, the change is refused with "No motion report from the scooter yet".
- Locking is refused while the scooter reports an active warning.
- **Unlocking only needs a connection** (the recovery path: an owner can always unlock).
- PIN, proximity unlock, unbind, reset and firmware opcodes are not implemented; the wire
  allowlist rejects them.

## Requirements

- Home Assistant 2025.10 or newer with a **connectable** Bluetooth adapter or an
  [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html) near where the
  scooter is parked.
- An already activated, owner-bound NAVEE scooter.
- The scooter's **owner identity** (12 hex characters), captured once from the NAVEE app.

### Getting the owner identity

The scooter authenticates its owner with a 6-byte identity tied to your NAVEE account. Capture it
once using one of Volt's helpers (see Volt's
[getting-started guide](https://github.com/JJRicks/navee-offline/blob/main/docs/getting-started.md)):
the Chrome/USB [setup website](https://pairscooter.jjricks.com/), Shizuku on the phone, or the
computer ADB helper. You never enter a NAVEE or Google password.

The setup flow accepts any of:

- the 12 hex characters, e.g. `8600123456AB`;
- the contents of the private setup file the capture helper saves (`identity: …`, `role: …`);
- a captured log line containing `Send authData: 55aa0030…` (the checksum is verified).

> [!WARNING]
> The identity can unlock and control your scooter. Treat it like a key. Home Assistant stores
> it in its config entry storage; diagnostics downloads redact it.

## Installation

**HACS:**

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ollies5&repository=ha-navee&category=integration)

The button opens this repository in HACS on your Home Assistant. Then download **NAVEE Scooter**
and restart Home Assistant. Releases are pre-releases while the integration is untested on
hardware, so turn on beta versions when downloading to see them. Without the button: HACS → ⋮ → Custom repositories → add this
repository's URL as an *Integration*.

**Manual:** copy `custom_components/navee` into your Home Assistant `config/custom_components/`
directory and restart.

Then: **Settings → Devices & services**. A nearby scooter is usually discovered automatically;
otherwise add it here:

[![Open your Home Assistant instance and start setting up NAVEE Scooter.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=navee)

Close the NAVEE app and Volt first, wake the scooter, and paste the owner identity. Home Assistant
connects, checks the identity and reads the serial before creating the entry.

## When the scooter sleeps

A powered-off NAVEE scooter goes to sleep and stops advertising, so no integration can hold a
connection forever. This one treats sleep as normal:

- **Last known values stay.** Battery %, odometer, range, trip, battery health, charge cycles,
  firmware and the lock state keep their last value while the scooter sleeps, and are restored
  after a Home Assistant restart. The **Connected** sensor shows whether the scooter is reachable.
- **Live values and controls wait.** Voltage, current, power, temperature, speed, driving mode,
  charging and warnings become unavailable, as do the setting switches/selects. Lock and unlock
  calls fail with "The scooter is asleep or out of Bluetooth range".
- **No dialling a sleeping scooter.** A connection is only attempted when the scooter has
  advertised in the last 30 seconds, so a Bluetooth proxy's connection slot is not tied up
  while it sleeps. When it wakes (power on, or plugging in the charger if it advertises then),
  the next advertisement reconnects straight away.
- **Backoff.** If the scooter advertises but connections keep failing (weak signal, a standby
  mode that refuses connections), retries back off from 30 seconds up to 5 minutes.

Still to confirm on real hardware: whether the scooter stays connectable while charging when
switched off, and whether an open connection delays its automatic power-off.

## Options

| Option | Default | Meaning |
| :-- | :-- | :-- |
| Poll interval | 30 s | How often status and battery are read. |
| Keep the Bluetooth connection open | On | Live ride/charging updates and instant controls. **The scooter accepts one connection at a time**: while Home Assistant holds it, the NAVEE app and Volt cannot connect. Turn it off to connect only for each poll and command. |

## How it works

```
custom_components/navee/
  protocol.py    framing, checksum, stream decoder, AES/XOR challenge, write allowlist
  telemetry.py   0x70 settings, 0x72 battery, 0x73 firmware, 0x74 serial, 0x79 signs,
                 0x90 home and 0x91/0x92 ride report decoding
  controls.py    named UT5 MAX controls: opcode, selector, readback offset/mask, parked flag
  identity.py    owner identity import (hex, setup file, captured authData line)
  client.py      Bleak session: connect, subscribe, authenticate, request/reply, confirmed writes
  coordinator.py polling, pushed-report throttling, reconnect on advertisement
```

Protocol summary (from Volt): service `0000d0ff-3c17-d293-8e48-14fe2e4da212`, write
`0000b002-…`, notify `0000b003-…`. Frames are `55 AA flag cmd [len] data checksum FE FD`. After
`0x30` authentication the scooter may send a 16-byte challenge answered with `0x31`
(AES-128-ECB or XOR with the OEM key), after which authentication is repeated and succeeds.

## Development

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements_test.txt ruff
# plus Home Assistant's bluetooth requirements (see .github/workflows/tests.yml)
.venv/bin/python -m pytest -q
.venv/bin/ruff check custom_components tests
```

The tests reuse Volt's protocol and telemetry vectors and drive the client against a simulated
scooter (`tests/fake_scooter.py`) that runs the real challenge handshake.

## Limitations

- Not yet run against a physical scooter from Home Assistant; the protocol port is tested against
  Volt's vectors and a simulator. Please report results.
- Model-specific controls for models other than the UT5 MAX are not ported (Volt maps many of
  them from the official app, but they are untested there too).
- Encrypted (flag 1) frames are not supported, matching Volt.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for Volt and navee-st3-pro attributions.
