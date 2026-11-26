# Configuration (config.ini)

Every setting the server reads from `config.ini`: what it does, its type and default, and when a change
takes effect. The types and the list of settings come from `CONFIG.config_types` in
[config.py](../config.py); the defaults are those of [config.ini.example](../config.ini.example), the
template a new installation starts from. Where the GUI shows a setting, its label is given in
*italics*; the GUI's own settings pages are described in
[screens-and-settings.md](../../freedata_gui/docs/screens-and-settings.md).

## Where the file is

| Installation | Folder |
|---|---|
| Windows installer | `%APPDATA%\FreeDATA` |
| Linux AppImage, Linux install script | `~/.config/FreeDATA` |
| macOS scripts | `~/Library/Application Support/FreeDATA` |
| Docker | the mounted data folder (`./freedata-data` in [README.docker.md](../../README.docker.md)) |

The environment variable `FREEDATA_CONFIG` names another file instead. The message database
`freedata-messages.db` sits next to `config.ini` (or where `FREEDATA_DATABASE` says).

## How the server treats the file

- **First start:** if the file does not exist, it is created from `config.ini.example`. The macOS run
  script changes `modemport` to 5050 at that point, because AirPlay uses port 5000.
- **Every start:** settings that are missing are added (with the defaults below), settings and sections
  the server does not know are removed, and the file is written back. Comments in the file do not
  survive this. Old `ptt_type` values that Hamlib does not know (`USB`, `MICDATA`) are changed to `RIG`.
- **Values:** booleans are `True` / `False`, numbers are integers, and lists are JSON
  (`allowed_hosts = ["shackpc"]`, `callsign_blacklist = ["AA1AAA"]`). A list may hold `#` or `%` only
  inside a JSON string; the server writes them as `#` / `%`.
- **Changes from the GUI or `POST /config/`:** only the settings sent are changed; they are merged into
  the stored config, checked (`STATION.mycall` and `STATION.mygrid` must be valid) and written. A change
  outside the `GUI` section restarts the modem, except `STATION.enable_log_to_temp` and
  `STATION.respond_to_cq`, which apply at once (see [api.md](api.md)). Open GUI tabs get a
  `config changed` event and show the new values.
- **Editing the file by hand:** stop the server first. The running server keeps its own copy and writes
  it back on the next save, and `NETWORK` settings are only read at start.

## [NETWORK]

| Key | Type | Default | Meaning |
|---|---|---|---|
| `modemaddress` | str | `127.0.0.1` | Address the server listens on. `127.0.0.1` or `localhost`: this computer only. `0.0.0.0`: every network interface (the GUI from other computers; the server has no login, see [SECURITY.md](../../SECURITY.md)). *Modem host*. Read at start |
| `modemport` | int | `5000` | Port of the REST API, the WebSockets and the GUI (`/gui`). *Modem port*. Read at start |
| `allowed_hosts` | list | `[]` | Extra host names the server accepts in a request's Host or Origin header ([request_guard.py](../request_guard.py)). IP addresses, `localhost`, `.local` names and the computer's own name are always accepted. Needed when the GUI is opened under another name (a DNS name, a reverse proxy); otherwise the browser gets 403. Not in the GUI |

## [STATION]

| Key | Type | Default | Meaning |
|---|---|---|---|
| `mycall` | str | `AA1AAA` | Own call sign without SSID, up to 7 letters or digits; checked on every save. *Callsign* |
| `myssid` | int | `1` | Own SSID, 0-15. *Callsign SSID* |
| `mygrid` | str | `JN48ea` | Maidenhead locator, 4 or 6 characters; checked on every save. *Grid Locator / Maidenhead* |
| `ssid_list` | list | `[0, 1, ..., 9]` | SSIDs of `mycall` this station also answers to: a frame addressed to `mycall` with any of these SSIDs counts as its own. Not in the GUI |
| `enable_explorer` | bool | `False` | Publish the station and its heard stations on explorer.freedata.app. Off while encryption is in use. *Publish station details* (Web tab) |
| `enable_stats` | bool | `False` | Send the statistics of received ARQ sessions to the explorer. Off while encryption is in use. Not in the GUI |
| `respond_to_cq` | bool | `True` | Answer a received CQ with QRV. Off: no answer, the caller is still listed as heard. Read at every CQ, no modem restart. *Respond to CQ callings with a QRV reply* |
| `iaru_region` | int | `0` | IARU region 1-3, or 0 (not set). Encrypted messages are not sent on the amateur bands of this region (0: of any region). *IARU region* |
| `enable_callsign_blacklist` | bool | `False` | Ignore requests from the call signs in `callsign_blacklist`. *Enable callsign blacklist* |
| `callsign_blacklist` | list | `[]` | Call signs to ignore (the GUI edits them one per line). *Callsign blacklist* |
| `enable_log_to_temp` | bool | `False` | Also write the log to `freedata_server_<date>_<time>.log` in the system temp folder, without colour codes. Starts and stops at once, no modem restart; `--log-to-temp` or `FREEDATA_LOG_TO_TEMP=1` at start turn it on and store it here. *Also log into a file in the temp directory* |

