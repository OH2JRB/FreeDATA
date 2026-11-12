# Testing

The tests live in [`tests/`](../../tests) at the repository root. They need no radio and no audio
hardware: modem traffic is simulated in-process by connecting two `AppContext` instances, one for
each station.

## 1. Running the tests

```bash
python -m venv .venv
.venv/bin/pip install -e ".[test]"        # Windows: .venv\Scripts\pip
```

CI ([modem_tests.yml](../../.github/workflows/modem_tests.yml)) runs the whole suite in one process
on Ubuntu with Python 3.12, 3.13, 3.14 and 3.15:

```bash
python -m unittest discover tests
```

pytest works too (`testpaths = ["tests"]` is set in `pyproject.toml`):

```bash
python -m pytest tests
```

A full run takes about a minute. Both commands exit non-zero when a test fails.

> Before the shutdown change described in [architecture.md](architecture.md#13-appcontextstartup--shutdown),
> `AppContext.shutdown()` called `os._exit(0)`, so the first `tearDownClass` ended the run with
> exit code 0 and CI never reported failures. Don't add a hard exit back into code that tests call.

## 2. Test files

| File | What it covers | Needs a running `AppContext` |
|---|---|---|
| `test_config.py` | `CONFIG` read, write and validation on a temporary copy of the template | no |
| `test_data_frame_factory.py` | Building beacon, ping, ARQ, CQ and burst frames; payload sizes; the SNR byte of the ARQ session frames | no (config only) |
| `test_data_type_handler.py` | raw, lzma and gzip handling in `ARQDataTypeHandler` | yes |
| `test_message_database.py` | Adding, deleting and updating messages, attachments, attempt counters | yes |
| `test_message_p2p.py` | `MessageP2P` from API params, payloads with attachments | yes |
| `test_protocols.py` | PING → PING_ACK and CQ → QRV through the real frame dispatcher | yes |
| `test_arq_stop.py` | The IRS sends `ARQ_STOP_ACK` in the signalling mode (`data_fsk4_50` on FSK), and the ISS reads it as a stop acknowledgement; a stopped (`ABORTED`) IRS answers INFO and burst frames with the `ABORT` flag | no (mock context) |
| `test_arq_session.py` | A full ARQ transfer (1000 random bytes) between two stations; IRS success event; the user's stop during a transfer (the ISS sends `ARQ_STOP`, both sides end `ABORTED` through the stop acknowledgement); the IRS aborting (abort flag in its ACK); 600 bytes with 30 % of the frames lost both ways (seeded per channel, the FSK fallback included); removing outdated ISS and IRS sessions in `check_if_running_arq_session` | two |
| `test_arq_final_ack.py` | A final burst ACK with `CHECKSUM` ends the ISS's session although it expected fewer bytes confirmed (a repeated final ACK after a resend); without `CHECKSUM` it fails | no (partial ISS) |
| `test_message_protocol.py` | A P2P message sent over an ARQ session, checked on both stations; the same with encryption on | two |
| `test_message_database_fixes.py` | One engine shared by all database managers, missing indexes added at startup, broadcast lookups returning snapshots, one update per ARQ result | yes |
| `test_message_send.py` | Two concurrent `SendMessageCommand.transmit()` calls for the same queued message (like the API and the scheduler) start only one ARQ session (`ARQSessionISS` mocked); a queued message passed to the command (as the scheduler does) is not added to the database again | yes (mocked modem) |
| `test_encryption.py` | AES-GCM helpers and key parsing, the encrypted ARQ data type, holding sent/received messages until a key is entered, the station encryption API, database migration from version 1 | yes |
| `test_common_key.py` | Common encryption key: station key precedence, `decrypt_any`, sending and receiving with the common key, decrypting pending messages of several stations, the common key API, `POST /config` switching the setting on | yes |
| `test_encryption_reporting.py` | Turning encryption on (per station or with the common key) switches Explorer and PSK Reporter off, keeps them locked off (409, config forced to `False`) until all encryption is off, and the startup check | yes |
| `test_norm_protocol.py` | NORM broadcasts between two test-mode stations, each with its own message database: the receiver assembles the bursts, checks the CRC and stores the broadcast as `received`; a lost burst is asked for by the receiver's scheduled `check_missing_broadcast_bursts` (clock two minutes ahead) and completed by the sender's `NORM_REPAIR` | two |
| `test_rigctld_connect.py` | rigctld connects in a background thread: creating the radio and polling it while not connected return at once, `disconnect()` ends the attempts, one connect at a time, connecting to a fake rigctld | no (mock context, local sockets) |
| `test_rigctld_ptt.py` | rigctld `set_ptt`: each `RADIO.ptt_mode` sends its `T` value and leaves rigctld connected, un-key sends `T 0`, an invalid mode falls back to `TX`, a socket error disconnects | no (mocked command channel) |
| `test_fsk_negotiation.py` | Both stations saying whether they have the FSK modes on (`CAPS`/`FSK` bits in the open frame and its ack), and no FSK fallback when the other station has them off; older stations without the bits as before | no |
| `test_session_snr.py` | The SNR the IRS chooses the speed level by: median of the last 3 readings, 0 dB counted, one stray reading ignored | no |
| `test_audio_renumbered.py` | A saved audio device found again under another ALSA card number (`find_renumbered_device`), and not guessed between two codecs | no |
| `test_test_frame.py` | Test frames sent in datac4 and handled by the receiving station (template, handler, event with SNR) | no |
| `test_p2p_fsk_negotiation.py` | P2P connections saying whether they have the FSK modes on (CONNECT_ACK and heartbeat flags), and no FSK fallback when the other station has them off | no |
| `test_snr_no_reading.py` | A frame without an SNR reading: `calculate_snr` rounds and gives `None` on error; ARQ sessions leave it out, the frame's other uses keep 0 | no |
| `test_fsk_snr.py` | The FSK modes' SNR converted to a 3 kHz noise bandwidth like the OFDM modes' (`codec2.snr_correction_db`; codec2 gives it in about the symbol rate, so `data_fsk4_50` read 17.8 dB and `data_vhf_1` 11.8 dB high), and `calculate_snr` reading the channel SNR through `tools/channel_sim.py` | no |
| `test_queue_busy_channel.py` | Queued messages on a busy channel: a channel kept busy only by non-FreeDATA signals holds the queue for `QUEUE_BUSY_MAX_WAIT` (60 s), then it sends; codec2 traffic and an ARQ session still hold it; a free moment restarts the wait; nothing is logged with an empty queue | no |
| `test_ptt_type.py` | `RADIO.ptt_type` values Hamlib does not know (`USB`, `MICDATA`) becoming `RIG`, in the config file when it is loaded and in rigctld's arguments; the template's default | no |
| `test_busy_detection_session.py` | No new busy traffic counted during an ARQ session or P2P connection (`calculate_fft`, `is_in_session`); marks from before it fade | no |
| `test_retry_without_modem.py` | ARQ and P2P retry threads when the modem is gone (stopped or restarting): the session ends through its failure path instead of the thread dying on `None.transmit` | no |
| `test_irs_resume.py` | An IRS session holding data resumes only the same data (`total_length`, `total_crc`); other data under the same session id starts over | no |
| `test_rigctld_commands.py` | rigctld commands from two threads against a fake rigctld that splits and delays its replies: each value read from its own reply, an unkey waiting for a slow poll and sent, polls giving way, a reply that does not end closing the connection, a pending unkey sent again | no |
| `test_radio_clock.py` | Setting the radio clock: Hamlib's time format with positive, negative, zero and half-hour offsets, the `RPRT` codes, `send_command(raw=True)`, the read-back, a radio without a clock, other backends, and `POST /radio/clock`'s status codes | no (mocked rigctld) |
| `test_radio_control_logging.py` | Radio control actions reach the log: `RadioManager.apply` at info/warning/error per outcome, un-key sent twice and logged once, the clock and tune actions, rigctld's `RPRT` check in every setter, poll errors logged once, flrig and serial PTT return values, and the entries landing in the log file set up by `setup_logging` (subprocess) | no (mocked backends) |
| `test_codec2_load.py` | libcodec2 that does not load, or is not found, stops the import with exit code 3 and the reason on stderr; a normal import works (each case in a new Python process) | no |
| `test_protocol_version_frames.py` | `CQ`, `QRV`, `BEACON` and `PING` carry the sender's protocol version in their former padding; an older station's frame reads `0`, an older decoder ignores the byte; `sender_protocol_version()` per frame type and on the received activity | no (mock context) |
| `test_websocket_send.py` | The WebSocket workers send on the server's event loop (a loop thread stands in), drop a failing client, send JSON and audio bytes, and do not serialize when no client is connected | no (fake clients) |
| `test_small_fixes.py` | flrig reports connected only when flrig answers and reads frequency and mode once per poll (fake XML-RPC server); codec2 CRC16 types and CRC-16/CCITT result; P2P connection data without a known session id reaches no connection | no (mock context, local XML-RPC server) |
| `test_check_callsign.py` | `helpers.check_callsign`: SSIDs from the list (ints or strings), the callsign's own SSID, callsigns without SSID, no match, and that the list passed in (the config's `ssid_list`) is not changed | no |
| `test_rx_audio_callback.py` | RX audio callback/worker split and re-blocking to whole DSP blocks | yes (not started) |
| `test_audio_backend.py` | Server import, device listing, audio test and modem start in a fresh interpreter where `import sounddevice` fails (no PortAudio / no sound server); loading PortAudio again once it is available | separate process |
| `test_api_fixes.py` | `GET /freedata/messages/{id}` (object / 404, called directly), broadcast DB helpers (read-only per-domain GET with `priority`, `mark_domain_as_read`, delete `not_found`), `Norm` priority coercion, `channel_busy` in state events, `POST /config` restarting only for non-GUI changes (fake ctx with its own config copy) | yes |
| `test_api_small_bugs.py` | GUI URL host for a server on all interfaces (`helpers.browsable_host`); `enqueue_tx_command` failing when `run()` returns `False`; `Norm.run()` results; OpenAPI error examples showing the `detail` body and 503 only where raised; the per-domain broadcast GET with and without the trailing slash (`TestClient`) | no (mock context) |
| `test_socket_data_port.py` | Which P2P session gets data from the TCP data port: the command socket's active session, else the only one, else none | no (mock context) |
| `test_pskreporter.py` | PSK Reporter datagram layout (templates compared with pskdev.html), deduplication and upload intervals, template resending, off at start, `GET`/`POST /modem/pskreporter` (called directly), the switch in the `/states` stream; a fake transport, no network traffic | yes |
| `test_server.py` | Starts the real server as a subprocess; `/`, `GET /config`, `POST /config`, 404 for unknown message id and broadcast delete, 409 for `POST /freedata/broadcasts` with group chat disabled, `PATCH /freedata/broadcasts/{domain}/read` | separate process |
| `test_demodulator_fsk.py` | The demodulator's FSK receiver restart: `data_vhf_1` bursts 500 ms apart decode without duplicates, the restart after transmitting (`reset_data_sync`), the audio history; runs one decoding thread on simulator audio | no (mock context) |
| `test_fading_channel.py` | The HF fading channel simulator in `tools/channel_sim.py` (Doppler spread, SNR, power, Doppler shift), decoding on a clean channel, one HF FSK candidate and FSK bursts with the receiver restart; the mode measurements themselves are in [fading-channels.md](fading-channels.md) | no |
| `test_high_rate_modes.py` | The higher code rates: payload, unique words and a clean loopback of `data_ofdm_250_hr`, `_500_hr` and `_1700_hr`; which speed level the IRS chooses; `HIGH_RATE_FLAG` in `ARQ_SESSION_OPEN`; the fallback to the lower code rate on both sides; the failures of a sibling mode left out; `MODEM.enable_high_rate` on by default ([arq-sessions.md](arq-sessions.md), section 4.2) | no |
| `test_p2p_connection.py` | P2P connections between two test-mode stations: connect and disconnect, payload frames both ways (signalling and datac4, the IRS sending after a heartbeat), 300 bytes in an ARQ session inside the connection, the IRS addressed by a `MYCALL` callsign that is not the station callsign; which `ARQ_SESSION_OPEN` destinations `is_frame_for_me` accepts; the socket interface is a mock | yes (two stations, own channel threads) |
| `test_session_ids.py` | ARQ sessions and P2P connections never get session id 35 (a burst ACK for it parses as a P2P payload ACK); a P2P connection's worker thread stops once disconnected | no (mock context) |
| `test_arq_abort_race.py` | A stop that arrives while a session answers the other station is not lost: the answer does not overwrite the ISS's `ABORTING` or the IRS's `ABORTED` | no (mock context) |
| `test_arq_fsk.py` | ARQ sessions falling back to `data_fsk4_50` (speed level 0): signalling after unanswered OFDM attempts, data after failed `datac4` bursts, both sides staying on it; the ISS's longer waits on FSK | no (mock context) |
| `test_arq_session_sim.py` | The real-time ARQ session simulator in `tools/arq_session_sim.py`: its audio bus, the streaming channel and one short session on a clean channel | no |
| `test_bandplan.py` | The bundled IARU band plan data and its lookup helpers | no |
| `test_encryption_logging.py` | Encryption events reach the server log (stdout and the log file), key material never does | no (subprocess) |
| `test_frequency_list.py` | The GUI's frequency list: loading the file, the fallback list and the API | yes (`TestClient`) |
| `test_fsk_early.py` | The early FSK decoder (`fsk_early.py`) passes FSK frames on right after their end, never before ([fading-channels.md](fading-channels.md), section 8) | no |
| `test_fsk_switch.py` | `MODEM.enable_fsk` through the config API: logged, saved to the config file, modem restarted | yes |
| `test_install_paths.py` | The bundled `lib/` and `data/` files found from an install, a Nuitka build and the source tree, whatever the working directory | no |
| `test_message_log.py` | `GET /freedata/messages/log`: completed messages as a CSV download, oldest first, nothing stored on the server | yes |
| `test_message_queue.py` | The message queue tab: the queue in sending order, deleting a queued message, `send_last` | yes |
| `test_norm_channel.py` | NORM traffic and the channel: no broadcast, resend, NACK or repair during an ARQ session or P2P connection; the random delay (0-15 s after a session); waiting for `channel_busy_event`, listening 0.5 s, up to 90 s while another FreeDATA station is heard (fake clock); repair log labels; the attempt limit; no `print()`; no modem | no (fake state) |
| `test_p2p_fsk.py` | P2P connections falling back to `data_fsk4_50` after two unanswered attempts of a frame, and when a frame arrives in FSK | no (mock context) |
| `test_request_guard.py` | `RequestGuard`: other web sites cannot use the API, the GUI and scripts can | no (`TestClient`, local socket) |
| `test_shutdown_guard.py` | The exit guard names threads that would keep the server process alive ([architecture.md](architecture.md), section 1.2) | no |
| `test_signal_detector.py` | `SignalDetector` finds transmissions by their power, and ARQ sessions use it where codec2 reports nothing (on `hl_disturbed` the OFDM modes do not sync) | no |
| `test_stop_all.py` | The Stop button (`stop_all`): every transmission stops, the transmit queues are emptied, queued and transmitting messages and broadcasts become `aborted`; no NORM burst after the stop | no (mock context) |

