# PSK Reporter

[pskreporter.py](../pskreporter.py) reports stations FreeDATA hears to
[pskreporter.info](https://pskreporter.info) with the mode `FREEDATA`. It uses the UDP/IPFIX
protocol from the PSK Reporter developer page, <https://pskreporter.info/pskdev.html>.

## 1. On/off switch

- Reporting is a runtime-only switch held in `ctx.pskreporter.enabled` (`PSKReporter` instance
  created in `AppContext.__init__`). It is **off after every server start**. There is no config key,
  so nothing saved earlier can switch it on.
- `GET /modem/pskreporter` returns `{"enabled": bool, "pending_spots": int}`.
- `POST /modem/pskreporter` with `{"enabled": bool}` switches it and returns the same object.
  A non-boolean `enabled` gives 400. The modem does not need to be running.
- Switching it off drops spots that have not been uploaded yet.
- **Encryption locks it off.** While encryption is in use (see
  [encryption.md](encryption.md#explorer-and-psk-reporter)), `POST` with `enabled: true` gives 409,
  and turning encryption on switches reporting off. `GET` and `POST` also return
  `blocked_by_encryption`.
- The GUI switch is in Settings → Web, next to the Explorer switch. It reads the state from the
  server when the settings component is mounted and shows what the server returns after each
  change, so it reflects the server's state, not a saved setting.

## 2. Where spots come from

`FrameHandler.add_to_heard_stations()` ([frame_handler.py](../frame_handler.py)) runs for every
received frame with an `origin` that passes the blacklist check (beacons, CQ, QRV, PING and so on).
After updating `state_manager.heard_stations`, it calls
`ctx.pskreporter.add_spot(origin, snr, frequency, locator=dxgrid)`, which does nothing while
reporting is off.

| Field | Value |
|---|---|
| Sender callsign | Frame `origin` without the FreeDATA SSID (`OH2ABC-1` → `OH2ABC`) |
| Frequency | `state_manager.radio_frequency` (dial) + 1500 Hz (modem centre) + measured frequency offset |
| SNR | Frame SNR, rounded and limited to -128..127 |
| Sender locator | Frame grid or the one from the station database, only if it is a valid 4/6/8 character locator |
| Time | When the frame was received (Unix seconds) |
| Mode | `FREEDATA` |
| Receiver | `STATION.mycall`, `STATION.mygrid`, decoder software `FreeDATA <MODEM_VERSION>` |

A spot is ignored when the dial frequency is unknown (`radio_frequency` is 0, for example without
rig control). Spots are not sent while `mycall` is a default callsign (`AA1AAA`, `XX1XXX`).

## 3. Batching and upload rules

- `add_spot` keeps the latest spot per callsign in `pending`. Later frames of the same station
  replace the SNR, frequency and time but keep a known locator.
- The scheduler job `pskreporter_upload` calls `flush()` every 60 s. `flush()` sends at most once
  every 300 s (`UPLOAD_INTERVAL`, measured with `time.monotonic()` from the last upload, not aligned
  to the clock). The first upload happens on the first job run after the first spot.
- A station that was uploaded within the last hour (`REPEAT_INTERVAL`) on the same band (same MHz) is
  not uploaded again. A band change is reported.
- Batches are split into datagrams of at most 30 spots (`MAX_SPOTS_PER_PACKET`), which keeps them
  well under a 1500 byte MTU.
- The datagrams go from one UDP socket (constant source port) to `report.pskreporter.info:4739`.
  Send errors are logged and the remaining spots of that batch are dropped.

## 4. Datagram layout

All values are big-endian. Built by `build_packet()`.

| Part | Content |
|---|---|
| Header | `00 0A`, total length, export time, sequence number, observation domain (random per session) |
| Receiver options template (set 3, id `0x9992`) | receiverCallsign, receiverLocator, decoderSoftware. Same bytes as on pskdev.html |
| Sender template (set 2, id `0x9993`) | senderCallsign, frequency (4 bytes), sNR (1), iMD (1), mode, informationSource (1), flowStartSeconds. "Option 3" of pskdev.html |
| Sender template (set 2, id `0x9994`) | As `0x9993` plus senderLocator before flowStartSeconds. "Option 4" of pskdev.html with its own linking value |
| Receiver record (set `0x9992`) | Length-prefixed strings |
| Sender records (set `0x9993` and/or `0x9994`) | One record per spot; spots without a locator use `0x9993`. iMD is 0, informationSource is 1 (automatically extracted) |

Each set is null-padded to a multiple of four bytes. The templates are sent in the first three
datagrams of a session and then at least once an hour. The sequence number counts the sender
records sent before this datagram.

## 5. Tests

[tests/test_pskreporter.py](../../tests/test_pskreporter.py) checks the templates against the bytes
on pskdev.html, parses a built datagram, and tests deduplication, the five-minute and one-hour
rules, template resending, splitting, the default-off state and the API toggle. The uploader gets a
fake transport, so the tests send nothing over the network.

## 6. Known limitations

- Without rig control the dial frequency is unknown, so nothing is reported.
- The frequency assumes USB/PKTUSB with the modem centred at 1500 Hz.
- The switch is runtime only: it is off after every server restart. Its state is part of the
  `/states` WebSocket stream (`is_pskreporter_enabled`, set by `PSKReporter.set_enabled`), so an
  open GUI shows it off again after a restart.