## [AUDIO]

| Key | Type | Default | Meaning |
|---|---|---|---|
| `input_device` | str | `5a1c` | Receive sound device: a 4-digit hex id, the CRC-16 of the device's name and host API ([audio.py](../audio.py) `device_crc`), so it survives renumbering. The default is a placeholder that matches no device. *Audio Input Device* |
| `output_device` | str | `bd6c` | Transmit sound device, same kind of id. *Audio Output Device* |
| `rx_audio_level` | int | `0` | Receive gain in dB, -30 to +20. *RX Audio Level* |
| `tx_audio_level` | int | `0` | Transmit gain in dB, -30 to +20. *TX Audio Level* |
| `rx_auto_audio_level` | bool | `True` | Normalise the receive level automatically. *Auto RX audio level* |
| `tx_auto_audio_level` | bool | `False` | Raise the transmit level to the maximum; watch the radio's ALC. *Auto TX audio level* |
| `rx_buffer_ms` | int | `0` | Depth of the receive capture buffer in ms, 100-1000; `0` means the default, 200 ms. Raise it when the system check shows "driver" losses. *RX buffer in ms* |

## [RADIO]

| Key | Type | Default | Meaning |
|---|---|---|---|
| `control` | str | `disabled` | How the radio is controlled: `disabled` (VOX, no CAT), `serial_ptt` (PTT on DTR/RTS only), `rigctld` (a rigctld already running, see `[RIGCTLD]`), `rigctld_bundle` (FreeDATA starts its own rigctld), `flrig`. *Rig Control* |
| `model_id` | int | `1001` | Hamlib radio model (`-m`); the default is the Yaesu FT-847, a placeholder. *Radio model* |
| `serial_port` | str | `/dev/cu.Bluetooth-Incoming-Port` | CAT serial port (`-r`), for example `COM3` or `/dev/ttyUSB0`; the default is a macOS placeholder. *Radio COM port* |
| `serial_speed` | int | `38400` | CAT baud rate (`-s`); must match the radio. *Serial speed* |
| `data_bits` | int | `8` | `--set-conf data_bits` |
| `stop_bits` | int | `1` | `--set-conf stop_bits` |
| `serial_handshake` | str | `ignore` | Not used: it is not passed to rigctld |
| `ptt_port` | str | `ignore` | Separate PTT port (`-p`) for `rigctld` / `rigctld_bundle`, or the serial port keyed by `serial_ptt` |
| `ptt_type` | str | `RIG` | Hamlib PTT type (`-P`): `RIG` (CAT), `RTS`, `DTR`, `CM108`, ... `RIG` works for most radios with USB audio |
| `ptt_mode` | str | `TX` | What PTT on asks for: `TX`, `TX_MIC` or `TX_DATA`; some radios need `TX_DATA` to take the data audio input. *PTT Mode* |
| `serial_dcd` | str | `NONE` | Hamlib DCD type (`-D`) |
| `serial_dtr` | str | `OFF` | DTR line state (`--set-conf dtr_state`); with `serial_ptt`, the line that keys PTT |
| `serial_rts` | str | `OFF` | RTS line state (`--set-conf rts_state`); with `serial_ptt`, the line that keys PTT |

A value of `ignore` (or `0`) leaves that rigctld argument out. Every action that changes the radio
(PTT, frequency, mode, power, tuner, clock) is logged with its result.

## [RIGCTLD]

| Key | Type | Default | Meaning |
|---|---|---|---|
| `ip` | str | `127.0.0.1` | rigctld's address. *Rigctld IP* |
| `port` | int | `4532` | rigctld's port. *Rigctld Port* |
| `path` | str | (empty) | Not used |
| `command` | str | (empty) | Not used |
| `arguments` | str | (empty) | Extra arguments for the bundled rigctld, space separated. *Rigctld custom arguments* |
| `enable_vfo` | bool | `False` | rigctld in VFO mode (`--vfo`); FreeDATA then names the VFO in its commands. *Rigctld VFO parameter* |

## [FLRIG]

| Key | Type | Default | Meaning |
|---|---|---|---|
| `ip` | str | `127.0.0.1` | flrig's address. *Flrig host* |
| `port` | int | `12345` | flrig's XML-RPC port. *Flrig port* |