## 3. Test environment isolation

[`tests/testenv.py`](../../tests/testenv.py) creates a temporary directory per test process and:

- sets `FREEDATA_DATABASE` to a database file inside it (only if the variable isn't already set);
- provides `testenv.config_path()`, which returns a temporary copy of `config.ini.example`.

Every test module imports it **before** any `freedata_server` import and passes
`testenv.config_path()` to `AppContext` / `CONFIG`. Without it, tests would rewrite the tracked
`config.ini.example` and write messages into the user's real database in the FreeDATA app directory
(`%APPDATA%\FreeDATA` on Windows). The temporary directory is removed when the process exits, after
the shared database engines are closed (`dispose_engines`): Windows cannot delete an open SQLite file.
A test that opens a database itself with `sqlite3` has to close the connection
(`contextlib.closing`); the connection's own `with` block only commits.

`test_server.py` passes the same config to the server subprocess through `FREEDATA_CONFIG`, and the
subprocess inherits `FREEDATA_DATABASE`. It starts the server with `sys.executable`, so it uses the
interpreter running the tests rather than whatever `python3` is on `PATH`.

## 4. Test mode

There are two separate switches:

| Switch | Set by | Effect |
|---|---|---|
| `ctx.TESTMODE = True` (set before `ctx.startup()`) | the test | `start_modem()` skips audio devices and starts the demodulators with no stream; `modem.transmit()` puts `[mode, frames]` into `ctx.TESTMODE_TRANSMIT_QUEUE` instead of playing audio; every event is also copied into `ctx.TESTMODE_EVENTS`. |
| `frame_handler.TESTMODE = True` (module-level) | the test | `FrameHandler.transmit()` broadcasts the reply frame as an event instead of calling the modem. Only `test_protocols.py` uses it, and it resets it in `tearDownClass`, because it is global to the process. |

### Simulating the radio link

The two-station tests run two `channelWorker` threads. Each one takes frames from one station's
`TESTMODE_TRANSMIT_QUEUE`, appends 2 bytes to stand in for the CRC, optionally drops the frame
(`loss_probability`), and passes it to the other station's
`service_manager.frame_dispatcher.process_data(...)`.

### Reading events

Read events from **`ctx.TESTMODE_EVENTS`**, not from `ctx.modem_events` /
`event_manager.queues[0]`. The WebSocket manager's events worker also reads `modem_events`, so a
test reading that queue loses events to it at random.

Received frames show up as `{"type": "frame-handler", "received": "<FRAME_TYPE>", ...}`. The end of
an ARQ transfer shows up as `{"arq-transfer-outbound": {...}}` on the ISS and
`{"arq-transfer-inbound": {...}}` on the IRS, with `success` or `ABORTED` in the inner dict.

## 5. Writing tests

- Import `testenv` first and use `testenv.config_path()`.
- Set `ctx.TESTMODE = True` before `ctx.startup()` and call `ctx.shutdown()` in `tearDownClass`.
- Stop and `join()` every non-daemon thread the test starts. Register the cleanup with
  `self.addCleanup(...)` right after starting the threads, so it also runs when an assertion fails;
  otherwise the interpreter never exits.
- Wait with a timeout, and assert on the event you waited for. A wait loop that consumes the
  "session ended" event and then checks the rest of the queue can never fail.
- Avoid non-ASCII characters in `print()`. On Windows, redirected output uses cp1252 and raises
  `UnicodeEncodeError`.
- Handlers wait before replying: CQ waits a random 0–5 s before sending QRV, and messages wait for a
  free channel. Use timeouts of about 15 s for those exchanges.
- Two stations in one process share the message database (`FREEDATA_DATABASE`). When the test checks
  what each station stored, give each its own database file, as `test_norm_protocol.py` does by
  patching `DatabaseManager.get_database` per context.
- Group chat (NORM) is off in the default config: set `EXP.enable_groupchat` in each context's config
  after startup, since the service manager reads the config file again when it starts the modem.

## 6. Gaps

- The socket interface and radio backends are only partly covered (the TCP data port target, rigctld connecting, flrig connecting and polling, the set commands' results and logging); the command port has no tests, and the REST endpoints are tested only for `/`, `/config` and a few handlers called directly (`test_api_fixes.py`).
