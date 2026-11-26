# FreeDATA Server HTTP and WebSocket API

This page covers the FastAPI layer of `freedata_server`: how the app is put together, every REST and WebSocket endpoint, how endpoints start transmit commands, and the input validation rules.

Source files: [server.py](../server.py), [api/](../api/), [api_validations.py](../api_validations.py), [websocket_manager.py](../websocket_manager.py).

---

## 1. Application wiring

### 1.1 App creation and lifespan

[server.py](../server.py) works out the config file path (from the `FREEDATA_CONFIG` env var, falling back to the default app dir), sets up logging, then builds a single `AppContext` and calls `ctx.startup()` **at import time**, before the FastAPI app is created. The FastAPI `lifespan` hook does nothing on startup. On shutdown it calls `ctx.shutdown()` and then `os._exit(0)`.

```python
app = FastAPI(title="FreeDATA API", version=str(API_VERSION), lifespan=lifespan)
app.state.ctx = ctx  # used by get_ctx()
```

`main()` reads `NETWORK.modemaddress` (empty means `127.0.0.1`, this computer only) and `NETWORK.modemport` (default `5000`) from the config and runs the app with `shutdown_guard.Server` (uvicorn's server that does not take a repeated Ctrl-C for a forced exit, [architecture.md](architecture.md) section 1.2). When the address is not a loopback one (`helpers.is_loopback`), it logs a `[NETWORK]` warning: the API has no login, so anyone who can reach the port can control the radio.

### 1.2 Middleware

| Middleware | Behaviour |
|---|---|
| `RequestGuard` (`request_guard.py`, ASGI, runs first, HTTP and websockets) | Turns away other web sites, since the API has no authentication. **Host check:** the Host header's name must be an IP address, `localhost`, `*.localhost`, `*.local`, this computer's name, or listed in `NETWORK.allowed_hosts` (stops DNS rebinding). **Origin check:** if an `Origin` header is sent, its host must equal the Host header's name (any port, so the dev GUI on 8080 works) or be in `NETWORK.allowed_hosts`; `Origin: null` is refused. Requests without `Origin` (scripts, curl) pass. A refused HTTP request gets `403 {"error": "Forbidden: ..."}`, a refused websocket handshake 403 (close code 1008); both are logged as `[GUARD] Request rejected` with the name to add. `allowed_hosts` is read on every request. |
| `CORSMiddleware` | `allow_origins=["*"]`, `allow_credentials=False`, `allow_methods=["*"]`, `allow_headers=["*"]`. Only requests from allowed origins get this far. |
| `nocache` (HTTP) | Sets `Cache-Control: no-store, no-cache, must-revalidate, private` and `Pragma: no-cache` on every response, and logs `[API] METHOD path -> status`. |

### 1.3 Router mounting

| Router module | Prefix | Tag |
|---|---|---|
| [general.py](../api/general.py) | `""` | General |
| [config.py](../api/config.py) | `/config` | Configuration |
| [devices.py](../api/devices.py) | `/devices` | Devices |
| [radio.py](../api/radio.py) | `/radio` | Radio |
| [modem.py](../api/modem.py) | `/modem` | Modem |
| [freedata.py](../api/freedata.py) | `/freedata` | FreeDATA |
| [websocket.py](../api/websocket.py) | `""` | WebSocket |

Static GUI: when one of the candidate directories exists, it is mounted at `/gui` with `StaticFiles(html=True)`. The candidates are `<pkg>/gui`, `<pkg>/../freedata_gui/dist`, `../freedata_gui/dist`, `freedata_gui/dist` and `FreeDATA/freedata_gui/dist`. If `GUI.auto_run_browser` is true (the default), `main()` opens `http://host:port/gui` in a browser after 2 s.

Several routes are declared as `"/"` under a prefix (for example `/config/`, `/radio/`). FastAPI's default `redirect_slashes` behaviour applies to the version without the slash.

### 1.4 Dependency injection: `get_ctx`

Every handler takes `ctx: AppContext = Depends(get_ctx)`. `get_ctx` is defined in [context.py](../context.py):

```python
def get_ctx(request: Request = None, websocket: WebSocket = None) -> AppContext:
    conn = request or websocket
    return conn.app.state.ctx
```

It works for both HTTP and WebSocket routes and returns the one `AppContext` created at startup. Handlers use these attributes of it: `config_manager`, `state_manager`, `radio_manager`, `modem_service` (a queue taking `"start"`, `"stop"` and `"restart"`), `rf_modem`, `websocket_manager`, `constants`, and the queues `modem_events`, `state_queue`, `modem_fft` and `audio_rx_queue`.

### 1.5 Response and error helpers ([common.py](../api/common.py))

| Helper | Behaviour |
|---|---|
| `api_response(data, status=200)` | `JSONResponse(content=data, status_code=status)` |
| `api_ok(message="ok")` | `api_response({"message": message})` |
| `api_abort(message, code)` | `print(message)`, then `raise HTTPException(status_code=code, detail={"error": message})` |

**Error body format.** Because `api_abort` passes `detail={"error": ...}`, FastAPI's actual error body is:

```json
{ "detail": { "error": "Modem not running" } }
```

The OpenAPI `responses=` examples declared in the routers show this body too.

All request bodies are typed as a plain `dict`, so FastAPI does no schema validation. Each handler checks fields by hand, as described below. A body that is not a JSON object gives FastAPI's standard 422.

---

## 2. Endpoint reference

Status codes listed under "Errors" are the ones the handler code actually raises. Codes that appear only in the decorator's `responses=` docs are not listed.

### 2.1 General ([general.py](../api/general.py))

| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/` | API metadata | `{name, description, api_version, modem_version, license, documentation}` from `ctx.constants` |
| GET | `/version` | Versions plus host info | `{api_version, modem_version, os_info:{system,node,release,version,machine,processor}, python_info:{build,compiler,implementation,version}}` |

No error paths.

### 2.2 Configuration ([config.py](../api/config.py))

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/config/` | none | The whole `ctx.config_manager.config` dict (sections such as `AUDIO`, `MODEM`, `NETWORK`, `RADIO`, `RIGCTLD`, `STATION`, ...) | 404 `Configuration not found` if config is `None` |
| POST | `/config/` | The settings to change, by section (a partial config), or the whole config | The config as re-read after the write | 400 `Invalid configuration`, 500 `Error writing configuration` |

How `POST /config/` runs:
1. The body must be a dict of sections, each a dict of settings, or 400. A `FREQUENCIES` section is dropped: the presets are saved through `POST /radio/frequencies` only.
2. The body is merged into the stored config (`old_cfg = config_manager.read()`): sections and settings it does not name keep their stored values. Send only what changed: a client that sends its whole copy also writes back every value it read earlier, including what another client changed meanwhile. On 2026-11-20 a GUI tab opened before the call sign was changed put the old call sign back when the SSID was changed in it; the GUI now sends only the settings that differ from the config it last received (`changedSettings` in `settingsStore.js`).
3. `validate_remote_config(merged)` checks `STATION.mycall` and `STATION.mygrid` of the result (see §5) and **normalizes them**. Any failure gives 400. With the common key or any station's encryption on, Explorer reporting is kept off in the result and PSK Reporter is switched off.
4. If the result equals the stored config, it is returned unchanged and nothing restarts. Otherwise `config_manager.write(merged)` runs once; a falsy result gives 500.
5. The config is re-read. `ctx.modem_service.put("restart")` restarts the modem only if a section other than `GUI` differs between the old and the re-read config, leaving out `NO_RESTART_SETTINGS`. GUI-only changes (`GUI.distance_unit`, `GUI.auto_run_browser`) are written without a restart. So is `STATION.enable_log_to_temp`: switching it starts or stops the temp-directory log file at once (`log_handler.set_temp_logging`, a new file `freedata_server_<date>_<time>.log` each start) and sets the state's `temp_log_file`.
6. Sends the `config changed` event (§ events), so open GUI tabs show the change.
7. Returns the re-read config, the whole of it.

Minimal body, changing the call sign only:

```json
{ "STATION": { "mycall": "aa1aaa" } }
```

Validation rewrites these to `"AA1AAA"` and `"JN48ea"`.

### 2.3 Devices ([devices.py](../api/devices.py))

| Method | Path | Response | Errors |
|---|---|---|---|
| GET | `/devices/audio` | `{"in": [...], "out": [...]}` from `audio.fetch_audio_devices([], [])`. Each entry looks like `{"api","id","name","native_index"}` | 503 `Error fetching audio devices: <exc>` |
| GET | `/devices/serial` | List from `serial_ports.get_ports()`, for example `[{"description": "n/a [26a9]", "port": "/dev/ttyS4"}]` | 503 `Error fetching serial ports: <exc>` |

### 2.4 Radio ([radio.py](../api/radio.py))

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/radio/` | none | `state_manager.get_radio_status()`: `{radio_status, radio_frequency, radio_mode, radio_rf_level, s_meter_strength, radio_swr, radio_tuner}` | 404 `Radio parameters not found` if `None` (the method always returns a dict) |
| POST | `/radio/` | Any subset of `radio_frequency`, `radio_mode`, `radio_rf_level`, `radio_tuner` | The request body, echoed back | 503 `Radio manager not running` if `ctx.radio_manager` is `None`. `int(radio_rf_level)` raising gives an unhandled 500 |
| POST | `/radio/tune` | `{"enable_tuning": bool}` | The request body, echoed back | 503 `Modem not running` (only when enabling); 500 `Failed to start tuning tone.` if the command raises |
| GET | `/radio/frequencies` | none | `{"slots": [{"frequency": 14093000, "band": "20m", "notes": "EU / US", "schedule": null}, null, ...], "frequencies": [{..., "slot": 0, "schedule_state": null}, ...], "slot_count": 10}`; `slots` has all slots, `null` for an empty one; `frequencies` the filled ones with their slot index and `schedule_state` (`{"state": "active"/"upcoming"/"ended"/"idle", "start", "end"}`, null without a schedule) | none; an invalid config value gives empty slots (logged); see [modem-audio-radio.md](modem-audio-radio.md#frequency-list-frequency_listpy) |
| POST | `/radio/frequencies` | `{"slots": [...]}`: at most 10, each a preset or `null`; a shorter list is filled up with empty slots | As GET, after writing `FREQUENCIES.presets`; an empty `band` is filled in from the IARU band plan | 400 with the reason if a slot is not valid (nothing written); 500 config not written |
| POST | `/radio/frequencies/reset` | none | As GET, after writing the shipped default list | 500 config not written |
| POST | `/radio/clock` | none | `{"set_to": "2026-10-28T14:05:30+0200", "radio_clock": "2026-10-28T14:05:30.000+02:00"}` | 503 `Radio manager not running`; 409 radio not connected, or radio control not rigctld; 422 the radio's Hamlib backend cannot set its clock; 500 other rigctld errors |

`POST /radio/` calls the matching `radio_manager.set_frequency`, `set_mode`, `set_rf_level(int(...))` or `set_tuner` for each key that is present. Values are not otherwise validated. `ctx.radio_manager` is only created by the service manager when the modem starts (it is `None` before that and after a stop), hence the 503. Each change is logged by the radio manager (`[RADIO] Radio control applied` / `failed` / `not supported`, see [modem-audio-radio.md](modem-audio-radio.md#logging-radio-control)).

`POST /radio/tune`: a truthy `enable_tuning` checks that the modem is running, then awaits `enqueue_tx_command(ctx, TransmitSine)` and aborts 500 if it returns `False`. A falsy value calls `ctx.rf_modem.stop_sine()`. The route logs `[RADIO] Radio control applied` with `action=tune` and `enable`, or `Radio control failed` at warning before a 503/500.

`POST /radio/clock` sets the radio's date and clock to the server's local time and UTC offset (`radio_manager.set_clock()`, run in a thread since rigctld blocks on its socket), only when called: nothing sets the clock on its own. The GUI's radio control widget calls it from a "Set clock" button after the user confirms. `radio_clock` is what `\get_clock` reads back, or `null`.

### 2.5 Modem ([modem.py](../api/modem.py))

| Method | Path | Body | Success response | Errors (actually raised) | Command class |
|---|---|---|---|---|---|
| GET | `/modem/state` | none | `state_manager.get_state_event(False)`, see below | 404 if `None` | none |
| POST | `/modem/cqcqcq` | none | `{"message":"ok"}` | 503 `Modem not running`; 500 `Failed to send CQ.` | `command_cq.CQCommand` |
| POST | `/modem/beacon` | `{"enabled": bool, "away_from_key": bool}` | `{"enabled": bool, "away_from_key": bool}` | 400 `Expected booleans for 'enabled' and 'away_from_key'`; 503 `Modem not running`; 500 `Failed to send beacon.` | `command_beacon.BeaconCommand` (only when `enabled` is true and no ARQ session is active) |
| GET | `/modem/pskreporter` | none | `{"enabled": bool, "pending_spots": int, "blocked_by_encryption": bool}` | none | none |
| POST | `/modem/pskreporter` | `{"enabled": bool}` | same as GET | 400 `Expected a boolean for 'enabled'`; 409 when switching on while encryption is in use | none, calls `ctx.pskreporter.set_enabled()` |
| POST | `/modem/ping_ping` | `{"dxcall": str}` | `{"message": true}` | 503 `Modem not running`; 400 `Invalid 'dxcall' parameter.`; 500 `Failed to send ping.` | `command_ping.PingCommand` |
| POST | `/modem/send_test_frame` | none | `{"message":"ok"}` | 503 `Modem not running`; 500 `Failed to send test frame.` | `command_test.TestCommand` |
| POST | `/modem/fec_transmit` | ignored | never succeeds | always 501 `FEC transmission is not implemented.` (see note) | none |
| POST | `/modem/start` | none | `{"modem":"started"}` | 503 `Modem already running` | puts `"start"` on `modem_service` |
| POST | `/modem/stop` | none | `{"modem":"stopped"}` | none (the running check is commented out) | puts `"stop"` on `modem_service` |
| POST | `/modem/send_arq_raw` | `{"dxcall": str, "type": "raw"\|"raw_lzma"\|"raw_gzip", "data": base64 str}` | `{"data", "dxcall", "type"}` echoed back | 503 `Modem not running.`; 503 `Modem Busy.` (ARQ active); 400 `Invalid 'dxcall' parameter.` / `Invalid 'type' parameter.` / `Missing 'data' parameter.`; 500 `Failed to start ARQ raw transmission.` | `command_arq_raw.ARQRawCommand` |
| POST | `/modem/stop_transmission` | none | `{"message":"ok"}` | 500 `Stop failed: ...` | `stop_all.stop_all(ctx)` in a worker thread: stops the beacon, P2P connections, ARQ sessions and what is on air, and sets queued and transmitting messages and broadcasts to `aborted` (see [message-system.md](message-system.md), "Stop button") |

**`GET /modem/state` response.** `get_state_event(False)`, a full `state` snapshot, returned to the caller only; nothing is sent to the `/states` WebSocket clients. The GUI calls it on every (re)connect of its `/states` socket (`loadAllData`) and applies the answer with the same `stateDispatcher` that handles pushed states.

```json
{
  "type": "state",
  "is_modem_running": true,
  "is_beacon_running": false,
  "is_away_from_key": false,
  "is_pskreporter_enabled": false,
  "radio_status": true,
  "channel_busy": false,
  "channel_busy_slot": [false, false, true, false, false],
  "is_codec2_traffic": false,
  "audio_dbfs": -7.9,
  "activities": { "<id>": { "activity_type": "BEACON", "direction": "received", "...": "..." } },
  "is_modem_busy": false,
  "temp_log_file": ""
}
```

**Beacon.** The handler sets `is_beacon_running` and `is_away_from_key` via `state_manager.set(...)`, which pushes a `state-change` frame to `/states`. `BeaconCommand` gets the payload but does not use it. The beacon frame reads `state_manager.is_away_from_key` instead.

**PSK Reporter.** The switch is runtime only: it is off after every server start and is not stored in the config. It works whether or not the modem is running. See [pskreporter.md](pskreporter.md).

**FEC.** FEC transmission is not implemented. The `FEC` / `FEC_WAKEUP` frame types are disabled in [modem_frametypes.py](../modem_frametypes.py), and there is no FEC command class (the old, non-working `command_fec.py` was removed). The handler therefore aborts with 501 straight away and does not check the modem state or the body.

**500 on command failure.** Every endpoint that runs a transmit command through `enqueue_tx_command` (see §4) aborts with 500 when that call returns `False`, which happens when the command's constructor (parameter parsing) or its `run()` raised, or `run()` returned `False`. `ARQRawCommand.run()` and `Norm.run()` catch their own exceptions and return `False`, so their failures give 500 as well.

**ARQ raw.** `type` is checked against the three literal strings above. `ARQRawCommand` maps it through `ARQ_SESSION_TYPES[...]`, falls back to `raw`, then base64-decodes `data` ([command_arq_raw.py](../command_arq_raw.py)). Example:

```json
{ "dxcall": "XX1XXX-6", "type": "raw", "data": "RnJlZURBVEEgaXMgdGhlIGJlc3Qh" }
```

### 2.6 FreeDATA messaging, beacons, stations, broadcasts ([freedata.py](../api/freedata.py))

Each request builds a new DB manager instance (`DatabaseManagerMessages`, `...Attachments`, `...Beacon`, `...Stations` or `...Broadcasts`) from `ctx`. None of these routes check whether the modem is running, and none documents a 503.

#### Messages (P2P)

| Method | Path | Body / params | Response | Errors |
|---|---|---|---|---|
| GET | `/freedata/messages` | none (query params are ignored) | `get_all_messages_json(filters=config["FILTERS"])`, for example `{"total_messages": N, "messages": [...]}` | none |
| POST | `/freedata/messages` | `{"destination": str, "body": str, "attachments"?: [ {name,type,data} ], "id"?: str}` | The request body, echoed back | 500 `Failed to transmit message.` if the command raises |
| GET | `/freedata/messages/{message_id}` | path `message_id: str` | The message as a JSON object (`get_message_by_id(...)`, i.e. `P2PMessage.to_dict()`) | 404 `Message not found` |
| PATCH | `/freedata/messages/{message_id}` | `{"action": "retransmit"}` or any dict of fields to update | Retransmit sets `status` to `queued` and `priority` back to 10 (undoing a send last), increments `attempts` and returns `{"message_id", "status": "queued"}`. Otherwise returns the `update_message` result `{"status": "success", "message": ...}` | 404 `Message not found` (checked with `get_message_by_id` before updating); 500 `Error updating message` if `update_message` does not report `success` |
| DELETE | `/freedata/messages/{message_id}` | none | `{"message": "<id> deleted", "status": "success"}` | 404 `Message not found`; 409 with the manager's failure message for any other failure (for example `Message is queued or transmitting`) |
| GET | `/freedata/queue` | none | `{"total_messages": n, "messages": [...]}`: the `transmitting` message first, then the `queued` ones in sending order; each with `position` (from 1), `id`, `timestamp`, `destination`, `body`, `attachments` (`name` and `type` only), `status`, `attempt`, `priority`, `encrypted` | none |
| DELETE | `/freedata/queue/{message_id}` | none | `{"message": "<id> deleted", "status": "success"}` | 409 `Message is being sent` while `SendMessageCommand` has claimed it; 404 `Message not found`; 409 `Message is queued or transmitting` for a transmitting message |
| PATCH | `/freedata/queue/{message_id}` | `{"action": "send_last"}` | `{"message_id", "status": "queued", "position": "last"}` | 400 `Invalid action`; 404 `Message not found`; 409 `Only a queued message can be moved` |
| GET | `/freedata/messages/log` | none | `text/csv` download (`Content-Disposition: attachment`, `X-Total-Messages`): `message_log_csv()` (in a worker thread), the successfully completed messages (`transmitted` sent, `received` received), oldest first; nothing is stored on the server (see [message-system.md](message-system.md), "Message log") | 500 `Could not make the message log: ...` |
| POST | `/freedata/messages/{message_id}/adif` | none | `{"adif_output": <adif string>}` | 404 `Message not found or ADIF unavailable` |
| GET | `/freedata/messages/{message_id}/attachments` | none | `{"attachments": [...]}` (an empty list if the message is unknown) | none |
| GET | `/freedata/messages/attachment/{data_sha512}` | none | Attachment record from `get_attachment_by_sha512` | 404 `Attachment not found` |

`POST /freedata/messages` runs `command_message_send.SendMessageCommand`. Its `set_params_from_api` builds the origin as `"{STATION.mycall}-{STATION.myssid}"` and calls `MessageP2P.from_api_params`, which does the following ([message_p2p.py](../message_p2p.py)):
- appends `-0` to `destination` if it fails `validate_freedata_callsign`, then raises `ValueError` if it still fails;
- runs `validate_message_attachment` on each attachment and base64-decodes `data`;
- generates `id = "{origin}_{destination}_{utc iso timestamp}"` when `id` is absent.

The message is stored with `status="queued"`, `direction="transmit"`. All of this runs in the command's constructor inside `enqueue_tx_command`. A validation failure (an invalid destination or attachment) makes it return `False`, and the endpoint aborts with 500 `Failed to transmit message.` Errors inside `SendMessageCommand.transmit()` are caught and logged by the command and do not change the response.

```json
{
  "destination": "XX1XXX-6",
  "body": "Hello FreeDATA",
  "attachments": [ { "name": "a.txt", "type": "text/plain", "data": "SGVsbG8=" } ]
}
```

`POST .../adif` sends the ADIF record through both `adif_udp_logger.send_adif_qso_data` and `wavelog_api_logger.send_wavelog_qso_data`.

#### Beacons and stations

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/freedata/beacons` | none | `get_all_beacons()` | none |
| GET | `/freedata/beacons/{callsign}` | none | `get_beacons_by_callsign(callsign)` | none |
| GET | `/freedata/station/{callsign}` | none | `get_station(callsign)` | 404 `Station not found` |
| POST | `/freedata/station/{callsign}` | `{"info": { ... }}` (must be a dict) | `true` (unknown stations are created by `update_station_info`) | 400 `Invalid input data`; 500 `Error updating station info` if the database update fails |
| GET | `/freedata/station/{callsign}/encryption` | none | encryption status of the station | 400 invalid callsign |
| PATCH | `/freedata/station/{callsign}/encryption` | `{"enabled": bool}` | encryption status | 400 `'enabled' must be true or false`; 500 database error |
| PUT | `/freedata/station/{callsign}/encryption/key` | `{"key": str}` | encryption status plus `decrypted`, `requeued` | 400 invalid key / key doesn't decrypt the waiting messages |
| DELETE | `/freedata/station/{callsign}/encryption/key` | none | encryption status | 400 invalid callsign |
| GET | `/freedata/encryption/common-key` | none | `{"enabled", "key_loaded", "fingerprint"}` | none |
| PUT | `/freedata/encryption/common-key` | `{"key": str}` | common key status plus `decrypted`, `requeued` | 400 invalid key |
| DELETE | `/freedata/encryption/common-key` | none | common key status | none |

The encryption endpoints are described in [encryption.md](encryption.md#api).

#### Broadcasts (NORM / group chat)

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/freedata/broadcasts` | none | `get_all_broadcasts_json()` | none |
| GET | `/freedata/broadcasts/domains` | none | `get_broadcast_domains_json()` | none |
| GET | `/freedata/broadcasts/{domain}/` (also without the slash, except for a domain named `domains`) | none | `get_broadcasts_per_domain_json(domain)`: `{domain: [ {id, timestamp, origin, gridsquare, msg_type, priority, payload_size, payload_data, direction, status, error_reason, received_at, nexttransmission_at, expires_at} ]}`. Read-only | none |
| PATCH | `/freedata/broadcasts/{domain}/read` | none | `mark_domain_as_read(domain)`: `{"domain", "marked_read": <count>, "status": "success"}`. Sends a `message-db` event if anything changed | none (an unknown domain gives `marked_read: 0`) |
| POST | `/freedata/broadcasts` | `{"origin", "domain", "data" (base64), "type", "gridsquare", "priority"?}` | `{"message": "broadcast transmitted", "status": "success"}` | 409 `Group chat is disabled (EXP.enable_groupchat), broadcast not transmitted.`; 500 `Failed to transmit broadcast.` if the command raises (for example on a missing key) |
| PATCH | `/freedata/broadcasts/{id}` | `{"action": "retransmit"}` | `{"message_id": id, "status": "retransmit started"}` | 404 `Message not found`; 400 `Invalid action` for any other action |
| DELETE | `/freedata/broadcasts/{id}` | none | `{"message": "<id> deleted", "status": "success"}`. `id` is tried as a message id first, then as a domain | 404 `Broadcast message or domain not found`; 500 with the error message on a database error |

`POST /freedata/broadcasts` runs `command_norm.Norm` ([command_norm.py](../command_norm.py)):
- `origin` and `domain` get `-0` appended if they fail callsign validation;
- `data` is base64-decoded and **truncated to 390 bytes** (`15 * 26`);
- `priority` is coerced to `int`; it defaults to `1` and also falls back to `1` when it is not a number or outside 0-7 (it is a 3-bit field in the burst flags);
- `origin`, `domain`, `data`, `type` and `gridsquare` are required. A missing key raises in the constructor, so the endpoint answers 500;
- `Norm.run()` returns `False` without transmitting when `EXP.enable_groupchat` is false. Otherwise it starts `NormTransmissionISS.prepare_and_transmit_data` in a thread and returns `True`. `run()` catches its own exceptions and returns `False`.

The endpoint checks `EXP.enable_groupchat` itself before building the command and answers 409 when group chat is disabled; otherwise `run()` returning `False` would give a 500.

The GET per domain does not change anything. Clients mark a domain read explicitly with `PATCH /freedata/broadcasts/{domain}/read` (the GUI does this when the user opens a domain), so background reloads do not clear unread counts.

`PATCH /freedata/broadcasts/{id}` first checks that the broadcast exists (404 if not), then calls `increment_attempts(id)`. It then runs `NormTransmissionISS(ctx).retransmit_data(msg)` through `loop.run_in_executor` without awaiting it.

---

## 3. WebSocket endpoints ([websocket.py](../api/websocket.py), [websocket_manager.py](../websocket_manager.py))

Each route calls `websocket.accept()` and then `ctx.websocket_manager.handle_connection(ws, client_list, queue)`. `handle_connection` adds the socket to `client_list` and loops on `receive_text()`, **ignoring whatever the client sends**. It removes the client when the receive fails. The `queue` argument is unused, because sending is done separately by worker threads that `startWorkerThreads` starts during `ctx.startup()`.

| Path | Client set | Queue that feeds it (worker) | Frame type |
|---|---|---|---|
| `/events` | `events_client_list` | `ctx.modem_events` (`transmit_sock_data_worker`) | text, `json.dumps(event)` |
| `/states` | `states_client_list` | `ctx.state_queue` (`transmit_sock_data_worker`) | text, JSON |
| `/fft` | `fft_client_list` | `ctx.modem_fft` (`transmit_sock_data_worker`) | text, JSON |
| `/audio_rx` | `audio_rx_client_list` | `ctx.audio_rx_queue` (`transmit_sock_audio_worker`) | **binary**, `ndarray.tobytes()` |

The worker threads block on `queue.get(timeout=1)`. With no client connected they only take the item off the queue (no JSON or bytes conversion). Otherwise `send_to_clients()` schedules `client.send_text(...)` (or `send_bytes`) for each client of a copy of the set on the server's event loop, which `handle_connection` records (`asyncio.run_coroutine_threadsafe`), and waits for it. A client whose send fails or takes over `SEND_TIMEOUT` (5 s) is dropped and its websocket closed (`close_client`), so the browser reconnects. A background tab or a sleeping computer can hold up a send that long; before, the client was only dropped, its socket stayed open with no more data, and the waterfall froze. Falsy events are skipped. The audio worker only sends items that are `np.ndarray`. Each send used to start a new event loop (`asyncio.run`), about 0.44 ms per message and client against about 0.04 ms now.

### 3.1 `/events` messages

These are produced by `EventManager.broadcast()` ([event_manager.py](../event_manager.py)). When a queue holds more than 10 items, `broadcast()` drops the oldest ones (`get_nowait()`) until 10 remain, then adds the new event. Slow consumers can therefore miss older events. Shapes:

| Event | JSON |
|---|---|
| PTT | `{"ptt": true}` (sent only when the value changes) |
| Scatter | `{"scatter": "<json string>"}` |
| Buffer overflow | `{"buffer-overflow": "<str>"}` |
| Modem lifecycle | `{"freedata_server": "started" \| "restarted" \| "stopped" \| "failed"}` |
| Config changed | `{"type": "config", "config": "changed"}` after `POST /config/` wrote a change (not for a save that changed nothing). Open GUI tabs reload the config and take over the settings they have not changed themselves (`refreshRemote` / `mergeRemoteConfig` in `settingsStore.js`) |
| Message DB change | `{"message-db": "changed", "message_id": <id or null>}` |
| Logging | `{"type": "message-logging", "endpoint", "status", "message"}` |
| ARQ new / progress / finished | `{"type": "arq", "arq-transfer-outbound"\|"arq-transfer-inbound": {session_id, dxcall, ...}}`. *new* carries `total_bytes, state`. *progress* carries `received_bytes, total_bytes, state, speed_level, mode, statistics` (`mode` is the data mode of `speed_level` as a FREEDV_MODE name, such as `datac4` or `data_fsk4_50`); a *progress* event identical to the session's last one (all fields but `statistics`) is not sent, so a client sees each change once. *finished* carries `statistics, success, state, data` (no top-level `received_bytes`/`total_bytes`; the byte count is `statistics.total_bytes`) |
| Custom | arbitrary kwargs passed to `send_custom_event(**data)` |

### 3.2 `/states` messages

Produced by `StateManager` ([state_manager.py](../state_manager.py)):

- `{"type": "state", ...}`: a full state snapshot, as `GET /modem/state` returns it (`get_state_event(False)`); the stream itself sends only the changes below.
- `{"type": "state-change", ...}`: the same fields, sent by `set(key, value)` and `set_channel_slot_busy()` when the snapshot differs from the last one. Fields: `is_modem_running, is_beacon_running, is_away_from_key, is_pskreporter_enabled, radio_status, channel_busy, channel_busy_slot, is_codec2_traffic, audio_dbfs, activities, is_modem_busy, rx_audio_loss, temp_log_file` (`rx_audio_loss` is `{driver, queue, decoder}`: RX audio blocks lost since the modem started; `temp_log_file` is the path of the temp-directory log file being written, `""` when off; the GUI footer shows it). `channel_busy` is `true` while the channel is busy (`not channel_busy_event.is_set()`; the event is set while the channel is free).
- `{"type": "radio-change", radio_status, radio_frequency, radio_rf_level, radio_mode, s_meter_strength, radio_swr, radio_tuner}`, sent by `set_radio()` when the value changes. `get_radio_event` can also build `type: "radio"`.

### 3.3 `/fft` messages

`audio.calculate_fft()` ([audio.py](../audio.py)) empties `modem_fft` and puts the first 315 integer FFT bins on it. Each frame is a JSON array, for example `[12, 9, 30, ...]`.

### 3.4 `/audio_rx` messages

The received radio audio, for listening in the browser (the GUI's Audio Stream widget). Each binary frame is 800 samples (100 ms) of 8 kHz, 16-bit signed little-endian, mono PCM: 1600 bytes, 10 frames a second, 128 kbit/s. It is the audio the modem demodulates, resampled from the sound card's 48 kHz, before the auto level and the `rx_audio_level` gain, so its loudness is the radio's USB audio level.

`modem.py` puts the chunks on `audio_rx_queue` (`Queue(maxsize=10)`, 1 s) via `enqueue_streaming_audio_chunks`, in blocks of `AUDIO_STREAMING_CHUNK_SIZE` (800) samples, with `put_nowait`, so blocks are dropped while the queue is full. **Nothing is sent while transmitting** (`state_manager.isTransmitting()`), as for `/fft`: the stream pauses for the TX and resumes after it.

The GUI opens this socket only while the user listens (`openAudioStream` in `event_sock.js`), so with nobody listening the worker only takes the blocks off the queue. Like the other routes it has no authentication; `RequestGuard` applies, and anyone who can reach the API can listen. For remote listening use an SSH tunnel or a VPN rather than opening the API to the network.

---

## 4. How API calls become transmit commands ([command_helpers.py](../api/command_helpers.py))

```python
async def enqueue_tx_command(ctx, cmd_class, params=None) -> bool:
    command = cmd_class(ctx, params or {})  # TxCommand.__init__ -> set_params_from_api(params)
    await asyncio.to_thread(command.run)  # blocking run() off the event loop
    return True  # any exception -> logged, returns False
```

Despite the name, nothing is queued. The handler `await`s `command.run()` in a worker thread, so the HTTP response waits until `run()` returns. Parameter parsing happens in the constructor (`TxCommand.__init__` calls `set_params_from_api`), inside the same `try`. Parse errors and run errors are both caught there.

`enqueue_tx_command` returns `False` if the constructor or `run()` raised, or if `run()` returned `False`; otherwise `True` (most commands return `None` on success). Every calling handler checks the result and aborts with 500 on `False` (see §2). `ARQRawCommand` and `Norm` catch their own errors inside `run()` and return `False`. When the channel gate refused the transmission (`TxCommand.not_sent_reason`), `enqueue_tx_command` answers itself instead: 503 "Not sent: the channel stayed busy with another FreeDATA station's traffic." (`channel_gate.DROPPED`) or 409 "Not sent: stopped while waiting for a clear channel." (`STOPPED`). Nothing was sent, on purpose, so it is not a 500; the GUI shows 409 and 503 as a "Not sent" warning, not a server error (`showApiError` in `api.js`).

`start_tx_command` is the background variant, used by `POST /freedata/messages`: it creates the command in a worker thread (the message is stored as `queued` then) and returns `True` at once, running `run()` in a daemon thread whose errors are logged. It returns `False`, and the handler answers 500, only if the command could not be created.

Base class `TxCommand` ([command.py](../command.py)): `run()` calls `emit_event()`, logs, waits for a clear channel (`wait_for_clear_channel`, listen before talk, [protocol-overview.md](protocol-overview.md#listen-before-talk); not with `LISTEN_BEFORE_TALK = False`), then `transmit()`; if the gate drops the transmission, `run()` returns `False` with `not_sent_reason` set, and the call answers 503 or 409 (above). `transmit()` calls `build_frame()` and `ctx.rf_modem.transmit(get_tx_mode(), 1, 0, frame)`. `get_tx_mode()` returns `FREEDV_MODE.data_vhf_1` when `EXP.enable_vhf` is set, otherwise `FREEDV_MODE.signalling`.

| Endpoint | Command class | Params consumed by the command |
|---|---|---|
| `POST /modem/cqcqcq` | [command_cq.py](../command_cq.py) `CQCommand` | none |
| `POST /modem/beacon` | [command_beacon.py](../command_beacon.py) `BeaconCommand` | none (uses `state_manager.is_away_from_key`) |
| `POST /modem/ping_ping` | [command_ping.py](../command_ping.py) `PingCommand` | `dxcall` (appends `-0` if invalid, creates the station DB entry) |
| `POST /modem/send_test_frame` | [command_test.py](../command_test.py) `TestCommand` | none |
| `POST /modem/send_arq_raw` | [command_arq_raw.py](../command_arq_raw.py) `ARQRawCommand` | `dxcall`, `type`, `data` (base64). Overrides `run()`: waits for a clear channel, then starts an `ARQSessionISS` whose first open goes out at once |
| `POST /radio/tune` (enable) | [command_transmit_sine.py](../command_transmit_sine.py) `TransmitSine` | none |
| `POST /freedata/messages` | [command_message_send.py](../command_message_send.py) `SendMessageCommand` | `destination`, `body`, `attachments`, `id`. Through `start_tx_command`: answers once the message is stored, then sends in the background. `run()` does not gate; `transmit()` waits for a clear channel right before the session, and leaves the message queued if the gate drops it |
| `POST /freedata/broadcasts` | [command_norm.py](../command_norm.py) `Norm` | `origin`, `domain`, `data`, `type`, `gridsquare`, `priority` |

Because the commands wait for a clear channel before sending, these calls answer only after the wait: usually `listen_time` plus the random delay (2-5 s), on a busy channel up to `freedata_max_wait` (60 s; a beacon 10 s) or `other_busy_wait` (30 s) more. Their answer says whether the transmission went out (200) or not (503, 409). A client with a short timeout (`curl -m 30`, say) can give up while the server still sends; on the air on 2026-11-20 such a client saw no answer, but the ping went out. `POST /freedata/messages` does not wait (it answers once the message is stored), and neither does `POST /radio/tune`.

---

## 5. Validation rules ([api_validations.py](../api_validations.py))

| Function | Rule | Returns |
|---|---|---|
| `validate_gridsquare(value)` | Must be a non-empty `str`. After `strip()`, must match `^[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?$` (4- or 6-char Maidenhead) | Normalized locator: field letters upper case, subsquare lower case (for example `jn48EA` becomes `JN48ea`), or `None` |
| `validate_freedata_callsign(value)` | Must be a non-empty `str`. After `strip().upper()`, must fully match `^[A-Z0-9]{1,7}(-[0-9]{1,3})?$` (case-insensitive). If an SSID is present, it must be in 0-255 | Normalized `BASE-SSID` with the SSID as an int (`"ab1cd-007"` becomes `"AB1CD-7"`), the bare base if there is no SSID, or `None` |
| `validate_remote_config(config)` | `config` must contain `STATION`. `STATION.mycall` must pass the callsign check and `STATION.mygrid` the grid check | `True` or `False`. On success it **writes the normalized values back** into `config["STATION"]` |
| `validate_message_attachment(attachment)` | Keys `name`, `type` and `data` must all exist. `name` and `data` must have `len >= 1`. `type` may be empty | Nothing. Raises `ValueError("Attachment missing '<f>'")` or `ValueError("Attachment has empty '<f>'")` |

Where they are used in the API layer:
- `validate_remote_config`: `POST /config/`.
- `validate_freedata_callsign`: `POST /modem/ping_ping` and `POST /modem/send_arq_raw` (400 on failure). Also used in the command classes (`PingCommand`, `ARQRawCommand`, `Norm`, `MessageP2P.from_api_params`), which append `-0` and retry instead of rejecting.
- `validate_message_attachment`: called by `MessageP2P.from_api_params` for each attachment.

The module docstring says the callsign regex "may require adjustment" for SSID 0-255. The current code handles that range with the regex (1-3 digits) plus the explicit range check.

---

## 6. Known quirks in the API layer

- `POST /modem/fec_transmit` always returns 501, because FEC transmission is not implemented.
