# UML views

Four views of FreeDATA as it is built: its components, the server's core classes, the protocol class
hierarchies and the message database. The class diagrams show structure, not every field or method; the
details are in [architecture.md](architecture.md), [protocol-frames.md](protocol-frames.md),
[arq-sessions.md](arq-sessions.md) and [message-system.md](message-system.md).

## Components

The GUI and other clients reach the server over REST, WebSocket and the TCP socket interface. The server
drives the sound card and the radio's control interface, and stores messages in SQLite.

```mermaid
flowchart LR
  subgraph GUI["freedata_gui (Vue 3 + Vite)"]
    C["components (.vue)"] --> S["Pinia stores"]
    C --> J["js handlers: api, eventHandler"]
    J --> S
  end
  subgraph EXT["External clients"]
    W["Winlink / VARA-style apps"]
  end
  subgraph SRV["freedata_server (FastAPI)"]
    API["api routers"] --> CTX["AppContext"]
    WS["WebSocket streams"] --> CTX
    SOCK["socket interface :9000 / :9001"] --> CTX
    CTX --> PROTO["ARQ, P2P, NORM sessions"]
    CTX --> MODEM["RF modem: modulator, demodulator"]
    CTX --> RADIO["RadioManager"]
    PROTO --> DB[("SQLite message DB")]
  end
  J -- "REST" --> API
  J -- "WebSocket" --> WS
  W -- "TCP" --> SOCK
  MODEM <--> AUDIO(["sound card"])
  RADIO <--> RIG(["rigctld, flrig, serial PTT"])
  AUDIO <--> TRX(["transceiver"])
  RIG <--> TRX
```

The GUI's Vue components, Pinia stores and JS handlers are modules, not classes, so they appear here only
as components.

## Server core

One `AppContext` ([context.py](../context.py)) holds the config and the managers. The service manager
(class `SM` in [service_manager.py](../service_manager.py)) creates the RF modem, the frame dispatcher and
the radio manager when the modem starts, and drops them when it stops.

```mermaid
classDiagram
  direction LR
  class AppContext {
    config_manager: CONFIG
    event_manager
    state_manager
    schedule_manager
    service_manager
    websocket_manager
    rf_modem
    radio_manager
    socket_interface_manager
    startup()
    shutdown()
  }
  class SM["ServiceManager (SM)"] {
    frame_dispatcher
    start_modem()
    stop_modem()
    start_radio_manager()
  }
  class RF {
    demodulator
    modulator
    transmit(mode, frames)
  }
  class DISPATCHER {
    process_data(frame, snr)
  }
  class RadioManager {
    radio
    set_ptt(state)
  }
  class RadioBackend {
    <<interface>>
    connect()
    get_parameters()
    set_ptt(state)
  }
  AppContext *-- CONFIG
  AppContext *-- EventManager
  AppContext *-- StateManager
  AppContext *-- ScheduleManager
  AppContext *-- SM
  AppContext *-- WebsocketManager
  AppContext *-- PSKReporter
  AppContext *-- StationKeyStore
  AppContext o-- SocketInterfaceHandler
  SM ..> RF : creates
  SM ..> DISPATCHER : creates
  SM ..> RadioManager : creates
  RF *-- Demodulator
  RF *-- Modulator
  DISPATCHER ..> FrameHandler : dispatches to
  RadioManager o-- RadioBackend
  RadioBackend <|.. rigctld_radio
  RadioBackend <|.. flrig_radio
  RadioBackend <|.. serial_ptt_radio
  RadioBackend <|.. rigdummy_radio
```

The four radio backends are each a class named `radio` in their own module (`rigctld.py`, `flrig.py`,
`serial_ptt.py`, `rigdummy.py`). They share no base class: `RadioBackend` stands for the methods
`RadioManager` calls on them.

## Protocol classes

