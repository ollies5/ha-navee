# NAVEE Scooter for Home Assistant

![Tested on: NAVEE UT5 MAX](https://img.shields.io/badge/tested%20on-NAVEE%20UT5%20MAX-brightgreen)

A Home Assistant integration that talks to **NAVEE scooters directly over Bluetooth**, with no
NAVEE account or cloud. It is a Python port of the Bluetooth layer of
[Volt](https://github.com/JJRicks/navee-offline) (the offline Android companion): the same
framing, owner handshake, telemetry decoding and safety rules for controls.

> [!TIP]
> **Tested on a US NAVEE UT5 MAX** through an ESP32 Bluetooth proxy: automatic discovery and
> setup, readings, the lock and settings all work. Other NAVEE models are untested.

> [!NOTE]
> Independent community project, not affiliated with or endorsed by NAVEE.

## Compatible scooters

| Scooter | Support |
| :-- | :-- |
| **NAVEE UT5 MAX** (US, model 2538) | **Tested.** All readings, the lock and every control listed below. |
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

See [Getting the owner identity](#getting-the-owner-identity) below before you start.

## Getting the owner identity

The scooter only accepts commands from its owner. When the NAVEE app connects, it logs in to the
scooter with a **6-byte owner identity** (12 hex characters) tied to your NAVEE account. This is
not your NAVEE password or a cloud token. You normally capture it **once**, from your own phone,
while the NAVEE app connects to your scooter.

All three methods below work the same way. They briefly switch on the NAVEE app's own debug
logging and restart it. You connect to the scooter in NAVEE, they pick out the single
`Send authData:` line it logs, and then they put the logging setting back. You never type a NAVEE
or Google password anywhere.

**Before you start (every method):**

- The NAVEE app (`com.navee.ucaret`) is installed, signed in to **your** account, and can connect
  to your scooter.
- The scooter is powered on, parked and near the phone. Close Volt and any other scooter app.
- Enable **Developer options** on the phone (Settings → About phone → tap *Build number* seven
  times), then turn on **USB debugging**. Use a USB *data* cable, not a charge-only one.

> [!WARNING]
> The identity can unlock and control your scooter. Treat it like a key: don't post it, the log
> line or the setup file in issues or chats. Home Assistant keeps it in its config entry storage,
> and the integration's diagnostics download redacts it. Capture only your own account's
> identity for your own scooter.

### Option 1: Volt's setup website (easiest, testing preview)

No software to install. You need desktop Chrome or Edge.

1. Open **[pairscooter.jjricks.com](https://pairscooter.jjricks.com/)** and connect the phone by
   USB.
2. Follow the page: select the phone, approve Android's USB debugging prompt (leave *Always allow*
   unticked), then allow the temporary NAVEE logging and restart.
3. Connect to the scooter in NAVEE when asked. The page restores NAVEE's logging and closes it.
4. Download the computer backup file. This is a small JSON file containing `owner_identity`.
5. Open the file in a text editor and paste its contents into Home Assistant.

The page works in your browser, and the identity is not uploaded. If USB disconnects mid-way,
return to the same page and use **Recover cleanup**. Details are in
[Volt's web helper notes](https://github.com/JJRicks/navee-offline/blob/main/setup/web/README.md).

### Option 2: Volt's computer helper (the established route)

You need [Android Platform-Tools](https://developer.android.com/tools/releases/platform-tools)
(`adb`) and Python 3. Get Volt's source (download the ZIP from
[JJRicks/navee-offline](https://github.com/JJRicks/navee-offline)), and from its folder run:

```sh
python3 setup/capture-owner.py
```

Connect to the scooter in NAVEE when prompted (it waits up to 90 seconds). The helper checks the
captured frame, closes NAVEE, restores its logging setting, and saves `navee-owner-setup.json`.
Paste the contents of that file into Home Assistant. With several phones attached, add
`-s PHONE_SERIAL` (from `adb devices`). Full instructions:
[Volt's SETUP.txt](https://github.com/JJRicks/navee-offline/blob/main/SETUP.txt).

### Option 3: by hand with `adb`

This does exactly what the helpers do, step by step. Run these on a computer with `adb`
installed and the phone connected.

1. **Check the phone is connected.** It should be listed as `device`:
   ```sh
   adb devices
   ```
2. **Note NAVEE's current log setting.** It's usually blank; you restore it in step 6:
   ```sh
   adb shell getprop log.tag.com.navee.ucaret
   ```
3. **Turn on NAVEE's debug logging and restart it.** NAVEE only reads this setting at startup,
   which is why it has to be restarted:
   ```sh
   adb shell setprop log.tag.com.navee.ucaret VERBOSE
   adb shell am force-stop com.navee.ucaret
   ```
4. **Open NAVEE on the phone and connect to your scooter.** Wait until it shows the scooter as
   connected.
5. **Read the login line.** Only NAVEE's own messages containing `Send authData:` are shown:
   ```sh
   adb logcat -d -s "com.navee.ucaret:D" "*:S" -e "Send authData:"
   ```
   You should see a line ending like this (your bytes will differ):
   ```
   ... D com.navee.ucaret: Send authData: [55 aa 00 30 09 01 00 86 00 12 34 56 78 00 d3 fe fd]
   ```
   Nothing printed? Disconnect and reconnect in NAVEE, then run the command again.
6. **Clean up.** Close NAVEE again, then put the log setting back. Use the value from step 2; if it
   was blank, use the second form:
   ```sh
   adb shell am force-stop com.navee.ucaret
   adb shell setprop log.tag.com.navee.ucaret PREVIOUS_VALUE
   adb shell "setprop log.tag.com.navee.ucaret ''"   # if step 2 printed nothing
   adb shell getprop log.tag.com.navee.ucaret         # check it matches step 2
   ```
7. **Paste the whole `Send authData:` line** into Home Assistant. The integration verifies the
   frame's checksum and extracts the identity.

**Reading the frame yourself** (optional). The line holds 17 bytes:
`55 aa 00 30 09` (header and login command), then a key-index byte (`01` here), then the **role** (`00` =
owner, `01` = shared user), then the **six identity bytes** (`86 00 12 34 56 78` in the example,
i.e. `860012345678`), then `00`, a checksum and `fe fd`. You can paste just the 12 identity
characters instead of the line; tick *Shared user* in the setup form if the role byte was `01`.

### Entering it in Home Assistant

The setup form accepts any of:

- the 12 hex characters, e.g. `860012345678`;
- the contents of a setup file from the website or computer helper (`owner_identity`, `role`);
- a captured `Send authData:` line or a whole filtered log (only checksum-valid login frames are
  used, and conflicting identities are rejected).

Home Assistant then connects to the scooter, checks that it accepts the identity, and reads its
serial before saving. Close NAVEE first, because the scooter accepts one connection at a time.

### Afterwards

- Turn off **USB debugging** (and revoke the computer under *Revoke USB debugging
  authorizations*) once you're done.
- Keep the setup file somewhere private, or delete it. You only need it again to set up a new
  Home Assistant instance.
- If the scooter ever rejects the identity (for example after it is re-bound to another NAVEE
  account), Home Assistant asks you to enter a new one. Capture it again the same way.

### Troubleshooting

- **`adb devices` shows `unauthorized`:** unlock the phone and accept the USB debugging prompt.
  Nothing listed at all usually means a charge-only cable.
- **No `Send authData:` line:** make sure NAVEE actually connected to the scooter *after* the
  restart in step 3. A future NAVEE update could also stop logging this line; the helpers would
  then fail the same way.
- **Work profile or secondary user:** the website refuses these; use the main Android user.

## Installation

**HACS:**

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ollies5&repository=ha-navee&category=integration)

The button opens this repository in HACS on your Home Assistant. Then download **NAVEE Scooter**
and restart Home Assistant. Without the button: HACS → ⋮ → Custom repositories → add this
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
  while it sleeps. When it is switched on, the next advertisement reconnects straight away.
- **Backoff.** If the scooter advertises but connections keep failing (weak signal, a standby
  mode that refuses connections), retries back off from 30 seconds up to 5 minutes.

> [!IMPORTANT]
> **Charging is only visible while the scooter is switched on.** On the UT5 MAX, switching the
> scooter off while it charges ends the Bluetooth connection, so battery % and charging state
> stop updating until it is switched on again. Charging with the scooter on reports normally.
> Automations that wait for the battery to reach a level need the scooter left on while it
> charges, and should use a timeout so a charger is never left on indefinitely.

Not yet checked: whether an open connection ("Keep the Bluetooth connection open") stops the
scooter's automatic power-off while it is on and idle.

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

- Tested on one scooter (a US UT5 MAX) through an ESP32 Bluetooth proxy. Reports from other
  models and adapters are welcome.
- Model-specific controls for models other than the UT5 MAX are not ported (Volt maps many of
  them from the official app, but they are untested there too).
- Encrypted (flag 1) frames are not supported, matching Volt.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for Volt and navee-st3-pro attributions.