## [MODEM]

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enable_morse_identifier` | bool | `False` | Not used |
| `tx_delay` | int | `50` | Pause in ms between keying PTT and the audio. *TX delay in ms* |
| `maximum_bandwidth` | int | `2438` | Widest mode this station uses: `250`, `500`, `1700` or `2438` Hz. An ARQ session uses the smaller of the two stations' values. *Maximum used bandwidth* |
| `enable_fsk` | bool | `False` | Let ARQ sessions and P2P connections fall back to the 4FSK mode (`data_fsk4_50`) when the OFDM modes fail, as on auroral paths. Both stations need it. *Enable FSK modes* |
| `enable_high_rate` | bool | `True` | Allow the speed levels with higher code rates (5-7) when the SNR allows. Both stations need it. *Enable higher code rates* |

## [CHANNEL]

Listen before talk ([channel_gate.py](../channel_gate.py), [protocol-overview.md](protocol-overview.md#listen-before-talk)):
before starting something new (a message's ARQ session, ping, CQ, beacon, test frame, P2P connection or
a CQ reply) the station waits for a clear channel. Times are in seconds. None of these are in the GUI.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enable_listen_before_talk` | bool | `True` | `False` sends at once, without listening |
| `listen_time` | int | `2` | The channel must be clear this long in a row |
| `freedata_holdoff` | int | `8` | ... and no FreeDATA signal heard for this long, to cover the reply window of a station this one cannot hear |
| `random_delay` | int | `3` | Then a random wait up to this long, so stations waiting for the same session do not key up together |
| `freedata_max_wait` | int | `60` | FreeDATA traffic is never sent over: after this long the transmission is dropped (the API answers 503; a queued message stays queued) |
| `other_busy_wait` | int | `30` | A channel busy only from other signals is waited for this long, then the transmission goes out anyway |
| `beacon_max_wait` | int | `10` | `freedata_max_wait` for beacons |
| `cq_reply_holdoff` | int | `1` | `freedata_holdoff` for the QRV reply to a CQ (the CQ was just heard) |
| `cq_reply_max_wait` | int | `15` | `freedata_max_wait` for the QRV reply |
| `cq_reply_random_delay` | int | `5` | `random_delay` for the QRV reply, so several answering stations spread out |

## [SOCKET_INTERFACE]

The VARA-style TCP interface for other programs ([arq-sessions.md](arq-sessions.md)). Not in the GUI.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enable` | bool | `False` | Start the interface |
| `host` | str | `127.0.0.1` | Address it listens on |
| `cmd_port` | int | `9000` | Command port; `0` means 8300 |
| `data_port` | int | `9001` | Data port; `0` means 8301 |

## [MESSAGES]

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enable_auto_repeat` | bool | `True` | Queue a failed message again when its receiver is heard (beacon, CQ or ping at -2 dB SNR or better), up to 10 times. *Enable message auto repeat* |
| `enable_common_key` | bool | `False` | Use one encryption key for all stations; the key itself is kept only in memory and entered after each start ([encryption.md](encryption.md)). Set from the GUI's common key dialog |

## [QSO_LOGGING]

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enable_adif_udp` | bool | `False` | Send an ADIF record of a message QSO over UDP. *ADIF UDP logging* |
| `adif_udp_host` | str | `127.0.0.1` | Logging program's address |
| `adif_udp_port` | int | `2237` | Logging program's UDP port |
| `enable_adif_wavelog` | bool | `False` | Send the ADIF record to Wavelog. *Wavelog logging* |
| `adif_wavelog_host` | str | `http://raspberrypi` | Wavelog's URL (placeholder) |
| `adif_wavelog_api_key` | str | `API-KEY` | Wavelog API key (placeholder) |

## [GUI]

Settings the server stores for the GUI. Changing them never restarts the modem.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `auto_run_browser` | bool | `True` | Open the GUI in the browser when the server starts. *Auto launch browser* |
| `distance_unit` | str | `km` | `km`, `mi` or `nm` for distances to heard stations. *Distance Unit* |

The language, colour mode, waterfall colours and dashboard layout are kept in the browser, not here.

## [FREQUENCIES]

Not in `config.ini.example`: on the first start without it, the server fills it from the user's old
`frequencies.json` next to the config, or from the default list.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `presets` | list | default list | The GUI's frequency presets: 10 slots, each `null` (empty) or `{"frequency": <Hz>, "band": "20m", "notes": "...", "schedule": null or {...}}`. Edited in the GUI's frequency list (`POST /radio/frequencies`), not through `POST /config/`; see [frequency_list.py](../frequency_list.py) for the schedule format |

## [EXP]

Experimental features (*Exp* tab).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enable_ring_buffer` | bool | `False` | Use a ring buffer for the received audio in the demodulator |
| `enable_vhf` | bool | `False` | Send commands (ping, CQ, beacon, test frame) in the `data_vhf_1` mode instead of `signalling` |
| `enable_groupchat` | bool | `False` | Broadcasts to a group (NORM): the Broadcast view and `POST /freedata/broadcasts` (409 when off). Both sending and receiving stations need it |

## Known issues

- Four settings are not used by the server: `MODEM.enable_morse_identifier`, `RIGCTLD.path`,
  `RIGCTLD.command` and `RADIO.serial_handshake`.
- `config.ini.example` has placeholder values from one particular computer: the audio device ids
  `5a1c` / `bd6c` (a fresh installation logs "Invalid input device index"), the macOS Bluetooth serial
  port, the Yaesu FT-847 model and a Wavelog `API-KEY`. Radio control and Wavelog are off by default,
  so only the audio warning is seen.