Transmissions start as `TxCommand` subclasses, called from the API, the scheduler or the socket interface.
Received frames go through the dispatcher to a `FrameHandler` subclass by frame type. Both feed the classes
that keep protocol state: ARQ sessions, P2P connections and NORM transmissions.

```mermaid
classDiagram
  direction TB
  class TxCommand {
    <<abstract>>
    set_params_from_api(params)
    build_frame()
    run()
  }
  TxCommand <|-- CQCommand
  TxCommand <|-- PingCommand
  TxCommand <|-- BeaconCommand
  TxCommand <|-- TestCommand
  TxCommand <|-- TransmitSine
  TxCommand <|-- ARQRawCommand
  TxCommand <|-- SendMessageCommand
  TxCommand <|-- P2PConnectionCommand
  TxCommand <|-- Norm
  class FrameHandler {
    <<abstract>>
    is_frame_for_me()
    follow_protocol()
  }
  FrameHandler <|-- CQFrameHandler
  FrameHandler <|-- PingFrameHandler
  FrameHandler <|-- BeaconFrameHandler
  FrameHandler <|-- ARQFrameHandler
  FrameHandler <|-- P2PConnectionFrameHandler
  FrameHandler <|-- NORMFrameHandler
  class ARQSession {
    speed_level
    fsk
    transmit_frame(frame, mode)
  }
  ARQSession <|-- ARQSessionISS
  ARQSession <|-- ARQSessionIRS
  class P2PConnection {
    state: States
    connect()
    disconnect()
  }
  class NormTransmission
  NormTransmission <|-- NormTransmissionISS
  NormTransmission <|-- NormTransmissionIRS
  ARQRawCommand ..> ARQSessionISS : starts
  SendMessageCommand ..> ARQSessionISS : starts
  P2PConnectionCommand ..> P2PConnection : opens
  Norm ..> NormTransmissionISS : starts
  ARQFrameHandler ..> ARQSessionIRS : creates
  P2PConnectionFrameHandler ..> P2PConnection : creates
  NORMFrameHandler ..> NormTransmissionIRS : creates
  P2PConnection ..> ARQSessionISS : large data
  ARQSession --> ARQDataTypeHandler : prepare, dispatch
  ARQSession --> DataFrameFactory : builds frames
```

[protocol-overview.md](protocol-overview.md) shows how these classes talk to their counterparts on the
other station.

## Message database

The SQLAlchemy models live in [message_system_db_model.py](../message_system_db_model.py). Each
`DatabaseManager*` class (messages, attachments, beacons, stations, broadcasts, encryption) wraps one area
of this schema; the configuration and pending encrypted messages have tables of their own, not shown.

```mermaid
erDiagram
  STATION ||--o{ P2P_MESSAGE : "origin / destination / via"
  STATION ||--o{ BEACON : sends
  STATION ||--o{ BROADCAST_MESSAGE : originates
  P2P_MESSAGE ||--o{ MESSAGE_ATTACHMENT : has
  ATTACHMENT ||--o{ MESSAGE_ATTACHMENT : "used in"
  STATUS ||--o{ P2P_MESSAGE : "status of"
  STATUS ||--o{ BROADCAST_MESSAGE : "status of"
  STATION {
    string callsign PK
    json location
    json info
  }
  P2P_MESSAGE {
    string id PK
    string origin_callsign FK
    string destination_callsign FK
    string via_callsign FK
    int status_id FK
    string direction
  }
  ATTACHMENT {
    int id PK
    string name
    string hash_sha512
  }
  MESSAGE_ATTACHMENT {
    string message_id FK
    int attachment_id FK
  }
  BEACON {
    int id PK
    string callsign FK
    int snr
  }
  BROADCAST_MESSAGE {
    string id PK
    string origin FK
    string domain
    int status_id FK
    json payload_data
  }
  STATUS {
    int id PK
    string name
  }
```

## Known issues

None of its own: this page summarizes the code and the documents it links.
